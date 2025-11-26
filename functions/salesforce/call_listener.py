import json
import logging
import os
import uuid
import psycopg2
from datetime import datetime, timezone
import azure.functions as func
from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient
import base64

bp = func.Blueprint()

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

CONTAINER_NAME = "callreports"
TABLE_NAME = "CallReportTable"
PARTITION_KEY = "transactions"


def resolve_transaction_id(cursor, conn, transaction_id, interaction_id, client_id, received_payload_time, initial_status="RECEIVED"):
    """
    Ensure we have a valid transaction_id:
      1) If provided transaction_id exists -> return it
      2) Else try lookup by interaction_id -> return found transaction_id
      3) Else insert a new transaction using initial_status and return new id
    """
    found_id = None

    # 1) If a transaction_id was provided, check it exists
    if transaction_id:
        cursor.execute(
            "SELECT transaction_id FROM logs.transactions WHERE transaction_id = %s",
            (transaction_id,),
        )
        row = cursor.fetchone()
        if row:
            found_id = row[0]

    # 2) If not found yet, try by interaction_id
    if not found_id and interaction_id:
        cursor.execute(
            "SELECT transaction_id FROM logs.transactions WHERE interaction_id = %s",
            (interaction_id,),
        )
        row = cursor.fetchone()
        if row:
            found_id = row[0]

    # 3) If still not found → insert new transaction with provided initial_status
    if not found_id:
        new_txn_id = transaction_id or str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO logs.transactions
                (transaction_id, client_id, status, updated_by, status_time, interaction_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                new_txn_id,
                client_id,
                initial_status,
                "call_listener",
                received_payload_time,
                interaction_id,
            ),
        )
        conn.commit()
        logging.info(f"🆕 Inserted new transaction {new_txn_id} (interaction_id={interaction_id}, status={initial_status})")
        return new_txn_id

    return found_id


