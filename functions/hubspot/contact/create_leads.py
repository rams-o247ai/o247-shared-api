import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function Blueprint ---
bp = func.Blueprint()

# HubSpot private app token stored in Azure App Settings
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")
hubspot_url = f"{os.getenv('HUBSPOT_URL')}objects/contacts"

@bp.route(route="hubspot/request-access", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_hubspot_lead(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing POST request to create HubSpot lead...")

    try:
        # Parse request body
        try:
            body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({"error": "Invalid JSON body"}),
                status_code=400,
                mimetype="application/json"
            )

        firstname = body.get("firstname")
        lastname = body.get("lastname")
        email = body.get("email")
        phone = body.get("phone")
        company = body.get("company")
        hear_about = body.get("hearAbout")  # custom field on HubSpot
        lead_status = body.get("leadStatus")

        # Validate required fields
        if not email or not firstname:
            return func.HttpResponse(
                json.dumps({"error": "Name and email are required."}),
                status_code=400,
                mimetype="application/json"
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "properties": {
                "email": email,
                "firstname": firstname,
                "lastname": lastname,
                "phone": phone,
                "company": company,
                "hear_about": hear_about,
                "hs_lead_status": lead_status
            }
        }

       

        response = requests.post(hubspot_url, headers=headers, json=payload, timeout=30)

        if response.status_code not in (200, 201):
            logging.error(f"HubSpot contact creation failed: {response.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to create contact in HubSpot", "details": response.text}),
                status_code=response.status_code,
                mimetype="application/json"
            )

        logging.info("✅ HubSpot lead created successfully.")
        hubspot_response = response.json()

        return func.HttpResponse(
            json.dumps({
                "success": True,
                "message": "Lead created successfully in HubSpot",
                "contactId": hubspot_response.get("id"),
            }),
            status_code=201,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"⚠️ Error creating HubSpot lead: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
