import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/companies/{companyId}/line-items", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_company_line_items(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-company-line-items...")

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

        # --- Step 1: Batch read subscriptions for the company ---
        batch_sub_url = "https://api.hubapi.com/crm/v4/associations/company/subscriptions/batch/read"
        payload = {"inputs": [{"id": str(company_id)}]}
        sub_assoc_resp = requests.post(batch_sub_url, headers=headers, json=payload, timeout=30).json()

        subscription_ids = []
        for item in sub_assoc_resp.get("results", []):
            for to in item.get("to", []):
                subscription_ids.append(to["toObjectId"])

        logging.info(f"Subscription IDs: {subscription_ids}")

        if not subscription_ids:
            return func.HttpResponse(json.dumps([]), status_code=200, mimetype="application/json")

        # --- Step 2: Batch read line items for all subscriptions ---
        li_batch_url = "https://api.hubapi.com/crm/v4/associations/subscriptions/line_items/batch/read"
        li_payload = {"inputs": [{"id": str(sid)} for sid in subscription_ids]}
        li_resp = requests.post(li_batch_url, headers=headers, json=li_payload, timeout=30).json()

        # --- Step 3: Collect current line items ---
        line_item_ids = []
        for item in li_resp.get("results", []):
            for line_item in item.get("to", []):
                if any(at.get("label") == "Current Line Item" for at in line_item.get("associationTypes", [])):
                    line_item_ids.append(line_item["toObjectId"])

        if not line_item_ids:
            return func.HttpResponse(json.dumps([]), status_code=200, mimetype="application/json")

        # --- Step 4: Batch read line item details ---
        li_detail_batch_url = "https://api.hubspot.com/crm/v3/objects/line_items/batch/read"
        li_detail_payload = {
            "idProperty": "hs_object_id",
            "properties": [
                "hs_product_id", "name", "hs_images", "description", "hs_sku", "price",
                "recurringbillingfrequency", "createdate", "assistant_role"
            ],
            "inputs": [{"id": str(li_id)} for li_id in line_item_ids]
        }

        li_details_resp = requests.post(li_detail_batch_url, headers=headers, json=li_detail_payload, timeout=30).json()

        # --- Step 5: Prepare initial products list ---
        products = []
        for li in li_details_resp.get("results", []):
            props = li.get("properties", {})
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
                "lineItemId": li.get("id"),
            })

        # --- Step 6: Fetch unique product details for SKU replacement ---
        unique_product_ids = set()
        for item in products:
            product_id = item.get("productId")
            if product_id:
                unique_product_ids.add(product_id)

        if unique_product_ids:
            product_batch_url = "https://api.hubspot.com/crm/v3/objects/products/batch/read"
            product_payload = {
                "inputs": [{"id": pid} for pid in unique_product_ids],
                "properties": ["hs_sku"]
            }
            product_resp = requests.post(product_batch_url, headers=headers, json=product_payload, timeout=30).json()

            # Map the product SKU to the productId
            product_sku_map = {}
            for product in product_resp.get("results", []):
                product_props = product.get("properties", {})
                product_sku_map[product.get("id")] = product_props.get("hs_sku")

        # --- Step 7: Replace agentId with product SKU ---
        final_line_items = []
        for item in products:
            product_id = item.get("productId")
            agent_id = item.get("agentId")

            if product_id and product_sku_map.get(product_id):
                agent_id = product_sku_map[product_id]

            item["agentId"] = agent_id
            final_line_items.append(item)

        # --- Step 8: Return final response ---
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
