import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LOCATION_ID = os.getenv("LOCATION_ID")

HEADERS = {
    "Accept": "application/json",
    "Authorization": PRIVATE_KEY,
    "Content-Type": "application/json",
    "Version": "2021-07-28"
}


@bp.function_name(name="get-product-details")
@bp.route(route="get-product-details", methods=["POST"])
def get_product_details(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("get-product-details function processed a request.")

    try:
        body = req.get_json()
        product_id = body.get("productId")

        if not product_id:
            return func.HttpResponse(
                json.dumps({"error": "Missing productId in request body"}),
                status_code=400,
                mimetype="application/json"
            )

        # --- 1. Fetch Product Details ---
        product_url = f"https://services.leadconnectorhq.com/products/{product_id}?locationId={LOCATION_ID}"
        product_res = requests.get(product_url, headers=HEADERS)
        print(product_res.text)
        if product_res.status_code != 200:
            return func.HttpResponse(
                json.dumps({"error": f"Failed to fetch product details", "status": product_res.status_code}),
                status_code=product_res.status_code,
                mimetype="application/json"
            )

        product_data = product_res.json()

        # --- 2. Fetch Price Details ---
        price_url = f"https://services.leadconnectorhq.com/products/{product_id}/price?locationId={LOCATION_ID}"
        price_res = requests.get(price_url, headers=HEADERS)

        if price_res.status_code != 200:
            return func.HttpResponse(
                json.dumps({"error": f"Failed to fetch price details", "status": price_res.status_code}),
                status_code=price_res.status_code,
                mimetype="application/json"
            )

        price_data = price_res.json().get("prices",[{}])[0]  # Assuming we want the first price
        
        # --- 3. Combine Both ---
        combined = {
            "productId": product_id,
            "productName": product_data.get("name"),
            "imageUrl": product_data.get("image"),
            "description": product_data.get("description"),
            "agentId": price_data.get("sku"),
            "currency": price_data.get("currency"),
            "amount": price_data.get("amount"),
            "recurring": {
                "interval": price_data.get("recurring", {}).get("interval"),
                "intervalCount": price_data.get("recurring", {}).get("intervalCount")
            }
        }

        return func.HttpResponse(
            json.dumps(combined, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error in get-product-details: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error", "details": str(e)}),
            status_code=500,
            mimetype="application/json"
        )
