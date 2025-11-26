import json
import logging
import azure.functions as func
import requests
import os

# --- Azure Function ---
bp = func.Blueprint()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LOCATION_ID = os.getenv("LOCATION_ID")

@bp.route(route="get-invoice-details", auth_level=func.AuthLevel.FUNCTION)
def process_invoices_details(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing request for get-invoices...")

    try:
        # Get the invoiceId and location from the query parameters
        invoice_id = req.params.get('invoiceId')

        if not invoice_id:
            return func.HttpResponse(
                json.dumps({"error": "invoiceId is required"}),
                status_code=400,
                mimetype="application/json",
            )
        
       

        # Prepare the request to external API (LeadConnectorHQ) with dynamic invoiceId and location
        api_url = f'https://services.leadconnectorhq.com/invoices/{invoice_id}'  # Use the dynamic invoiceId
        headers = {
            'Accept': 'application/json',
            'Authorization': PRIVATE_KEY,
            'Content-Type': 'application/json',
            'Version': '2021-07-28'
        }

        # Set up the query parameters with the invoiceId and location
        params = {
            "altId": LOCATION_ID,  # Use the location as the altId
            "altType": "location"
        }

        # Call the external API
        response = requests.get(api_url, headers=headers, params=params, timeout=30)

        # Check if the response was successful
        if response.status_code != 200:
            logging.error(f"API call failed with status code {response.status_code}: {response.text}")
            return func.HttpResponse(
                json.dumps({"error": "Failed to fetch data from the external API"}),
                status_code=500,
                mimetype="application/json",
            )

        # Return the response from the external API
        api_response = response.json()
        logging.info(f"External API response: {json.dumps(api_response)}")

        # Check if invoices are found and return only the required fields for the invoice
        if not api_response:
            return func.HttpResponse(
                json.dumps({"message": "No invoice found with the provided invoiceId"}),
                status_code=404,
                mimetype="application/json",
            )

        # Extract the required fields from the API response
        invoice = api_response
        formatted_invoice = {
            "invoiceNumber": invoice.get("invoiceNumber"),
            "invoiceTotal": invoice.get("invoiceTotal"),
            "amountDue": invoice.get("amountDue"),
            "status": invoice.get("status"),
            "currency": invoice.get("currency"),
            "issueDate": invoice.get("issueDate"),
            "dueDate": invoice.get("dueDate"),
            "businessDetails": {
                "name": invoice.get("businessDetails", {}).get("name"),
                "address": invoice.get("businessDetails", {}).get("address", {}).get("addressLine1"),
                "phoneNo": invoice.get("businessDetails", {}).get("phoneNo"),
            },
            "contactDetails": {
                "name": invoice.get("contactDetails", {}).get("name"),
                "email": invoice.get("contactDetails", {}).get("email"),
                "address": invoice.get("contactDetails", {}).get("address", {}).get("addressLine1"),
                "phoneNo": invoice.get("contactDetails", {}).get("phoneNo"),
            },
            "invoiceItems": [
                {
                    "name": item.get("name"),
                    "qty": item.get("qty"),
                    "amount": item.get("amount"),
                    "taxes": item.get("taxes", [])
                }
                for item in invoice.get("invoiceItems", [])
            ],
            "amountPaid": invoice.get("amountPaid"),
            "total": invoice.get("total"),
            "discount": invoice.get("discount", {}).get("value", 0),
            "totalSummary": {
                "subTotal": invoice.get("totalSummary", {}).get("subTotal"),
                "discount": invoice.get("totalSummary", {}).get("discount"),
                "tax": invoice.get("totalSummary", {}).get("tax"),
            },
            "title": invoice.get("title"),
            "invoiceNumberPrefix": invoice.get("invoiceNumberPrefix")
        }

        # Return the formatted invoice data
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "invoice": formatted_invoice
            }),
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
