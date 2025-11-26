import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/contacts/{contactId}/invoices", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_contact_invoices(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-contact-invoices...")

    try:
        contact_id = req.route_params.get("contactId")
        if not contact_id:
            return func.HttpResponse(
                json.dumps({"error": "contactId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Step 1: Fetch contact role and associated company ---
        contact_url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}?properties=hs_role&properties=associatedcompanyid"
        contact_resp = requests.get(contact_url, headers=headers, timeout=30)
        contact_data = contact_resp.json()
        contact_props = contact_data.get("properties", {})
        contact_role = contact_props.get("hs_role", "").lower()
        user_company_id = contact_props.get("associatedcompanyid")

        if contact_role not in ["administrative", "accounting", "billing"]:
            return func.HttpResponse(json.dumps([]), status_code=200, mimetype="application/json")

        if not user_company_id:
            return func.HttpResponse(
                json.dumps({"error": "Contact has no associated company"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Step 2: Fetch all child companies recursively ---
        company_ids = [user_company_id]

        if contact_role == "administrative":
            to_check = [user_company_id]
            while to_check:
                current_id = to_check.pop(0)
                companies_search_url = "https://api.hubapi.com/crm/v3/objects/companies/search"
                payload = {
                    "filterGroups": [
                        {"filters": [{"propertyName": "hs_parent_company_id", "operator": "EQ", "value": current_id}]}
                    ],
                    "properties": ["hs_object_id", "hs_num_child_companies", "name"],
                    "limit": 100
                }
                resp = requests.post(companies_search_url, headers=headers, json=payload, timeout=30)
                if resp.status_code != 200:
                    logging.warning(f"Failed to fetch child companies for {current_id}: {resp.text}")
                    continue
                results = resp.json().get("results", [])
                for company in results:
                    cid = company["properties"].get("hs_object_id")
                    if cid:
                        company_ids.append(cid)
                        if int(company["properties"].get("hs_num_child_companies", 0)) > 0:
                            to_check.append(cid)

        # --- Step 3: Batch read invoices for all companies ---
        invoice_assoc_url = "https://api.hubapi.com/crm/v4/associations/company/invoices/batch/read"
        payload = {"inputs": [{"id": cid} for cid in company_ids]}
        invoice_assoc_resp = requests.post(invoice_assoc_url, headers=headers, json=payload, timeout=30).json()

        invoice_ids = []
        for item in invoice_assoc_resp.get("results", []):
            for to in item.get("to", []):
                invoice_ids.append(to["toObjectId"])

        # --- Step 4: Batch read invoice details ---
        invoice_details = {}
        if invoice_ids:
            invoice_batch_url = "https://api.hubapi.com/crm/v3/objects/invoices/batch/read"
            invoice_payload = {
                "inputs": [{"id": iid} for iid in invoice_ids],
                "properties": ["hs_amount_billed", "hs_invoice_date", "hs_invoice_status"]
            }
            invoice_batch_resp = requests.post(invoice_batch_url, headers=headers, json=invoice_payload, timeout=30).json()
            print(invoice_batch_resp)
            for inv in invoice_batch_resp.get("results", []):
                props = inv.get("properties", {})
                status = props.get("hs_invoice_status")
                if status and status.upper() in ["OPEN", "PAID"]:
                    invoice_details[inv["id"]] = {
                        "amount": props.get("hs_amount_billed"),
                        "billingDate": props.get("hs_invoice_date"),
                        "status": props.get("hs_invoice_status")
                }

        # --- Step 5: (Optional) Batch read invoice items ---
        invoice_items_assoc_url = "https://api.hubapi.com/crm/v4/associations/invoices/invoice_items/batch/read"
        invoice_items_payload = {"inputs": [{"id": iid} for iid in invoice_ids]}
        invoice_items_resp = requests.post(invoice_items_assoc_url, headers=headers, json=invoice_items_payload, timeout=30).json()
        # You can extend final_response with items if needed

        # --- Step 6: Build final response ---
        final_response = []
        for iid, details in invoice_details.items():
            final_response.append({
                "invoiceId": iid,
                "amount": details["amount"],
                "billingDate": details["billingDate"],
                "status": details["status"]
            })

        return func.HttpResponse(
            json.dumps(final_response),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
