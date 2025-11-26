import json
import logging
import azure.functions as func
import requests
import os
from datetime import datetime

# --- Azure Function Blueprint ---
bp = func.Blueprint()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LOCATION_ID = os.getenv("LOCATION_ID")

HEADERS = {
    "Accept": "application/json",
    "Authorization": PRIVATE_KEY,
    "Content-Type": "application/json",
    "Version": "2021-07-28"
}

SUBSCRIPTIONS_API_URL = "https://services.leadconnectorhq.com/payments/subscriptions"


def format_unix_timestamp(ts):
    try:
        if ts:
            return datetime.utcfromtimestamp(int(ts)).isoformat()
    except Exception:
        return ts
    return None


def fetch_subscription_by_id(subscription_id, alt_id=LOCATION_ID, alt_type="location"):
    """Fetch subscription details for a given subscriptionId."""
    url = f"{SUBSCRIPTIONS_API_URL}/{subscription_id}"
    params = {"altId": alt_id, "altType": alt_type}

    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if response.status_code != 200:
        logging.error(f"Failed to fetch subscription {subscription_id}: {response.text}")
        return None

    data = response.json()
    subscriptionSnapshot = data.get("subscriptionSnapshot", {})
    contactSnapshot = data.get("contactSnapshot", {})
    recurringProduct = data.get("recurringProduct", {})
    productInfo = recurringProduct.get("product", {})
    priceInfo = recurringProduct.get("price", {})
    paymentProvider = data.get("paymentProvider", {})

    latestInvoice = subscriptionSnapshot.get("latest_invoice", {})
    paymentIntent = latestInvoice.get("payment_intent", {})
    cardDetails = paymentIntent.get("payment_method_details", {}).get("card", {})

    result = {
        "subscriptionId": data.get("_id"),
        "status": data.get("status"),
        "startDate": format_unix_timestamp(subscriptionSnapshot.get("start_date")),
        "currentPeriodStart": format_unix_timestamp(subscriptionSnapshot.get("current_period_start")),
        "currentPeriodEnd": format_unix_timestamp(subscriptionSnapshot.get("current_period_end")),
        "totalAmount": data.get("amount"),
        "customer": {
            "contactId": contactSnapshot.get("id"),
            "name": f"{contactSnapshot.get('firstName', '')} {contactSnapshot.get('lastName', '')}".strip(),
            "email": contactSnapshot.get("email"),
            "phone": contactSnapshot.get("phone"),
            "location": f"{contactSnapshot.get('city', '')}, {contactSnapshot.get('state', '')}, {contactSnapshot.get('country', '')}"
        },
        "plan": {
            "productId": productInfo.get("_id"),
            "productName": productInfo.get("name"),
            "price": priceInfo.get("amount", 0),
            "qty": recurringProduct.get("qty", 1),
            "currency": priceInfo.get("currency"),
            "billingInterval": priceInfo.get("recurring", {}).get("interval")
        },
        "payment": {
            "provider": paymentProvider.get("type"),
            "lastPaymentStatus": latestInvoice.get("status"),
            "lastPaymentAmount": latestInvoice.get("amount_paid") / 100 if latestInvoice.get("amount_paid") else None,
    
        }
    }

    return result


@bp.route(route="get-subscription", auth_level=func.AuthLevel.FUNCTION)
def process_subscription_request(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-subscription...")

    try:
        raw_body = req.get_body().decode("utf-8")
        incoming = json.loads(raw_body)
        subscription_id = incoming.get("subscriptionId", "").strip()

        if not subscription_id:
            return func.HttpResponse(
                json.dumps({"error": "subscriptionId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        subscription_data = fetch_subscription_by_id(subscription_id)
        if not subscription_data:
            return func.HttpResponse(
                json.dumps({"error": "Subscription not found or API error"}),
                status_code=404,
                mimetype="application/json",
            )

        return func.HttpResponse(
            json.dumps({"success": True, "subscription": subscription_data}, indent=2),
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
