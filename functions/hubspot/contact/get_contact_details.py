import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # HubSpot private app token

# RESTful: GET hubspot/contacts/{contactId}
@bp.route(route="hubspot/contacts/{contactId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_contact_by_id(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for GET hubspot/contacts/{contactId}...")

    try:
        contact_id = req.route_params.get("contactId")
        if not contact_id:
            return func.HttpResponse(
                json.dumps({"error": "contactId is required in the URL"}),
                status_code=400,
                mimetype="application/json",
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Define contact properties ---
        contact_properties = [
            "email",
            "firstname",
            "lastname",
            "associatedcompanyid"
        ]

        # --- Step 1: Fetch contact details by ID ---
        props_query = "&".join([f"properties={p}" for p in contact_properties])
        contact_url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}?{props_query}"

        contact_resp = requests.get(contact_url, headers=headers, timeout=30)
        if contact_resp.status_code != 200:
            logging.error(f"HubSpot contact fetch failed: {contact_resp.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch contact from HubSpot"}),
                status_code=contact_resp.status_code,
                mimetype="application/json",
            )

        contact_data = contact_resp.json()
        props = contact_data.get("properties", {})
        default_company_id = props.get("associatedcompanyid")

        # --- Step 2: Fetch associated companies ---
        company_properties = ["name", "hs_object_id"]
        comp_payload = {
            "filters": [
                {
                    "propertyName": "associations.contact",
                    "operator": "EQ",
                    "value": contact_id,
                }
            ],
            "properties": company_properties,
        }

        companies_url = "https://api.hubapi.com/crm/v3/objects/companies/search"
        companies_resp = requests.post(companies_url, headers=headers, json=comp_payload, timeout=30)
        if companies_resp.status_code != 200:
            logging.error(f"HubSpot company search failed: {companies_resp.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch companies from HubSpot"}),
                status_code=companies_resp.status_code,
                mimetype="application/json",
            )

        companies_data = companies_resp.json()
        associated_companies = []

        for comp in companies_data.get("results", []):
            comp_id = comp["id"]
            comp_name = comp["properties"].get("name")

            company_entry = {
                "companyId": comp_id,
                "companyName": comp_name,
            }

            # Mark default company
            if default_company_id and comp_id == default_company_id:
                company_entry["default"] = True

            associated_companies.append(company_entry)

        # --- Final RESTful response (flat) ---
        final_response = {
            "contactId": contact_id,
            "email": props.get("email"),
            "firstName": props.get("firstname"),
            "lastName": props.get("lastname"),
            "associatedCompanies": associated_companies,
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
