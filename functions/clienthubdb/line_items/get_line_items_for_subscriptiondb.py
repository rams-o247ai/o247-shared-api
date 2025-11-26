import os
import json
import psycopg2
import logging
import azure.functions as func

bp = func.Blueprint()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}

@bp.route(route="db/subscriptions/{subscriptionId}/line-items", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_line_items(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for subscription line items...")

    subscription_id = req.route_params.get("subscriptionId")
    if not subscription_id:
        return func.HttpResponse("Missing subscriptionId in URL", status_code=400)

    try:
        subscription_id_num = int(subscription_id)
    except ValueError:
        return func.HttpResponse("subscriptionId must be numeric", status_code=400)

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        sql = """
            SELECT
                product_id       AS "productId",
                product_name     AS "productName",
                image_url        AS "imageUrl",
                description,
                agent_id         AS "agentId",
                price::text,
                billing_frequency AS "billingFrequency",
                created_at       AS "createdDate",
                assistant_role   AS "assistantRole",
                line_item_id     AS "lineItemId"
            FROM app.subscription_line_items
            WHERE subscription_id = %s
            ORDER BY created_at ASC;
        """
        cur.execute(sql, (subscription_id_num,))
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]

        data = []
        for r in rows:
            item = dict(zip(cols, r))
            # Convert datetime to ISO 8601 string if possible
            if hasattr(item["createdDate"], "isoformat"):
                item["createdDate"] = item["createdDate"].isoformat()
            data.append(item)

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error fetching line items: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)
    finally:
        if cur: cur.close()
        if conn: conn.close()
