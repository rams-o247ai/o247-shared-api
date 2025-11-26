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
    "password": os.getenv("DB_PASSWORD")
}


@bp.route(route="db/contacts/{contactId}/line-items", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_line_items_by_contact(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for contact line items...")

    contact_id = req.route_params.get("contactId")
    if not contact_id:
        return func.HttpResponse("Missing contactId in URL", status_code=400)

    try:
        contact_id_num = int(contact_id)
    except ValueError:
        return func.HttpResponse("contactId must be numeric", status_code=400)

    # ✅ check for query param ?pending
    is_pending = req.params.get("pending") is not None

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # ✅ dynamically filter based on status
        if is_pending:
            status_filter = "WHERE contact_id = %s AND status in ('pending', 'processing')"
        else:
            status_filter = "WHERE contact_id = %s AND status = 'active'"

        sql = f"""
            SELECT
                product_id        AS "productId",
                product_name      AS "productName",
                image_url         AS "imageUrl",
                description,
                agent_id          AS "agentId",
                price::text,
                billing_frequency AS "billingFrequency",
                created_at        AS "createdDate",
                assistant_role    AS "assistantRole",
                line_item_id      AS "lineItemId",
                fields::text      AS "fields",
                status            AS "status"
            FROM app.subscription_line_items
            {status_filter}
            ORDER BY created_at ASC;
        """
        cur.execute(sql, (contact_id_num,))
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]

        data = []
        for r in rows:
            item = dict(zip(cols, r))
            if hasattr(item["createdDate"], "isoformat"):
                item["createdDate"] = item["createdDate"].isoformat()
            if item.get("fields"):
                try:
                    item["fields"] = json.loads(item["fields"])
                except Exception:
                    item["fields"] = []
            data.append(item)

        return func.HttpResponse(
            json.dumps(data, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error fetching line items by contact: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
