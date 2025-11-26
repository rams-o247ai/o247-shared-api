import json
import logging
import os
import psycopg2
import requests
from datetime import datetime, timezone
import azure.functions as func
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient
import base64

# ------------------- ENV & CONFIG -------------------
SF_CLIENT_ID = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SECRET = os.environ["SF_CLIENT_SECRET"]
SF_TOKEN_URL = os.getenv("SF_TOKEN_URL", "https://login.salesforce.com/services/oauth2/token")
CONTAINER_NAME = "callreports"


DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}


bp = func.Blueprint()

# ------------------- QUEUE TRIGGER -------------------
@bp.queue_trigger(
    arg_name="msg",
    queue_name="outgoingqueue",
    connection="AZURE_STORAGE_CONNECTION_STRING"
)
def process_outgoing_queue(msg: func.QueueMessage):
    conn = None
    cursor = None

    try:
        # Parse queue message
        message_body = msg.get_body().decode("utf-8")
        payload = json.loads(message_body)
        logging.info("📬 Received message from outgoingqueue:")
        logging.info(json.dumps(payload, indent=4))

        transaction_id = payload.get("transaction_id")
        client_id = payload.get("client_id") or "unknown_client"
        data_type = payload.get("data_type") or "call_report"
        source = payload.get("source") or "unknown_source"

        if not transaction_id:
            logging.error("❌ Missing transaction_id in queue message — skipping.")
            return

        # ------------------- Get Blob Content -------------------
        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        blob_service = BlobServiceClient.from_connection_string(conn_str)
        blob_client = blob_service.get_blob_client(
            container=CONTAINER_NAME,
            blob=f"{transaction_id}_{data_type}.json"
        )

        blob_data = blob_client.download_blob().readall()
        report = json.loads(blob_data)
        logging.info("📄 Retrieved report from Blob Storage")

        # ------------------- Extract needed fields -------------------
        message = report.get("message", {})
        assistant_vars = message.get("assistant", {}).get("variableValues", {})

        # Summary extraction
        if data_type == "call_report":
            summary = message.get("summary", "")
            notes_title = (
                message.get("analysis", {})
                .get("structuredData", {})
                .get("notes_title")
                or "Call Summary"
            )
            opportunity_id = assistant_vars.get("opportunity_id")
        else:  # chat_report
            summary = ""
            # iterate structuredOutputs to find "Call Summary"
            artifact = message.get("session", {}).get("artifact", {})
            structured_outputs = artifact.get("structuredOutputs", {})
            for so in structured_outputs.values():
                if so.get("name") == "Call Summary":
                    summary = so.get("result")
                    break
            print(structured_outputs)
            print("Extracted chat summary:", summary)
            notes_title = "Chat Summary"
            opportunity_id = None  # chats won't have opportunity_id

            

        # ------------------- DB: Update transactions -------------------
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO logs.transactions 
                (transaction_id, client_id, status, status_time, agent_summary, updated_by, payload, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
            ON CONFLICT (transaction_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                status_time = EXCLUDED.status_time,
                agent_summary = EXCLUDED.agent_summary,
                updated_by = EXCLUDED.updated_by,
                payload = EXCLUDED.payload,
                last_updated = CURRENT_TIMESTAMP AT TIME ZONE 'UTC';
        """, (
            transaction_id,
            client_id,
            "COMPLETED",
            datetime.utcnow(),
            summary,
            "outgoing_queue_processor",
            json.dumps({})
        ))
        logging.info(f"✅ Transaction {transaction_id} updated/inserted with status COMPLETED")

        # Log event: received_response_from_agent
        cursor.execute("""
            INSERT INTO logs.transaction_events
                (transaction_id, event, source, event_time, payload)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s)
        """, (transaction_id, "client_notification_received", source, json.dumps({})))
        logging.info(f"📌 Logged event 'received_response_from_agent' for transaction {transaction_id}")

        # ------------------- Salesforce update (calls only) -------------------
        if data_type == "call_report" and opportunity_id and summary:
            try:
                response_text = send_note_to_opportunity(opportunity_id, summary, notes_title)
                logging.info(f"✅ Opportunity {opportunity_id} updated in Salesforce")

                cursor.execute("""
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time, payload)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s)
                """, (transaction_id, "client_notified", source,
                      json.dumps({"opportunity_id": opportunity_id})))
                logging.info(f"📌 Logged event 'updated_opportunity_in_crm' for {transaction_id}")

            except Exception as e:
                logging.error(f"❌ Salesforce update failed for {opportunity_id}: {e}")

        conn.commit()

    except Exception as e:
        logging.error(f"❌ Error processing outgoing queue message: {e}")
        if conn:
            conn.rollback()

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ------------------- Salesforce Token -------------------
def get_access_token():
    resp = requests.post(
        SF_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": SF_CLIENT_ID,
            "client_secret": SF_CLIENT_SECRET,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data["instance_url"]


# ------------------- Salesforce Update -------------------
def send_note_to_opportunity(opportunity_id: str, notes: str, title: str = None) -> str:
    token, base = get_access_token()
    url = f"{base}/services/apexrest/updateOpportunityNotes"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"opportunityId": opportunity_id, "notes": notes}
    if title:
        payload["title"] = title

    r = requests.post(url, json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Salesforce error {r.status_code}: {r.text}")
    return r.text
