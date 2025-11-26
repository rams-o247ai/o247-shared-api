import json 
import logging
import azure.functions as func
import requests
import os
from collections import deque

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

INVOICES_API_URL = "https://services.leadconnectorhq.com/invoices/"
BUSINESSES_API_URL = "https://services.leadconnectorhq.com/businesses/"
CONTACTS_SEARCH_API_URL = "https://services.leadconnectorhq.com/contacts/search"


def find_child_businesses(all_businesses, parent_id):
    """Return list of all descendant businesses (child, grandchild, etc.)."""
    children = []
    queue = deque([parent_id])

    while queue:
        current_id = queue.popleft()
        for b in all_businesses:
            for cf in b.get("customFields", []):
                if cf.get("key") == "parent_company_id" and cf.get("valueString") == current_id:
                    children.append(b)
                    queue.append(b["id"])
    return children


def fetch_invoices_for_contact(contact_id):
    """Fetch invoices for a given contactId and return formatted list."""
    params = {
        "altId": LOCATION_ID,
        "altType": "location",
        "contactId": contact_id,
        "limit": 10,
        "offset": 0
    }

    response = requests.get(INVOICES_API_URL, headers=HEADERS, params=params, timeout=30)
    if response.status_code != 200:
        logging.error(f"Failed to fetch invoices for contact {contact_id}")
        return []

    api_response = response.json()
    invoices = api_response.get("invoices", [])
    return [
        {
            "invoiceNumber": inv.get("invoiceNumber"),
            "issueDate": inv.get("issueDate"),
            "invoiceTotal": inv.get("invoiceTotal"),
            "invoiceId": inv.get("_id"),
            "invoiceName": inv.get("name")
        }
        for inv in invoices
    ]


@bp.route(route="get-invoices", auth_level=func.AuthLevel.FUNCTION)
def process_invoices_request(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-invoices...")

    try:
        # --- Parse request (only email is expected) ---
        raw_body = req.get_body().decode("utf-8")
        incoming = json.loads(raw_body)
        email=incoming.get("email", "").strip()

        if not email:
            return func.HttpResponse(
                json.dumps({"error": "email is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Fetch contact info from /contacts/search ---
        search_body = {
            "locationId": LOCATION_ID,
            "pageLimit": 100,
            "filters": [
                {"field": "email", "operator": "eq", "value": email}
            ]
        }

        search_response = requests.post(
            CONTACTS_SEARCH_API_URL,
            headers=HEADERS,
            json=search_body,
            timeout=30
        )
        if search_response.status_code != 200:
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch contact from LeadConnector"}),
                status_code=500,
                mimetype="application/json",
            )

        contacts_data = search_response.json()
        contacts = contacts_data.get("contacts", [])
        if not contacts:
            return func.HttpResponse(
            json.dumps({"error": "No contact found with that email"}),
            status_code=404,
            mimetype="application/json",
        )

        contact = contacts[0]
        contact_id = contact.get("id")
        business_id = contact.get("businessId")
        business_name = contact.get("businessName")
        user_type = contact.get("type")
        logging.info(f"Contact found: ID={contact_id}, BusinessID={business_id}, Type={user_type}")
        # --- If type is empty/null, return empty invoices ---
        if not user_type:
            return func.HttpResponse(
                json.dumps({"success": True, "invoices": []}),
                status_code=200,
                mimetype="application/json",
            )

        # --- Scenario 1: Billing role ---
        if user_type.lower() == "billing":
            invoices_list = fetch_invoices_for_contact(contact_id)

            return func.HttpResponse(
                json.dumps({"success": True, "invoices": invoices_list}),
                status_code=200,
                mimetype="application/json",
            )

        # --- Scenario 2 & 3: Account admin role ---
        elif user_type.lower() == "account_admin":
            response = requests.get(
                BUSINESSES_API_URL,
                headers=HEADERS,
                params={"locationId": LOCATION_ID},
                timeout=30
            )
            if response.status_code != 200:
                return func.HttpResponse(
                    json.dumps({"error": "Failed to fetch businesses"}),
                    status_code=500,
                    mimetype="application/json",
                )

            businesses = response.json().get("businesses", [])
            child_businesses = find_child_businesses(businesses, business_id)

            # Collect only names
            child_names = [b["name"] for b in child_businesses]

            # Append current business name
            if business_name:
                child_names.append(business_name)

            # --- Build contacts search body for billing contacts ---
            filters_or = [
                {"field": "businessName", "operator": "eq", "value": name}
                for name in child_names
            ]

            search_body = {
                "locationId": LOCATION_ID,
                "pageLimit": 50,
                "filters": [
                    {
                        "group": "AND",
                        "filters": [
                            {"group": "OR", "filters": filters_or},
                            {"field": "type", "operator": "eq", "value": "billing"}
                        ]
                    }
                ]
            }

            search_response = requests.post(
                CONTACTS_SEARCH_API_URL,
                headers=HEADERS,
                json=search_body,
                timeout=30
            )
            if search_response.status_code != 200:
                return func.HttpResponse(
                    json.dumps({"error": "Failed to fetch contacts"}),
                    status_code=500,
                    mimetype="application/json",
                )

            contacts_data = search_response.json()
            contact_ids = [c.get("id") for c in contacts_data.get("contacts", [])]

            # --- Fetch invoices for each contact ---
            all_invoices = []
            for cid in contact_ids:
                all_invoices.extend(fetch_invoices_for_contact(cid))

            return func.HttpResponse(
                json.dumps({"success": True, "invoices": all_invoices}),
                status_code=200,
                mimetype="application/json",
            )

        # --- Unsupported type ---
        else:
            return func.HttpResponse(
                json.dumps({"success": True, "invoices": []}),
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
