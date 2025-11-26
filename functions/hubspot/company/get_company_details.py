import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")

@bp.route(route="hubspot/companies/{companyId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_company(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-company...")

    try:
        company_id = req.route_params.get("companyId")
        if not company_id:
            return func.HttpResponse(
                json.dumps({"error": "companyId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Define company properties ---
        properties = [
            "id",
            "name",
            "country",
            "phone",
            "website",
            "address",
            "state",
            "city",
            "zip",
            "hs_logo_url"
        ]

        # --- Build URL ---
        base_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        query_string = "&".join([f"properties={p}" for p in properties])
        company_url = f"{base_url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Fetch company details ---
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

        # --- Build final response ---
        final_response = {
            "companyId": company_data.get("id"),
            "name": props.get("name"),
            "country": props.get("country"),
            "phone": props.get("phone"),
            "website": props.get("website"),
            "address": props.get("address"),
            "state": props.get("state"),
            "city": props.get("city"),
            "zip": props.get("zip"),
            "logoUrl": props.get("hs_logo_url")
        }

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
