import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/companies/{companyId}/subscriptions", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_company_subscriptions(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-company-subscriptions...")

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

        # --- Step 3: Fetch subscriptions for the company ---
        subscription_ids = []
        subscription_company_map = {}

        # Fetch subscriptions associated with the given company
        sub_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}/associations/subscriptions?limit=100"
        sub_resp = requests.get(sub_url, headers=headers, timeout=30)
        subscriptions = sub_resp.json().get("results", [])
        
        for sub in subscriptions:
            sub_id = sub.get("id")
            if sub_id:
                subscription_ids.append(sub_id)
                subscription_company_map[sub_id] = company_id

        logging.info(f"Collected subscription IDs: {subscription_ids}")

        # --- Step 4: Batch read subscription statuses ---
        subscription_status_map = {}
        if subscription_ids:
            sub_batch_url = "https://api.hubapi.com/crm/v3/objects/subscriptions/batch/read"
            sub_payload = {"inputs": [{"id": sid} for sid in subscription_ids], "properties": ["hs_status"]}
            sub_resp = requests.post(sub_batch_url, headers=headers, json=sub_payload, timeout=30).json()
            for sub in sub_resp.get("results", []):
                subscription_status_map[sub["id"]] = sub.get("properties", {}).get("hs_status", "UNKNOWN")
        logging.info(f"Subscription statuses: {subscription_status_map}")

        # --- Step 5: Batch read line items for all subscriptions ---
        li_batch_url = "https://api.hubapi.com/crm/v4/associations/subscriptions/line_items/batch/read"
        li_payload = {"inputs": [{"id": sid} for sid in subscription_ids]}
        li_resp = requests.post(li_batch_url, headers=headers, json=li_payload, timeout=30).json()

        subscription_line_map = {}
        for item in li_resp.get("results", []):
            sid = item["from"]["id"]
            line_items = []
            for to in item.get("to", []):
                for assoc in to.get("associationTypes", []):
                    if assoc.get("label") == "Current Line Item":
                        line_items.append(to)
                        break
            subscription_line_map[sid] = line_items

        logging.info(f"Subscription line items map: {subscription_line_map}")

        # --- Step 6: Build final response ---
        final_response = []
        for sid in subscription_ids:
            cid = subscription_company_map.get(sid)
            line_items = subscription_line_map.get(str(sid), [])
            final_response.append({
                "companyId": cid,
                "status": subscription_status_map.get(str(sid), "UNKNOWN"),
                "totalProducts": len(line_items),
                "subscriptionID": sid
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
