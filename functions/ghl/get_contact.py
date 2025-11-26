import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LOCATION_ID = os.getenv("LOCATION_ID")

@bp.route(route="get-contact", auth_level=func.AuthLevel.FUNCTION)
def process_email_request(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for process-email...")

    try:
        # Get raw request body
        raw_body = req.get_body().decode("utf-8")
        logging.info(f"Raw request body ({type(raw_body)}): {raw_body!r}")

        # Attempt to parse JSON
        try:
            incoming = json.loads(raw_body)
        except json.JSONDecodeError as e:
            logging.error(f"JSON decode error: {str(e)}")
            return func.HttpResponse(
                json.dumps({"error": f"Invalid JSON body: {str(e)}"}),
                status_code=400,
                mimetype="application/json",
            )

        logging.info(f"Parsed JSON payload: {json.dumps(incoming)}")

        email = incoming.get("email")

        if not email:
            return func.HttpResponse(
                json.dumps({"error": "Email is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # Prepare the request to external API (LeadConnectorHQ)
        api_url = 'https://services.leadconnectorhq.com/contacts/search'
        headers = {
            'Accept': 'application/json',
            'Authorization': PRIVATE_KEY,
            'Content-Type': 'application/json',
            'Version': '2021-07-28'
        }
        
        # Set up the payload with the email
        payload = {
            "locationId": LOCATION_ID,  # Replace with your location ID
            "pageLimit": 10,
            "filters": [
                {
                    "field": "email",
                    "operator": "eq",
                    "value": email
                }
            ]
        }

        # Call the external API
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)

        # Check if the response was successful
        if response.status_code != 200:
            logging.error(f"API call failed with status code {response.status_code}: {response.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch data from the external API"}),
                status_code=500,
                mimetype="application/json",
            )

        # Return the response from the external API
        api_response = response.json()
        logging.info(f"External API response: {json.dumps(api_response)}")

        # Check if contacts are found and return only the first contact if available
        if api_response.get("contacts", []) == []:
            return func.HttpResponse(
                json.dumps({"message": "No account exists with this email ID"}),
                status_code=404,
                mimetype="application/json",
            )

        # Extract the first contact from the contacts array
        first_contact = api_response["contacts"][0]

        # Return only the first contact as the response
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "contact": first_contact
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logging.info(f"Error processing request: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )
