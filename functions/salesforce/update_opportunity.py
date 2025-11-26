import os
import json
import logging
import requests
import azure.functions as func

SF_CLIENT_ID     = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SECRET = os.environ["SF_CLIENT_SECRET"]
SF_TOKEN_URL     = os.getenv("SF_TOKEN_URL", "https://login.salesforce.com/services/oauth2/token")

# ------------------- Salesforce Auth -------------------

def get_access_token():
    """Always request a new access token from Salesforce."""
    resp = requests.post(
        SF_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": SF_CLIENT_ID,
            "client_secret": SF_CLIENT_SECRET,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data["instance_url"]

# ------------------- Send Note -------------------

def send_note_to_opportunity(opportunity_id: str, notes: str, title: str = None) -> str:
    token, base = get_access_token()
    url = f"{base}/services/apexrest/updateOpportunityNotes"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {"opportunityId": opportunity_id, "notes": notes}
    if title:
        payload["title"] = title

    r = requests.post(url, json=payload, headers=headers, timeout=30)

    if r.status_code >= 400:
        raise RuntimeError(f"Salesforce error {r.status_code}: {r.text}")
    return r.text

# ------------------- Azure Function -------------------

bp = func.Blueprint()

@bp.route(route="update-opportunity-note", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def update_opportunity_note(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for update-opportunity-note...")

    try:
        body = req.get_json()
        opportunity_id = body.get("opportunityId")
        notes = body.get("summary")
        title = body.get("notes_title")  # optional

        if not opportunity_id or not notes:
            return func.HttpResponse(
                json.dumps({"error": "Missing opportunityId or notes"}),
                status_code=400,
                mimetype="application/json"
            )

        response = send_note_to_opportunity(opportunity_id, notes, title)

        return func.HttpResponse(
            json.dumps({"status": "success", "response": response}),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error sending note to Opportunity: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
