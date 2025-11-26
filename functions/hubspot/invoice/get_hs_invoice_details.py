import json
import logging
import azure.functions as func
import requests
import os

bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/invoices/{invoiceId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_invoice_details(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-invoice-details...")

    try:
        invoice_id = req.route_params.get("invoiceId")
        if not invoice_id:
            return func.HttpResponse(
                json.dumps({"error": "invoiceId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Step 1: Get invoice details ---
        invoice_url = f"https://api.hubapi.com/crm/v3/objects/invoices/{invoice_id}"
        invoice_params = {
            "properties": ",".join([
                "hs_amount_billed",
                "hs_amount_paid",
                "hs_createdate",
                "hs_currency",
                "hs_due_date",
                "hs_invoice_date",
                "hs_invoice_status",
                "hs_lastmodifieddate",
                "hs_object_id",
                "hs_subtotal",
                "hs_title",
                "hs_pdf_download_link",
                "hs_invoice_latest_contact_firstname",
                "hs_invoice_latest_contact_firstname",
                "hs_invoice_latest_company_name"
            ])
        }
        invoice_resp = requests.get(invoice_url, headers=headers, params=invoice_params, timeout=30).json()
        invoice_props = invoice_resp.get("properties", {})

        # --- Step 2: Get associated line items ---
        assoc_url = f"https://api.hubapi.com/crm/v4/objects/invoices/{invoice_id}/associations/line_items"
        assoc_resp = requests.get(assoc_url, headers=headers, timeout=30).json()

        line_item_ids = []
        for r in assoc_resp.get("results", []):
            # Handle direct "toObjectId"
            if "toObjectId" in r:
                line_item_ids.append(str(r["toObjectId"]))
            # Handle nested "to" array (older association shape)
            elif "to" in r:
                for to in r.get("to", []):
                    if "toObjectId" in to:
                        line_item_ids.append(str(to["toObjectId"]))

        logging.info(f"Line item IDs: {line_item_ids}")

        # --- Step 3: Batch read line items ---
        line_items = []
        if line_item_ids:
            li_batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            li_payload = {
                "inputs": [{"id": lid} for lid in line_item_ids],
                "properties": ["price", "quantity", "hs_tax_amount","name","amount"]
            }
            li_resp = requests.post(li_batch_url, headers=headers, json=li_payload, timeout=30).json()
            print(li_resp)
            for li in li_resp.get("results", []):
                props = li.get("properties", {})
                line_items.append({
                    "lineItemId": li["id"],
                    "price": props.get("price"),
                    "quantity": props.get("quantity"),
                    "tax": props.get("hs_tax_amount"),
                    "name": props.get("name"),
                    "netAmount": props.get("amount"),
                })

        # --- Step 4: Build final response ---
        final_response = {
            
                "invoiceId": invoice_props.get("hs_object_id"),
                "invoiceTotal": invoice_props.get("hs_amount_billed"),
                "amountPaid": invoice_props.get("hs_amount_paid"),
                "currency": invoice_props.get("hs_currency"),
                "issueDate": invoice_props.get("hs_invoice_date"),
                "dueDate": invoice_props.get("hs_due_date"),
                "status": invoice_props.get("hs_invoice_status"),
                "pdfDownloadLink": invoice_props.get("hs_pdf_download_link"),
                "subTotal": invoice_props.get("hs_subtotal"),
                "createdAt": invoice_props.get("hs_createdate"),
                "lastModified": invoice_props.get("hs_lastmodifieddate"),
                "billingContact": (
                    (invoice_props.get("hs_invoice_latest_contact_firstname") or "") + " " +
                    (invoice_props.get("hs_invoice_latest_contact_lastname") or "")
                ).strip(),
                "billingCompany": invoice_props.get("hs_invoice_latest_company_name"),
                

                "lineItems": line_items
            
        }

        return func.HttpResponse(
            json.dumps(final_response, indent=2),
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
