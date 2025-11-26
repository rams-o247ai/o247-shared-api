import json
import logging
import os
from datetime import datetime

import azure.functions as func
import psycopg2

bp = func.Blueprint()

# ------------------- DB CONFIG -------------------
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}


# ------------------- GET TRANSACTION EVENTS -------------------
@bp.route(route="events/{transaction_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_transaction_events(req: func.HttpRequest) -> func.HttpResponse:
    conn = None
    cursor = None
    try:
        transaction_id = req.route_params.get("transaction_id")
        if not transaction_id:
            return func.HttpResponse(
                json.dumps({"error": "transaction_id required"}),
                status_code=400
            )

        # Connect to Postgres
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Query all events for the transaction
        cursor.execute("""
            SELECT id, transaction_id, event, source, event_time
            FROM logs.transaction_events
            WHERE transaction_id = %s
            ORDER BY event_time ASC
        """, (transaction_id,))
        rows = cursor.fetchall()

        events = []
        for row in rows:
            id_, txn_id, event, source, event_time = row
            events.append({
                "id": id_,
                "transaction_id": str(txn_id),
                "event": event,
                "source": source,
                "event_time": event_time.isoformat() if event_time else None
            })

        return func.HttpResponse(
            json.dumps({"transaction_id": transaction_id, "events": events}),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error fetching transaction events: {str(e)}")
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
