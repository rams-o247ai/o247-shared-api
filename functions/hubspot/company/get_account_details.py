import json
import logging
import azure.functions as func
import requests
import os
import psycopg2

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}


@bp.route(route="hubspot/companies/{companyId}/account/{assistantId?}",methods=["GET"],auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_company_account(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-company...")

    try:
        company_id = req.route_params.get("companyId")
        assistant_id = req.route_params.get("assistantId")  # Optional param
        if not company_id:
            return func.HttpResponse(
                json.dumps({"error": "companyId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Step 1: Define HubSpot company properties ---
        properties = [
            "id",
            "vapi_private_token",
            "vapi_public_token",
            "vapi_outbound_phone_number_id"
        ]

        # --- Step 2: Build HubSpot API request ---
        base_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        query_string = "&".join([f"properties={p}" for p in properties])
        company_url = f"{base_url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Step 3: Fetch company details from HubSpot ---
        response = requests.get(company_url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.error(f"HubSpot company fetch failed: {response.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch company from HubSpot"}),
                status_code=500,
                mimetype="application/json",
            )

        company_data = response.json()
        props = company_data.get("properties", {})

        # --- Step 4: Default data from HubSpot ---
        hubspot_outbound_phone_number_id = props.get("vapi_outbound_phone_number_id")
        final_response = {
            "companyId": company_data.get("id"),
            "vapiPrivateToken": props.get("vapi_private_token"),
            "vapiPublicToken": props.get("vapi_public_token"),
            "outboundPhoneNumberId": hubspot_outbound_phone_number_id,
            "source": "hubspot"
        }

        # --- Step 5: If assistantId is provided, check the database first ---
        if assistant_id:
            try:
                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()

                query = """
                    SELECT outbound_phone_number_id
                    FROM app.subscription_line_items
                    WHERE agent_id = %s
                    LIMIT 1;
                """
                cur.execute(query, (assistant_id,))
                result = cur.fetchone()

                cur.close()
                conn.close()

                if result and result[0]:
                    # Found outbound_phone_number_id in DB
                    final_response["outboundPhoneNumberId"] = result[0]
                    final_response["source"] = "database"
                else:
                    # DB value is null, fallback to HubSpot one
                    final_response["outboundPhoneNumberId"] = hubspot_outbound_phone_number_id
                    final_response["source"] = "hubspot"

            except Exception as db_err:
                logging.error(f"Database query failed: {db_err}")
                final_response["dbError"] = str(db_err)

        # --- Step 6: Return final response ---
        return func.HttpResponse(
            json.dumps(final_response),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )
