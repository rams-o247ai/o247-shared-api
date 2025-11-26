import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/companies/{companyId}/invoices", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_company_invoices(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-company-invoices...")

    try:
        company_id = req.route_params.get("companyId")
        if not company_id:
            return func.HttpResponse(
                json.dumps({"error": "companyId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Step 1: Fetch all invoices for the given company ---
        invoice_assoc_url = "https://api.hubapi.com/crm/v4/associations/company/invoices/batch/read"
        payload = {"inputs": [{"id": company_id}]}
        invoice_assoc_resp = requests.post(invoice_assoc_url, headers=headers, json=payload, timeout=30).json()

        invoice_ids = []
        for item in invoice_assoc_resp.get("results", []):
            for to in item.get("to", []):
                invoice_ids.append(to["toObjectId"])

        # --- Step 2: Batch read invoice details ---
        invoice_details = {}
        if invoice_ids:
            invoice_batch_url = "https://api.hubapi.com/crm/v3/objects/invoices/batch/read"
            invoice_payload = {
                "inputs": [{"id": iid} for iid in invoice_ids],
                "properties": ["hs_amount_billed", "hs_invoice_date", "hs_invoice_status"]
            }
            invoice_batch_resp = requests.post(invoice_batch_url, headers=headers, json=invoice_payload, timeout=30).json()

            for inv in invoice_batch_resp.get("results", []):
                props = inv.get("properties", {})
                status = props.get("hs_invoice_status")
                if status and status.upper() in ["OPEN", "PAID"]:
                    invoice_details[inv["id"]] = {
                        "amount": props.get("hs_amount_billed"),
                        "billingDate": props.get("hs_invoice_date"),
                        "status": props.get("hs_invoice_status")
                    }

        # --- Step 3: (Optional) Batch read invoice items ---
        invoice_items_assoc_url = "https://api.hubapi.com/crm/v4/associations/invoices/invoice_items/batch/read"
        invoice_items_payload = {"inputs": [{"id": iid} for iid in invoice_ids]}
        invoice_items_resp = requests.post(invoice_items_assoc_url, headers=headers, json=invoice_items_payload, timeout=30).json()

        # --- Step 4: Build final response ---
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
