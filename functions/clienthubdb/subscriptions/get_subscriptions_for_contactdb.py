import os
import json
import psycopg2
import logging
import azure.functions as func

bp = func.Blueprint()

# Database connection parameters can also come from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}

@bp.route(route="db/contacts/{contactId}/subscriptions", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_subscriptions(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for contact subscriptions...")

    contact_id = req.route_params.get("contactId")
    if not contact_id:
        return func.HttpResponse("Missing contactId in URL", status_code=400)

    # Cast to integer
    try:
        contact_id_num = int(contact_id)
    except ValueError:
        return func.HttpResponse("contactId must be numeric", status_code=400)

    conn = None
    cur = None
    try:
        # Connect to PostgreSQL
        logging.info(f"Connecting to PostgreSQL at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # Query subscriptions and compute totalProducts
        sql = """
            SELECT
              s.company,
              s.administrative_contact AS "administrativeContact",
              s.status,
              COALESCE(li.cnt, 0) AS "totalProducts",
              s.subscription_id AS "subscriptionID"
            FROM app.subscriptions s
            LEFT JOIN (
              SELECT subscription_id, COUNT(*)::int AS cnt
              FROM app.subscription_line_items
              GROUP BY subscription_id
            ) li ON li.subscription_id = s.subscription_id
            WHERE s.contact_id = %s
            ORDER BY s.subscription_id DESC;
        """
        cur.execute(sql, (contact_id_num,))
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]

        data = [dict(zip(cols, r)) for r in rows]

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error fetching subscriptions: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)
    finally:
        if cur: cur.close()
        if conn: conn.close()
