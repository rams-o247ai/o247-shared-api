import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/contacts/{contactId}/subscriptions", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_contact_subscriptions_children(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-contact-subscriptions-children...")

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
        company_names = {}

        # Optionally fetch user's company name
        user_company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{user_company_id}?properties=name"
        resp = requests.get(user_company_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            company_names[user_company_id] = resp.json().get("properties", {}).get("name", "")

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
                    cname = company["properties"].get("name")
                    if cid:
                        company_ids.append(cid)
                        company_names[cid] = cname
                        if int(company["properties"].get("hs_num_child_companies", 0)) > 0:
                            to_check.append(cid)

        # --- Step 3: Batch read subscriptions for all companies ---
        batch_assoc_url = "https://api.hubapi.com/crm/v4/associations/company/subscriptions/batch/read"
        payload = {"inputs": [{"id": cid} for cid in company_ids]}
        batch_resp = requests.post(batch_assoc_url, headers=headers, json=payload, timeout=30).json()

        subscription_company_map = {}
        subscription_ids = []
        for item in batch_resp.get("results", []):
            cid = item["from"]["id"]
            for to in item.get("to", []):
                sid = to["toObjectId"]
                subscription_ids.append(sid)
                subscription_company_map[sid] = cid
        print( subscription_company_map )
        # --- Step 4: Batch read subscription statuses ---
        subscription_status_map = {}
        if subscription_ids:
            sub_batch_url = "https://api.hubapi.com/crm/v3/objects/subscriptions/batch/read"
            sub_payload = {"inputs": [{"id": sid} for sid in subscription_ids], "properties": ["hs_status"]}
            sub_resp = requests.post(sub_batch_url, headers=headers, json=sub_payload, timeout=30).json()
            for sub in sub_resp.get("results", []):
                subscription_status_map[sub["id"]] = sub.get("properties", {}).get("hs_status", "UNKNOWN")
        print( subscription_status_map )
        # --- Step 5: Batch read administrative contacts for all companies ---
        contact_assoc_url = "https://api.hubapi.com/crm/v4/associations/company/contacts/batch/read"
        contact_payload = {"inputs": [{"id": cid} for cid in company_ids]}
        contact_assoc_resp = requests.post(contact_assoc_url, headers=headers, json=contact_payload, timeout=30).json()

        contact_ids = []
        company_to_contacts = {}
        for item in contact_assoc_resp.get("results", []):
            cid = item["from"]["id"]
            cids = [to["toObjectId"] for to in item.get("to", [])]
            company_to_contacts[cid] = cids
            contact_ids.extend(cids)
        print("Company to contach map below:\n")
        print( company_to_contacts )
        # Batch read contact details
        contact_details = {}
        if contact_ids:
            contact_batch_url = "https://api.hubapi.com/crm/v3/objects/contacts/batch/read"
            contact_payload = {"inputs": [{"id": cid} for cid in contact_ids], "properties": ["firstname", "lastname", "hs_role"]}
            contact_batch_resp = requests.post(contact_batch_url, headers=headers, json=contact_payload, timeout=30).json()
            print("COntact batch response below:\n")
            print( contact_batch_resp )
            for contact in contact_batch_resp.get("results", []):
                contact_details[contact["id"]] = contact.get("properties", {})
        print("Contact details below:\n")
        print( contact_details )
        # Map companies to their administrative contact
        company_admin_map = {}
        for cid, cids in company_to_contacts.items():
            admin_name = None
            for c in cids:
                props = contact_details.get(str(c), {})
                role = props.get("hs_role", "")
                if role and role.lower() == "administrative":
                    admin_name = f"{props.get('firstname','')} {props.get('lastname','')}".strip()

                    break
            company_admin_map[cid] = admin_name
        print("Company admin map below:\n")
        print( company_admin_map )
        # --- Step 6: Batch read line items for all subscriptions ---
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

        print( subscription_line_map )
        # --- Step 7: Build final response ---
        final_response = []
        for sid in subscription_ids:
            cid = subscription_company_map.get(sid)
            line_items = subscription_line_map.get(str(sid), [])
            final_response.append({
                "company": company_names.get(cid, cid),
                "administrativeContact": company_admin_map.get(str(cid)),
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
