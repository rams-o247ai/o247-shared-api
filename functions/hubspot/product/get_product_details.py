import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")  # Private App Token

@bp.route(route="hubspot/products/{productId}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_hubspot_product(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-hubspot-product...")

    try:
        product_id = req.route_params.get("productId")
        if not product_id:
            return func.HttpResponse(
                json.dumps({"error": "productId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        # --- Define product properties ---
        properties = [
            "hs_object_id",
            "name",
            "hs_images",
            "description",
            "hs_sku",
            "price",
            "recurringbillingfrequency",
            "assistant_role"
        ]

        # --- Build URL ---
        base_url = f"https://api.hubapi.com/crm/v3/objects/products/{product_id}"
        query_string = "&".join([f"properties={p}" for p in properties])
        product_url = f"{base_url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # --- Fetch product details ---
        response = requests.get(product_url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.error(f"HubSpot product fetch failed: {response.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch product from HubSpot"}),
                status_code=500,
                mimetype="application/json",
            )

        product_data = response.json()
        props = product_data.get("properties", {})

        # --- Build final response ---
        final_response = {
            "productId": product_data.get("id"),
            "productName": props.get("name"),
            "imageUrl": props.get("hs_images"),
            "description": props.get("description"),
            "agentId": props.get("hs_sku"),
            "currency": "USD",  # default
            "price": props.get("price"),
            "billingFrequency": props.get("recurringbillingfrequency"),
            "assistantRole": props.get("assistant_role"),
            "createdDate": props.get("createdate")
        }

        return func.HttpResponse(
            json.dumps(final_response),
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
