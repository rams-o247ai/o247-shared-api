import json
import logging
import os
import uuid
from datetime import datetime

import azure.functions as func
from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient
import psycopg2

bp = func.Blueprint()

CONTAINER_NAME = "callreports"
TABLE_NAME = "CallReportTable"
PARTITION_KEY = "transactions"

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

# -------------------------------
# Helper: Get Table & Blob clients
# -------------------------------
def get_clients():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING not set")
    
    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    try:
        container_client.create_container()
    except Exception:
        pass

    table_service_client = TableServiceClient.from_connection_string(conn_str)
    table_client = table_service_client.get_table_client(TABLE_NAME)
    try:
        table_client.create_table()
    except Exception:
        pass

    return container_client, table_client


# -------------------------------
# CREATE / UPLOAD
# -------------------------------
@bp.route(route="call", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def upload_call(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()
        transaction_id = data.get("transaction_id") or str(uuid.uuid4())
        data_type = data.get("data_type", "default")
        blob_content = data.get("data", {})
        assistant_id = data.get("assistant_id", "unknown")


        container_client, table_client = get_clients()

        # Upload to Blob
        blob_name = f"{transaction_id}_{data_type}.json"
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(json.dumps(blob_content), overwrite=True)
        blob_url = f"{CONTAINER_NAME}/{blob_name}"

        # Upsert Table entity
        entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": f"{transaction_id}_{data_type}",
            "TransactionId": transaction_id,
            "BlobUrl": blob_url,
            "DataType": data_type,
            "AssistantId": assistant_id,
            "clientId": data.get("client_id", "unknown"),
            "InsertedDate": datetime.utcnow().isoformat(),
            "UpdatedDate": datetime.utcnow().isoformat()
        }
        table_client.upsert_entity(entity)

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "transaction_id": transaction_id,
                "data_type": data_type,
                "blob_url": blob_url
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in upload_call: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)


