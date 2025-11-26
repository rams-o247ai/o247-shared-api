import json
import logging
import os
import requests
import psycopg2
import azure.functions as func
from azure.storage.blob import BlobServiceClient
import base64

bp = func.Blueprint()

# 🔧 Environment variables
VAPI_URL = "https://api.vapi.ai/call"
VAPI_KEY = os.getenv("VAPI_API_KEY")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID")

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}


# Blob constants
BLOB_CONTAINER_NAME = "callreports"

def get_blob_payload(transaction_id: str):
    """Retrieve CRM payload from Blob storage using transaction_id."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)

    blob_name = f"{transaction_id}_crm.json"
    blob_client = container_client.get_blob_client(blob_name)

    if not blob_client.exists():
        logging.error(f"❌ Blob not found for transaction_id: {transaction_id}")
        return None

    blob_data = blob_client.download_blob().readall()
    return json.loads(blob_data)


@bp.queue_trigger(
    arg_name="msg",
    queue_name="contactsqueue",
    connection="AZURE_STORAGE_CONNECTION_STRING"
)
def process_contacts_queue(msg: func.QueueMessage):
    conn = None
    cursor = None

    try:
        # Decode queue message
        message_body = msg.get_body().decode("utf-8")
        try:
            payload = json.loads(message_body)
            queue_payload = payload.copy()  # keep original for logging
            logging.info("📩 Received message from queue:")
            logging.info(json.dumps(payload, indent=4))
        except json.JSONDecodeError:
            logging.error(f"❌ Invalid JSON in queue: {message_body}")
            return

        # Extract transaction_id
        transaction_id = str(payload.get("transaction_id"))
        if not transaction_id:
            logging.error("❌ transaction_id missing in queue message.")
            return

        # 🟣 NEW STEP: Retrieve actual CRM payload from Blob
        crm_payload = get_blob_payload(transaction_id)
        if not crm_payload:
            logging.error(f"❌ Could not fetch CRM payload for transaction_id: {transaction_id}")
            return
        # Replace payload with actual CRM content
        payload = crm_payload
        logging.info(f"✅ Retrieved CRM payload from blob for transaction_id {transaction_id}")

        # -----------------------------
        # Extract key fields (same as before)
        # -----------------------------
        subject = payload.get("data", {}).get("subject")
        assistant_id = str(payload.get("targetId"))
        opportunity_id = str(payload.get("subject"))

        if not subject or not assistant_id:
            logging.error("Missing required fields: subject or assistantId — skipping VAPI call.")
            return
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        client_id = str(queue_payload.get("client_id"))
        source = get_business_name(cursor, client_id) or "unknown"

        # 📞 Build VAPI payload with transaction ID
        vapi_payload = {
            "name": "Outbound Call from Assistant",
            "assistantId": assistant_id,
            "phoneNumberId": VAPI_PHONE_NUMBER_ID,
            "customer": {"number": f"+1{subject.get('phoneNumber')}"},
            "assistantOverrides": {
                "variableValues": {
                    "subject": subject,
                    "opportunityId": opportunity_id,
                    "transactionId": transaction_id,
                    "source": source,
                    "clientId": client_id,
                    "opportunity_id": opportunity_id
                }
            }
        }

        logging.info("🚀 Sending VAPI call with payload:")
        logging.info(json.dumps(vapi_payload, indent=4))

        # Send the VAPI request
        headers = {
            "Authorization": f"Bearer {VAPI_KEY}",
            "Content-Type": "application/json"
        }
        response = requests.post(VAPI_URL, headers=headers, json=vapi_payload)
        logging.info(f"VAPI response status: {response.status_code}")
        logging.info(f"VAPI response body: {response.text}")

        # Connect to DB only if we have a transaction_id
        if transaction_id:
    

            if response.status_code == 201:
                # ✅ Success: mark as IN_PROGRESS
                logging.info("✅ Call initiated successfully, updating transaction → IN_PROGRESS")
                cursor.execute("""
                    UPDATE logs.transactions
                    SET status = %s,
                        updated_by = %s,
                        status_time = CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
                    WHERE transaction_id = %s;
                """, ("IN_PROGRESS", "queue_listener", transaction_id))

                # --- Log transaction_event: call_initiated ---
                cursor.execute("""
                    INSERT INTO logs.transaction_events 
                        (transaction_id, event, source, event_time, payload)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s);
                """, (transaction_id, "call_initiated", source, json.dumps({})))
                logging.info(f"Logged event 'call_initiated' for transaction {transaction_id}")

            else:
                # ⚠️ Failed to initiate call: mark as FAILED
                logging.warning("⚠️ VAPI call failed, marking transaction → FAILED")
                cursor.execute("""
                    UPDATE logs.transactions
                    SET status = %s,
                        updated_by = %s,
                        status_time = CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
                    WHERE transaction_id = %s;
                """, ("FAILED", "queue_listener", transaction_id))

                # --- Log transaction_events: call_failed ---
                cursor.execute("""
                    INSERT INTO logs.transaction_events 
                        (transaction_id, event, source, event_time, payload)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s);
                """, (transaction_id, "call_failed", source, json.dumps({})))
                logging.info(f"Logged event 'call_failed' for transaction {transaction_id}")

            conn.commit()

    except Exception as e:
        logging.error(f"Error processing queue message: {e}")
        if conn:
            conn.rollback()

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def get_business_name(cursor, client_id: str):
    """
    Given a client_id, fetch the corresponding business_name
    from the business_client_map table.

    Returns:
        str or None: business_name if found, else None
    """
    if not client_id:
        return None

    cursor.execute(
        """
        SELECT business_name
        FROM app.business_client_map
        WHERE client_id = %s
        LIMIT 1
        """,
        (client_id,)
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    return None