@bp.route(route="call/listener", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def call_listener(req: func.HttpRequest) -> func.HttpResponse:
    conn = None
    cursor = None
    transaction_id = None

    try:
        received_payload_time = datetime.utcnow().isoformat()
        body = req.get_json()
        message = body.get("message", {})
        message_type = message.get("type")
        logging.info(f"Message Type: {message_type}")

        # Determine if it’s a call or chat
        is_call = message_type in ["status-update", "end-of-call-report"]

        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        if is_call:
            # ===== CALL HANDLING =====
            call_subtype = None
            if message_type == "status-update":
                call_status = message.get("status")
                if call_status == "in-progress":
                    call_subtype = "in_progress"
                    logging.info("📞 Call In Progress")
                elif call_status == "ended":
                    call_subtype = "ended"
                    logging.info("📞 Call Ended")
            elif message_type == "end-of-call-report":
                call_subtype = "final_report"
                logging.info("📄 Final Report Received")

            # Extract variables
            if call_subtype in ["in_progress", "ended"]:
                assistant_vars = (
                    message.get("call", {})
                    .get("assistantOverrides", {})
                    .get("variableValues", {})
                )
            else:
                assistant_vars = message.get("assistant", {}).get("variableValues", {})

            transaction_id = assistant_vars.get("transactionId")
            assistant_id = message.get("assistant", {}).get("id", "unknown_assistant")

            print("assistant_id", assistant_id)
            client_id, source = get_client_and_business(cursor, assistant_id)
            print("client_id and source",client_id, source)

            interaction_id = message.get("call", {}).get("id")

            if call_subtype == "in_progress":
                initial_status = "IN PROGRESS"
            elif call_subtype == "final_report":
                initial_status = "COMPLETED"
            else:
                initial_status = "IN PROGRESS"

            transaction_id = resolve_transaction_id(
                cursor, conn, transaction_id, interaction_id, client_id, received_payload_time, initial_status
            )

            if call_subtype == "in_progress":
                event_name = "call_in_progress"
            elif call_subtype == "ended":
                event_name = "call_ended"
            elif call_subtype == "final_report":
                event_name = "call_report_received"
            else:
                event_name = "unknown_event"

            cursor.execute(
                """
                INSERT INTO logs.transaction_events
                    (transaction_id, event, source, event_time)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    transaction_id,
                    event_name,
                    source,
                    received_payload_time
                ),
            )
            conn.commit()
            logging.info(f"Inserted '{event_name}' event for {transaction_id}")

            # Final report
            if call_subtype == "final_report":
                conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
                if not conn_str:
                    raise ValueError("AZURE_STORAGE_CONNECTION_STRING not set")

                # Blob upload
                blob_service_client = BlobServiceClient.from_connection_string(conn_str)
                container_client = blob_service_client.get_container_client(CONTAINER_NAME)
                try:
                    container_client.create_container()
                except Exception:
                    pass

                blob_name = f"{transaction_id}_call_report.json"
                blob_client = container_client.get_blob_client(blob_name)
                blob_client.upload_blob(json.dumps(body), overwrite=True)
                blob_url = f"{CONTAINER_NAME}/{blob_name}"
                logging.info(f"Uploaded final report blob: {blob_url}")

                # Table
                table_service_client = TableServiceClient.from_connection_string(conn_str)
                table_client = table_service_client.get_table_client(TABLE_NAME)
                try:
                    table_client.create_table()
                except Exception:
                    pass

                entity = {
                    "PartitionKey": PARTITION_KEY,
                    "RowKey": f"{transaction_id}_call_report",
                    "TransactionId": transaction_id,
                    "BlobUrl": blob_url,
                    "DataType": "call_report",
                    "AssistantId": assistant_id,
                    "ClientId": client_id,
                    "InsertedDate": datetime.utcnow().isoformat(),
                    "UpdatedDate": datetime.utcnow().isoformat(),
                }
                table_client.upsert_entity(entity)

                # Queue
                queue_name = "outgoingqueue"
                queue_client = QueueClient.from_connection_string(conn_str, queue_name)
                try:
                    queue_client.create_queue()
                except Exception:
                    pass

                outgoing_message = {
                    "transaction_id": transaction_id,
                    "source": source,
                    "data_type": "call_report",
                    "event_time": datetime.now(timezone.utc).isoformat(),
                    "client_id": client_id,
                    "status": "call_report_collected",
                }

                message_str = json.dumps(outgoing_message)
                message_b64 = base64.b64encode(message_str.encode("utf-8")).decode("utf-8")
                queue_client.send_message(message_b64)

                # Update transaction + log
                cursor.execute(
                    """
                    UPDATE logs.transactions
                    SET status = %s,
                        updated_by = %s,
                        status_time = CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
                    WHERE transaction_id = %s
                    """,
                    ("COMPLETED", "call_listener", transaction_id),
                )
                conn.commit()

                cursor.execute(
                    """
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time, payload)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC', %s)
                    """,
                    (
                        transaction_id,
                        "client-notification-scheduled",
                        source,
                        json.dumps({}),
                    ),
                )
                conn.commit()

        else:
            # ===== CHAT HANDLING =====
            message_type = message.get("type")
            session = message.get("session", {})
            session_status = session.get("status")
            interaction_id = session.get("id")
            updated_at = session.get("updatedAt")
            conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            assistant_id= message.get("assistant", {}).get("id")
            client_id, source = get_client_and_business(cursor, assistant_id)
            

            transaction_id = None  # Always resolved internally

            if message_type == "session.created" and session_status == "active":
                # Chat started
                transaction_id = resolve_transaction_id(
                    cursor, conn, transaction_id, interaction_id, client_id,
                    datetime.utcnow().isoformat(), initial_status="IN PROGRESS"
                )

                cursor.execute(
                    """
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                    """,
                    (transaction_id, "chat_in_progress", source)
                )
                conn.commit()

            elif message_type == "session.updated" and session_status == "completed":
                # Chat completed
                transaction_id = resolve_transaction_id(
                    cursor, conn, transaction_id, interaction_id, client_id,
                    datetime.utcnow().isoformat(), initial_status="COMPLETED"
                )

                
                
                # chat_ended
                cursor.execute(
                    """
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (transaction_id, "chat_ended", source, updated_at)
                )
                conn.commit()

                cursor.execute(
                    """
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                    """,
                    (transaction_id, "chat_report_received", source)
                )
                conn.commit()

                # Upload chat report to Blob
                if conn_str:
                    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
                    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
                    try:
                        container_client.create_container()
                    except Exception:
                        pass

                    blob_name = f"{transaction_id}_chat_report.json"
                    blob_client = container_client.get_blob_client(blob_name)
                    blob_client.upload_blob(json.dumps(body), overwrite=True)
                    blob_url = f"{CONTAINER_NAME}/{blob_name}"

                    # Table entry
                    table_service_client = TableServiceClient.from_connection_string(conn_str)
                    table_client = table_service_client.get_table_client(TABLE_NAME)
                    try:
                        table_client.create_table()
                    except Exception:
                        pass

                    entity = {
                        "PartitionKey": PARTITION_KEY,
                        "RowKey": f"{transaction_id}_chat_report",
                        "TransactionId": transaction_id,
                        "BlobUrl": blob_url,
                        "DataType": "chat_report",
                        "AssistantId": assistant_id,
                        "ClientId": client_id,
                        "InsertedDate": datetime.utcnow().isoformat(),
                        "UpdatedDate": datetime.utcnow().isoformat(),
                    }
                    table_client.upsert_entity(entity)

                    # Queue message
                    queue_name = "outgoingqueue"
                    queue_client = QueueClient.from_connection_string(conn_str, queue_name)
                    try:
                        queue_client.create_queue()
                    except Exception:
                        pass

                    outgoing_message = {
                        "transaction_id": transaction_id,
                        "source": source,
                        "data_type": "chat_report",
                        "event_time": datetime.now(timezone.utc).isoformat(),
                        "client_id": client_id,
                        "status": "chat_report_collected",
                    }
                    message_str = json.dumps(outgoing_message)
                    message_b64 = base64.b64encode(message_str.encode("utf-8")).decode("utf-8")
                    queue_client.send_message(message_b64)

                # chat_report_received
                cursor.execute(
                    """
                    INSERT INTO logs.transaction_events
                        (transaction_id, event, source, event_time)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                    """,
                    (transaction_id, "client_notification_scheduled", source)
                )
                conn.commit()

        return func.HttpResponse(
            json.dumps(
                {
                    "status": "received",
                    "transaction_id": transaction_id,
                    "data_type": "call_report" if is_call else "chat_report",
                }
            ),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"Error in call_listener: {str(e)}")
        if conn:
            conn.rollback()
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def get_client_and_business(cursor, assistant_id: str):
    """
    Given an assistant_id (agent_id), fetch one business_id from subscription_line_items,
    then fetch the corresponding client_id and business_name from business_client_map.

    Returns:
        tuple: (client_id, business_name)
               If not found, returns ("unknown_client", None)
    """
    if not assistant_id:
        return "unknown_client", None

    # Step 1: Get one business_id linked to this assistant/agent
    cursor.execute(
        """
        SELECT business_id
        FROM app.subscription_line_items
        WHERE agent_id = %s
        LIMIT 1
        """,
        (assistant_id,)
    )
    row = cursor.fetchone()
    if not row:
        print("business_id not found")
        return "unknown_client", None

    business_id = row[0]

    # Step 2: Lookup client_id and business_name from mapping table
    cursor.execute(
        """
        SELECT client_id, business_name
        FROM app.business_client_map
        WHERE business_id = %s
        """,
        (business_id,)
    )
    row = cursor.fetchone()
    if not row:
        print("business_name not found")
        return "unknown_client", None

    client_id, business_name = row
    return client_id, business_name
