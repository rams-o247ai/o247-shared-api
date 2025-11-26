from dotenv import load_dotenv
load_dotenv()

from azure import functions as func

# from functions.chat_completions import bp as chat_bp
from functions.http_trigger import bp as http_bp
# from functions.ghl.get_contact import bp as gt_contact
# from functions.ghl.get_invoices import bp as gt_invoices
# from functions.ghl.get_invoice_details import bp as gt_invoice_details
# from functions.ghl.get_company import bp as gt_company
# from functions.ghl.get_account_details import bp as gt_account_details  
# from functions.ghl.get_subscriptions import bp as gt_subscriptions
# from functions.ghl.get_subscription_details import bp as gt_subscription_details
# from functions.ghl.get_products import bp as gt_products
# from functions.hubspot.contact.get_hs_user_search import bp as hs_contact
# from functions.ghl.get_product_details import bp as gt_product_details
# from functions.hubspot.company.get_company_details import bp as hs_company
# from functions.hubspot.product.get_product_details import bp as hs_product
# from functions.hubspot.contact.get_contact_details import bp as hs_contact_by_id 
# from functions.hubspot.subscriptions.get_subscriptions_for_contact import bp as hs_subscriptions
# from functions.hubspot.line_items.get_line_items_for_contact import bp as hs_products
# from functions.hubspot.line_items.get_line_items_for_subscription import bp as hs_subscription_details
# from functions.hubspot.line_items.get_line_items_for_company import bp as hs_comp_line_items
# from functions.hubspot.invoice.get_invoices_for_contact import bp as hs_contact_invoices
# from functions.hubspot.invoice.get_invoices_for_company import bp as hs_comp_invoices
# from functions.hubspot.company.get_account_details import bp as hs_account_details
# from functions.hubspot.subscriptions.get_subscriptions_for_company import bp as hs_subscriptions_for_company
# from functions.hubspot.invoice.get_hs_invoice_details import bp as hs_invoice_details
# from functions.clienthubdb.product.create_product import bp as db_create_product
# from functions.clienthubdb.line_items.get_line_items_for_contactdb import bp as db_get_line_items_for_contactdb
# from functions.clienthubdb.subscriptions.get_subscriptions_for_contactdb import bp as db_get_subscriptions_for_contactdb
# from functions.clienthubdb.line_items.get_line_items_for_subscriptiondb import bp as db_line_items_for_subscriptiondb

#salesforce and logging functions to be imported here
# from functions.salesforce.schedule_call import bp as add_contact_queue
# from functions.salesforce.update_opportunity import bp as update_opportunity_note
# from functions.salesforce.process_contact_queue import bp as process_contact_queue
# from functions.salesforce.outgoing_queue_listener import bp as outgoing_queue_listener
# from functions.salesforce.call_listener import bp as call_listener_bp
# from functions.salesforce.crud_blob import bp as crud_blob_bp
# from functions.salesforce.crud_transaction_events import bp as crud_transaction_events

# from functions.hubspot.contact.create_leads import bp as hs_create_leads


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
app.register_functions(http_bp)
# app.register_functions(chat_bp)

#ghl functions below
# app.register_functions(gt_contact)
# app.register_functions(gt_invoices)
# app.register_functions(gt_invoice_details)
# app.register_functions(gt_company)  
# app.register_functions(gt_account_details) 
# app.register_functions(gt_subscriptions)
# app.register_functions(gt_subscription_details)
# app.register_functions(gt_products)
# app.register_functions(gt_product_details)

# #hubspot functions below
# app.register_functions(hs_contact)
# app.register_functions(hs_company)
# app.register_functions(hs_product)
# app.register_functions(hs_contact_by_id)
# app.register_functions(hs_subscriptions)
# app.register_functions(hs_products)
# app.register_functions(hs_subscription_details)
# app.register_functions(hs_comp_line_items)
# app.register_functions(hs_contact_invoices)
# app.register_functions(hs_invoice_details)
# app.register_functions(hs_comp_invoices)
# app.register_functions(hs_account_details)
# app.register_functions(hs_subscriptions_for_company)




# # retrieve and create in db
# app.register_functions(db_create_product)
# app.register_functions(db_get_line_items_for_contactdb)
# app.register_functions(db_get_subscriptions_for_contactdb)
# app.register_functions(db_line_items_for_subscriptiondb)


#salesforce and logging functions
# app.register_functions(add_contact_queue)
# app.register_functions(update_opportunity_note)
# app.register_functions(process_contact_queue)
# app.register_functions(outgoing_queue_listener)
# app.register_functions(call_listener_bp)
# app.register_functions(crud_blob_bp)
# app.register_functions(crud_transaction_events)

# app.register_functions(hs_create_leads)


