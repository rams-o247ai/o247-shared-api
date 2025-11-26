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

@bp.route(route="get-company-details", auth_level=func.AuthLevel.FUNCTION)
def get_business_details(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-business-details...")

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
                json.dumps({"error": "Failed to fetch business details"}),
                status_code=500,
                mimetype="application/json",
            )

        api_response = response.json()
        business = api_response.get("business", {})

        # Extract required fields
        custom_fields = {cf.get("key"): cf.get("valueString") for cf in business.get("customFields", [])}

        result = {
            "id": business.get("id"),
            "name": business.get("name"),
            "phone": business.get("phone"),
            "website": business.get("website"),
            "address": business.get("address"),
            "state": business.get("state"),
            "city": business.get("city"),
            "zip": business.get("postalCode"),
            "country": business.get("country"),
            "parentCompanyName": custom_fields.get("parent_company_name"),
            "parentCompanyId": custom_fields.get("parent_company_id")
        }

        return func.HttpResponse(
            json.dumps({"success": True, "business": result}),
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
