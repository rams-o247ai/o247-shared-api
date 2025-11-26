import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/contacts/{contactId}/line-items", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_contact_line_items(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-contact-line-items...")

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

        # --- Step 1: Fetch contact's associated company ID ---
        contact_url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}?properties=associatedcompanyid"
        contact_resp = requests.get(contact_url, headers=headers, timeout=30)
        contact_data = contact_resp.json()
        contact_props = contact_data.get("properties", {})
        user_company_id = contact_props.get("associatedcompanyid")

        if not user_company_id:
            return func.HttpResponse(
                json.dumps({"error": "Contact has no associated company"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Step 2: Fetch all child companies recursively ---
        company_ids = [user_company_id]
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

        print(f"Collected company IDs: {company_ids}")

        # --- Step 3: Fetch subscriptions for all companies ---
        subscription_ids = []
        subscription_company_map = {}
        for cid in company_ids:
            sub_url = f"https://api.hubapi.com/crm/v3/objects/companies/{cid}/associations/subscriptions?limit=100"
            sub_resp = requests.get(sub_url, headers=headers, timeout=30)
            subscriptions = sub_resp.json().get("results", [])
            for sub in subscriptions:
                sub_id = sub.get("id")
                if sub_id:
                    subscription_ids.append(sub_id)
                    subscription_company_map[sub_id] = cid

        # --- Step 4: Fetch line items for each subscription ---
        line_item_ids = []
        if subscription_ids:
            li_url = "https://api.hubapi.com/crm/v4/associations/subscriptions/line_items/batch/read"
            li_payload = {
                "inputs": [{"id": sid} for sid in subscription_ids]
            }

            # Request to batch fetch line item associations
            li_resp = requests.post(li_url, headers=headers, json=li_payload, timeout=30).json()
            print(f"Line items response: {li_resp}")

            # Extract line item IDs from the response
            for item in li_resp.get("results", []):
                for to in item.get("to", []):
                    if any(a.get("label") == "Current Line Item" for a in to.get("associationTypes", [])):
                        line_item_ids.append(to["toObjectId"])

        print(f"Collected line item IDs: {line_item_ids}")

        # --- Step 5: Fetch line item details ---
        products = []
        if line_item_ids:
            batch_url = "https://api.hubspot.com/crm/v3/objects/line_items/batch/read"
            payload = {
                "idProperty": "hs_object_id",
                "properties": [
                    "hs_object_id",
                    "name",
                    "hs_images",
                    "description",
                    "hs_sku",
                    "price",
                    "recurringbillingfrequency",
                    "createdate",
                    "assistant_role",
                    "hs_product_id"
                ],
                "inputs": [{"id": lid} for lid in line_item_ids]
            }
            batch_resp = requests.post(batch_url, headers=headers, json=payload, timeout=30)
            results = batch_resp.json().get("results", [])

            for item in results:
                props = item.get("properties", {})
                products.append({
                    "productId": props.get("hs_product_id"),
                    "productName": props.get("name"),
                    "imageUrl": props.get("hs_images"),
                    "description": props.get("description"),
                    "agentId": props.get("hs_sku"),
                    "price": props.get("price"),
                    "billingFrequency": props.get("recurringbillingfrequency"),
                    "createdDate": props.get("createdate"),
                    "assistantRole": props.get("assistant_role"),
                    "lineItemId": item.get("id"),
                })

        # --- Step 6: Fetch product details for unique productIds ---
        unique_product_ids = set()
        for item in products:
            product_id = item.get("productId")
            if product_id:
                unique_product_ids.add(product_id)

        # Fetch product details for all unique productIds
        if unique_product_ids:
            product_batch_url = "https://api.hubspot.com/crm/v3/objects/products/batch/read"
            product_payload = {
                "inputs": [{"id": pid} for pid in unique_product_ids],
                "properties": ["hs_sku", "name", "description", "hs_images"]
            }
            product_resp = requests.post(product_batch_url, headers=headers, json=product_payload, timeout=30).json()

            # Map the product SKU to the productId
            product_sku_map = {}
            for product in product_resp.get("results", []):
                product_props = product.get("properties", {})
                product_sku_map[product.get("id")] = product_props.get("hs_sku")

        # --- Step 7: Replace agentId with the SKU from product details ---
        final_line_items = []
        for item in products:
            product_id = item.get("productId")
            agent_id = item.get("agentId")

            # If productId exists, replace agentId with SKU from the product details
            if product_id and product_sku_map.get(product_id):
                agent_id = product_sku_map[product_id]  # Replace with product SKU

            final_line_items.append({
                "productId": product_id,
                "productName": item.get("productName"),
                "imageUrl": item.get("imageUrl"),
                "description": item.get("description"),
                "agentId": agent_id,
                "price": item.get("price"),
                "billingFrequency": item.get("billingFrequency"),
                "createdDate": item.get("createdDate"),
                "assistantRole": item.get("assistantRole"),
                "lineItemId": item.get("lineItemId"),
            })

        # --- Step 8: Build final response ---
        return func.HttpResponse(
            json.dumps(final_line_items),
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
