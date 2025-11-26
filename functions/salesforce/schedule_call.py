import json
import logging
import os
import psycopg2
import base64
from datetime import datetime
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient
import azure.functions as func

bp = func.Blueprint()

# Database connection parameters for local PostgreSQL
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}


# Azure resources
PARTITION_KEY = "transactions"
BLOB_CONTAINER_NAME = "callreports"
TABLE_NAME = "CallReportTable"

def get_clients():
    """Helper to create blob and table clients"""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
    try:
        container_client.create_container()
    except Exception:
        pass  # already exists

    table_service_client = TableServiceClient.from_connection_string(conn_str)
    table_client = table_service_client.get_table_client(table_name=TABLE_NAME)
    try:
        table_client.create_table()
    except Exception:
        pass  # already exists

    return container_client, table_client


@bp.route(route="queue/contact", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def enqueue_message(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for enqueue-message...")

    conn = None
    cursor = None

    try:
        # Parse incoming payload
        body = req.get_json()
        logging.info(f"Received body: {body}")

        client_id = body.get("client_id")
        assistant_id=body.get("targetId") or "unknown"
        if not client_id:
            return func.HttpResponse(
                json.dumps({"error": "Missing client_id or opportunity_id"}),
                status_code=400,
                mimetype="application/json"
            )

        # Connect to PostgreSQL
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # --- 1️⃣ Insert into transactions table ---
        insert_query = """
            INSERT INTO logs.transactions
                (client_id, status, updated_by, agent_summary)
            VALUES (%s, %s, %s, %s)
            RETURNING transaction_id;
        """
        cursor.execute(insert_query, (
            client_id,
            "RECEIVED",
            "schedule_call_api",
            None
        ))
        transaction_id = cursor.fetchone()[0]
        conn.commit()
        logging.info(f"Inserted transaction with ID: {transaction_id}")

        # --- 2️⃣ Log event → opportunity_received ---
        event_type = "opportunity_received" if "opportunity" in body.get("type", "").lower() else "data_received"
        source = get_business_name(cursor, client_id)or "unknown"


        cursor.execute("""
            INSERT INTO logs.transaction_events (transaction_id, event, source, event_time, payload)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s);
        """, (transaction_id, event_type, source, json.dumps({})))
        conn.commit()

        # --- 3️⃣ Add transaction_id to payload ---
        body["transaction_id"] = str(transaction_id)

        # --- 🟣 4️⃣ Dump to Blob + Table as CRM payload ---
        try:
            container_client, table_client = get_clients()
            data_type = "crm"

            # Upload CRM payload JSON to blob
            blob_name = f"{transaction_id}_{data_type}.json"
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(json.dumps(body), overwrite=True)
            blob_url = blob_client.url

            # Insert record into Azure Table
            entity = {
                "PartitionKey": PARTITION_KEY,
                "RowKey": f"{transaction_id}_{data_type}",
                "TransactionId": str(transaction_id),
                "BlobUrl": blob_url,
                "DataType": data_type,
                "AssistantId": assistant_id,
                "ClientId": client_id,
                "InsertedDate": datetime.utcnow().isoformat(),
                "UpdatedDate": datetime.utcnow().isoformat()
            }
            table_client.upsert_entity(entity)
            logging.info(f"✅ CRM payload dumped to Blob + Table for {transaction_id}")
        except Exception as dump_err:
            logging.error(f"Error dumping CRM payload to blob/table: {dump_err}")

        # --- 5️⃣ Push compact message to Azure Queue ---
        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        queue_client = QueueClient.from_connection_string(conn_str, "contactsqueue")

        queue_message = {
            "client_id": client_id,
            "transaction_id": str(transaction_id),
            "event_name": "opportunity_received",
            "status": "queued"
        }
        message_str = json.dumps(queue_message)
        message_b64 = base64.b64encode(message_str.encode("utf-8")).decode("utf-8")
        queue_client.send_message(message_b64)
        logging.info(f"📬 Message enqueued: {queue_message}")

        # --- 6️⃣ Update main transaction status → QUEUED ---
        update_query = """
            UPDATE logs.transactions
            SET status = %s,
                updated_by = %s,
                status_time = CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
            WHERE transaction_id = %s;
        """
        cursor.execute(update_query, ("QUEUED", "opportunity_api", transaction_id))
        conn.commit()

        # --- 7️⃣ Log event → opportunity_queued ---
        cursor.execute("""
            INSERT INTO logs.transaction_events (transaction_id, event, source, event_time, payload)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s);
        """, (transaction_id, "opportunity_queued", source, json.dumps({})))
        conn.commit()

        # ✅ Response
        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "transaction_id": str(transaction_id),
                "blob_dumped": True,
                "queue_message": queue_message
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        if conn:
            conn.rollback()
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

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