# -------------------------------
# READ / GET specific blob
# -------------------------------
@bp.route(route="call/{transaction_id}/{data_type}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_call(req: func.HttpRequest) -> func.HttpResponse:
    try:
        transaction_id = req.route_params.get("transaction_id")
        data_type = req.route_params.get("data_type")
        if not transaction_id or not data_type:
            return func.HttpResponse(json.dumps({"error": "transaction_id and data_type required"}), status_code=400)

        container_client, table_client = get_clients()

        row_key = f"{transaction_id}_{data_type}"
        entity = table_client.get_entity(PARTITION_KEY, row_key)

        blob_client = container_client.get_blob_client(f"{row_key}.json")
        blob_content = blob_client.download_blob().readall()
        call_data = json.loads(blob_content)

        return func.HttpResponse(
            json.dumps({
                "transaction_id": transaction_id,
                "data_type": data_type,
                "data": call_data,
                "blob_url": entity.get("BlobUrl")
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_call: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)


# -------------------------------
# READ ALL BLOBS for a transaction
# -------------------------------
@bp.route(route="call/{transaction_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_all_blobs(req: func.HttpRequest) -> func.HttpResponse:
    try:
        transaction_id = req.route_params.get("transaction_id")
        if not transaction_id:
            return func.HttpResponse(json.dumps({"error": "transaction_id required"}), status_code=400)

        container_client, table_client = get_clients()
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and TransactionId eq '{transaction_id}'"
        )

        result = []
        for e in entities:
            blob_client = container_client.get_blob_client(f"{e['RowKey']}.json")
            blob_content = blob_client.download_blob().readall()
            result.append({
                "data_type": e["DataType"],
                "blob_url": e["BlobUrl"],
                "data": json.loads(blob_content)
            })

        return func.HttpResponse(
            json.dumps({"transaction_id": transaction_id, "files": result}),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_all_blobs: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)


# -------------------------------
# UPDATE (overwrites same blob)
# -------------------------------
@bp.route(route="call/{transaction_id}/{data_type}", methods=["PUT"], auth_level=func.AuthLevel.FUNCTION)
def update_call(req: func.HttpRequest) -> func.HttpResponse:
    try:
        transaction_id = req.route_params.get("transaction_id")
        data_type = req.route_params.get("data_type")
        if not transaction_id or not data_type:
            return func.HttpResponse(json.dumps({"error": "transaction_id and data_type required"}), status_code=400)

        data = req.get_json()
        blob_content = data.get("data", {})

        container_client, table_client = get_clients()
        row_key = f"{transaction_id}_{data_type}"

        blob_client = container_client.get_blob_client(f"{row_key}.json")
        blob_client.upload_blob(json.dumps(blob_content), overwrite=True)

        entity = table_client.get_entity(PARTITION_KEY, row_key)
        entity["UpdatedDate"] = datetime.utcnow().isoformat()
        table_client.upsert_entity(entity)

        return func.HttpResponse(
            json.dumps({"status": "updated", "transaction_id": transaction_id, "data_type": data_type}),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in update_call: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)


# -------------------------------
# DELETE (single blob)
# -------------------------------
@bp.route(route="call/{transaction_id}/{data_type}", methods=["DELETE"], auth_level=func.AuthLevel.FUNCTION)
def delete_call(req: func.HttpRequest) -> func.HttpResponse:
    try:
        transaction_id = req.route_params.get("transaction_id")
        data_type = req.route_params.get("data_type")
        if not transaction_id or not data_type:
            return func.HttpResponse(json.dumps({"error": "transaction_id and data_type required"}), status_code=400)

        container_client, table_client = get_clients()
        row_key = f"{transaction_id}_{data_type}"

        table_client.delete_entity(PARTITION_KEY, row_key)
        blob_client = container_client.get_blob_client(f"{row_key}.json")
        blob_client.delete_blob()

        return func.HttpResponse(
            json.dumps({"status": "deleted", "transaction_id": transaction_id, "data_type": data_type}),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in delete_call: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)

@bp.route(route="call/{transaction_id}", methods=["DELETE"], auth_level=func.AuthLevel.FUNCTION)
def delete_all_for_transaction(req: func.HttpRequest) -> func.HttpResponse:
    """
    Deletes ALL blobs and table entries related to a given transaction_id.
    """
    try:
        transaction_id = req.route_params.get("transaction_id")
        if not transaction_id:
            return func.HttpResponse(json.dumps({"error": "transaction_id required"}), status_code=400)

        container_client, table_client = get_clients()

        # Query all entities with this transaction_id
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and TransactionId eq '{transaction_id}'"
        )

        deleted = []
        for entity in entities:
            row_key = entity["RowKey"]

            # Delete from table
            try:
                table_client.delete_entity(PARTITION_KEY, row_key)
            except Exception as te:
                logging.warning(f"⚠️ Failed to delete table entity {row_key}: {te}")

            # Delete from blob
            try:
                blob_client = container_client.get_blob_client(f"{row_key}.json")
                blob_client.delete_blob()
            except Exception as be:
                logging.warning(f"⚠️ Failed to delete blob {row_key}.json: {be}")

            deleted.append(row_key)

        return func.HttpResponse(
            json.dumps({
                "status": "deleted_all",
                "transaction_id": transaction_id,
                "deleted_items": deleted
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in delete_all_for_transaction: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)

    
# -------------------------------
# READ ALL TRANSACTIONS by data_type
# -------------------------------
@bp.route(route="calls/{data_type}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_transactions_by_data_type(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data_type = req.route_params.get("data_type")
        if not data_type:
            return func.HttpResponse(json.dumps({"error": "data_type required"}), status_code=400)

        container_client, table_client = get_clients()

        # Query all entities with this data_type
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and DataType eq '{data_type}'"
        )

        result = []
        for e in entities:
            blob_client = container_client.get_blob_client(f"{e['RowKey']}.json")
            blob_content = blob_client.download_blob().readall()
            result.append({
                "transaction_id": e["TransactionId"],
                "blob_url": e["BlobUrl"],
                "data": json.loads(blob_content)
            })

        return func.HttpResponse(
            json.dumps({
                "data_type": data_type,
                "transactions": result
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_transactions_by_data_type: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)
    
@bp.route(route="calls/assistant/{assistantId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_reports_by_assistant(req: func.HttpRequest) -> func.HttpResponse:
    try:
        assistant_id = req.route_params.get("assistantId")
        print("AssistantId: ",assistant_id)
        if not assistant_id:
            return func.HttpResponse(json.dumps({"error": "assistantId required"}), status_code=400)

        container_client, table_client = get_clients()

        # Query all entities with this assistant_id
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and AssistantId eq '{assistant_id}'"
        )

        result = []
        for e in entities:
            blob_client = container_client.get_blob_client(f"{e['RowKey']}.json")
            blob_content = blob_client.download_blob().readall()
            result.append({
                "transaction_id": e["TransactionId"],
                "data_type": e["DataType"],
                "blob_url": e["BlobUrl"],
                "data": json.loads(blob_content)
            })

        return func.HttpResponse(
            json.dumps({
                "assistantId": assistant_id,
                "reports": result
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_reports_by_assistant: {str(e)}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)

@bp.route(route="calls/clients/{clientId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_reports_by_client(req: func.HttpRequest) -> func.HttpResponse:
    try:
        client_id = req.route_params.get("clientId")
        logging.info(f"ClientId: {client_id}")
        if not client_id:
            return func.HttpResponse(
                json.dumps({"error": "clientId required"}),
                status_code=400,
                mimetype="application/json"
            )

        # Get Azure clients
        container_client, table_client = get_clients()

        # Query all entities with this client_id
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and ClientId eq '{client_id}'"
        )

        result = []
        for e in entities:
            blob_client = container_client.get_blob_client(f"{e['RowKey']}.json")
            blob_content = blob_client.download_blob().readall()
            result.append({
                "transaction_id": e["TransactionId"],
                "data_type": e["DataType"],
                "blob_url": e["BlobUrl"],
                "data": json.loads(blob_content)
            })

        return func.HttpResponse(
            json.dumps({
                "clientId": client_id,
                "reports": result
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_reports_by_client: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )

@bp.route(route="calls/business/{business_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_reports_by_business(req: func.HttpRequest) -> func.HttpResponse:
    conn = None
    cursor = None
    try:
        business_id = req.route_params.get("business_id")
        logging.info(f"BusinessId: {business_id}")
        if not business_id:
            return func.HttpResponse(
                json.dumps({"error": "business_id required"}),
                status_code=400,
                mimetype="application/json"
            )

        # Connect to PostgreSQL
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Get client_id from business_client_map
        cursor.execute(
            """
            SELECT client_id
            FROM app.business_client_map
            WHERE business_id = %s
            """,
            (business_id,)
        )
        row = cursor.fetchone()
        if not row:
            return func.HttpResponse(
                json.dumps({"error": "No client found for this business_id"}),
                status_code=404,
                mimetype="application/json"
            )

        client_id = row[0]

        # Get Azure clients
        container_client, table_client = get_clients()

        # Query all entities with this client_id
        entities = table_client.query_entities(
            f"PartitionKey eq '{PARTITION_KEY}' and ClientId eq '{client_id}'"
        )

        result = []
        for e in entities:
            blob_client = container_client.get_blob_client(f"{e['RowKey']}.json")
            blob_content = blob_client.download_blob().readall()
            result.append({
                "transaction_id": e["TransactionId"],
                "data_type": e["DataType"],
                "blob_url": e["BlobUrl"],
                "data": json.loads(blob_content)
            })

        return func.HttpResponse(
            json.dumps({
                "businessId": business_id,
                "clientId": client_id,
                "reports": result
            }),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get_reports_by_business: {str(e)}")
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


