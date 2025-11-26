import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # HubSpot private app token

@bp.route(route="hubspot/contacts/search", auth_level=func.AuthLevel.FUNCTION)
def search_hubspot_contact(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for search-hubspot-contact...")

    try:
        # --- Parse request body ---
        raw_body = req.get_body().decode("utf-8")
        logging.info(f"Raw request body: {raw_body!r}")

        try:
            incoming = json.loads(raw_body)
        except json.JSONDecodeError as e:
            return func.HttpResponse(
                json.dumps({"error": f"Invalid JSON body: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
            )

        email = incoming.get("email")
        if not email:
            return func.HttpResponse(
                json.dumps({"error": "Email is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Search contact by email ---
        contact_url = "https://api.hubapi.com/crm/v3/objects/contacts/search"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # Added lifecyclestage property
        contact_properties = [
            "email",
            "firstname",
            "lastname",
            "hs_object_id",
            "associatedcompanyid",
            "lifecyclestage",
        ]

        payload = {
            "filterGroups": [
                { 
                    "filters": [

                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }
                    ]
                }
            ],
            "properties": contact_properties,
        }

        contact_resp = requests.post(contact_url, headers=headers, json=payload, timeout=30)
        if contact_resp.status_code != 200:
            logging.error(f"HubSpot contact search failed: {contact_resp.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch contact from HubSpot"}),
                status_code=500,
                mimetype="application/json",
            )

        contact_data = contact_resp.json()
        if not contact_data.get("results"):
            return func.HttpResponse(
                json.dumps({"message": "No contact exists with this email"}),
                status_code=404,
                mimetype="application/json",
            )

        contact = contact_data["results"][0]
        contact_id = contact["id"]
        props = contact["properties"]

        # --- Check lifecycle stage ---
        lifecycle_stage = (props.get("lifecyclestage") or "").lower()
        if lifecycle_stage != "customer":
            return func.HttpResponse(
                json.dumps({"message": "No contact exists with this email"}),
                status_code=404,
                mimetype="application/json",
            )

        default_company_id = props.get("associatedcompanyid")

        # --- Build final response ---
        final_response = {
            "contactId": contact_id,
            "email": props.get("email"),
            "firstName": props.get("firstname"),
            "lastName": props.get("lastname"),
            "defaultCompanyId": default_company_id
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
