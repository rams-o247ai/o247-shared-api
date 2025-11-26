import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function Blueprint ---
bp = func.Blueprint()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LOCATION_ID = os.getenv("LOCATION_ID")

HEADERS = {
    'Accept': 'application/json',
    'Authorization': PRIVATE_KEY,
    'Content-Type': 'application/json',
    'Version': '2021-07-28'
}

BUSINESSES_API_URL = "https://services.leadconnectorhq.com/businesses/"

@bp.route(route="get-account-details", auth_level=func.AuthLevel.FUNCTION)
def get_account_details(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-account-details...")

    try:
        # Parse request
        raw_body = req.get_body().decode("utf-8")
        incoming = json.loads(raw_body)
        business_id = incoming.get("businessId")
        
        if not business_id:
            return func.HttpResponse(
                json.dumps({"error": "businessId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # Call the external API
        response = requests.get(
            f"{BUSINESSES_API_URL}{business_id}",
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            return func.HttpResponse(
            response.text,
            status_code=response.status_code,
            mimetype="application/json"
         )


        api_response = response.json()
        business = api_response.get("business", {})

        # Extract vapi_private_token from custom fields
        custom_fields = {cf.get("key"): cf.get("valueString") for cf in business.get("customFields", [])}
        vapi_token = custom_fields.get("vapi_private_token")

        result = {
            "vapiPrivateToken": vapi_token
        }

        return func.HttpResponse(
            json.dumps({"success": True, "account": result}),
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
