import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/subscriptions/{subscriptionId}/line-items", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_subscription_line_items(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-subscription-line-items...")

    try:
        subscription_id = req.route_params.get("subscriptionId")
        if not subscription_id:
            return func.HttpResponse(
                json.dumps({"error": "subscriptionId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Step 1: Fetch associated line items for the subscription ---
        assoc_url = f"https://api.hubapi.com/crm/v4/associations/subscriptions/line_items/batch/read"
        payload = {"inputs": [{"id": subscription_id}]}
        assoc_resp = requests.post(assoc_url, headers=headers, json=payload, timeout=30).json()
        print(assoc_resp)
        line_item_ids = []
        for item in assoc_resp.get("results", []):
            for to in item.get("to", []):
                # Only consider current line items
                if any(a.get("label") == "Current Line Item" for a in to.get("associationTypes", [])):
                    line_item_ids.append(to["toObjectId"])
        print(f"Line item IDs: {line_item_ids}")
        if not line_item_ids:
            return func.HttpResponse(json.dumps([]), status_code=200, mimetype="application/json")

        # --- Step 2: Batch read line item properties ---
        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_payload = {
            "inputs": [{"id": str(lid)} for lid in line_item_ids],
            "properties": [
                "hs_product_id", "name", "hs_images", "description",
                "hs_sku", "price", "recurringbillingfrequency", 
                "createdate", "assistant_role"
            ]
        }
        batch_resp = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30).json()

        # --- Step 3: Prepare initial products list ---
        products = []
        for item in batch_resp.get("results", []):
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

        # --- Step 4: Fetch product details for unique productIds ---
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
                "properties": ["hs_sku"]
            }
            product_resp = requests.post(product_batch_url, headers=headers, json=product_payload, timeout=30).json()

            # Map the product SKU to the productId
            product_sku_map = {}
            for product in product_resp.get("results", []):
                product_props = product.get("properties", {})
                product_sku_map[product.get("id")] = product_props.get("hs_sku")

        # --- Step 5: Replace agentId with the SKU from product details ---
        final_line_items = []
        for item in products:
            product_id = item.get("productId")
            agent_id = item.get("agentId")

            if product_id and product_sku_map.get(product_id):
                agent_id = product_sku_map[product_id]

            item["agentId"] = agent_id
            final_line_items.append(item)

        # --- Step 6: Build final response ---
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
