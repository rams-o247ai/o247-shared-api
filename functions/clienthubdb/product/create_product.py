import os
import json
import logging
from datetime import datetime, timezone
import base64
import psycopg2
import requests
import azure.functions as func
from azure.storage.queue import QueueClient

bp = func.Blueprint()

# PostgreSQL connection config
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "options": "-c search_path=" + os.getenv("DB_SCHEMA")
}

VAPI_URL = f"{os.getenv('VAPI_URL')}assistant"
HUBSPOT_URL = f"{os.getenv('HUBSPOT_URL')}objects/products"
AGENT_BUILDER_URL = os.getenv("AGENT_BUILDER_URL")
API_URL = os.getenv("API_URL")
API_CODE = os.getenv("API_CODE")
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")


# -------------------------------
#  HTTP ENTRYPOINT (FAST RESPONSE)
# -------------------------------

@bp.function_name(name="create_agent")
@bp.route(route="db/{businessId}/{contactId}/agent", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def create_agent(req: func.HttpRequest) -> func.HttpResponse:
    business_id = req.route_params.get("businessId")
    contact_id = req.route_params.get("contactId")

    if not business_id or not contact_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing businessId or contactId"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    agent_name = body.get("agentName")
    agent_voice_profile = body.get("agentVoiceProfile")
    job_description = body.get("jobDescription")
    template_name = body.get("templateName")
    business_name = body.get("businessName")
    fields = body.get("fields", [])
    rules = body.get("rules", [])

    if not all([agent_name, agent_voice_profile, job_description]):
        return func.HttpResponse(
            json.dumps({"error": "Missing one or more required fields"}),
            status_code=400,
            mimetype="application/json",
        )

    conn = None
    cursor = None

    try:
        # 1️⃣ Connect to DB
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # 2️⃣ Get or create default subscription for this contact
        cursor.execute(
            """
            SELECT subscription_id
            FROM app.subscriptions
            WHERE contact_id = %s
              AND is_default = TRUE
            """,
            (contact_id,),
        )
        row = cursor.fetchone()

        if row:
            subscription_id = row[0]
        else:
            cursor.execute(
                """
                INSERT INTO app.subscriptions
                    (company, administrative_contact, status, created_at, contact_id, is_default)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, TRUE)
                RETURNING subscription_id
                """,
                (business_name, "Default Admin", "active", contact_id),
            )
            subscription_id = cursor.fetchone()[0]

        # 3️⃣ Insert subscription_line_items row with status='processing'
        fields_json = json.dumps(fields)

        cursor.execute(
            """
            INSERT INTO app.subscription_line_items
                (subscription_id,
                 product_name,
                 description,
                 price,
                 created_at,
                 contact_id,
                 fields,
                 business_id,
                 business_name,
                 status)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s,
                    %s, %s, %s, 'processing')
            RETURNING line_item_id
            """,
            (
                subscription_id,
                agent_name,
                job_description,
                0.0,
                contact_id,
                fields_json,
                business_id,
                business_name,
            ),
        )
        line_item_id = cursor.fetchone()[0]

        conn.commit()

        # 4️⃣ Push background job to Queue
        queue_payload = {
            "subscriptionId": subscription_id,
            "lineItemId": line_item_id,
            "contactId": contact_id,
            "businessId": business_id,
            "agentData": body,
        }
        queue_message = json.dumps(queue_payload)
        message_bytes = queue_message.encode('utf-8')
        # Base64 encode the bytes
        message_b64 = base64.b64encode(message_bytes).decode('utf-8')
        # Log the encoded message
        queue_connection = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not queue_connection:
            logging.error("AZURE_STORAGE_CONNECTION_STRING connection string not set.")
        else:
            queue_client = QueueClient.from_connection_string(
                conn_str=queue_connection,
                queue_name="agent-generation",
            )
            try:
                # Try creating the queue if it doesn't exist (will do nothing if the queue exists)
                try:
                    queue_client.create_queue()
                except Exception as e:
                    # Log the error if the queue already exists, but continue
                    if "Queue already exists" in str(e):
                        logging.info(f"Queue already exists: {e}")
                    else:
                        logging.error(f"Error creating the queue: {str(e)}")
                        raise e  # re-raise the exception if it's not about the queue already existing

                # Send the base64 encoded message to the queue
                queue_client.send_message(message_b64)
                logging.info(f"Message sent to queue: {message_b64}")

            except Exception as e:
                logging.error(f"Error sending message to the queue: {str(e)}")


        # 5️⃣ Return immediately so UI can show "processing"
        return func.HttpResponse(
            json.dumps(
                {
                    "status": "processing",
                    "subscriptionId": subscription_id,
                    "lineItemId": line_item_id,
                }
            ),
            status_code=202,
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"Error in create_agent: {str(e)}")
        if conn:
            conn.rollback()
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# --------------------------------------
#  QUEUE TRIGGER (LONG-RUNNING WORKFLOW)
# --------------------------------------

@bp.function_name(name="process_agent_job")
@bp.queue_trigger(arg_name="msg", queue_name="agent-generation", connection="AZURE_STORAGE_CONNECTION_STRING")
def process_agent_job(msg: func.QueueMessage) -> None:
    logging.info("Queue message received.")
    raw_body = msg.get_body().decode("utf-8")
    data = json.loads(raw_body)

    subscription_id = data["subscriptionId"]
    line_item_id = data["lineItemId"]
    contact_id = data["contactId"]
    business_id = data["businessId"]
    body = data["agentData"]

    agent_name = body.get("agentName")
    agent_voice_profile = body.get("agentVoiceProfile")
    job_description = body.get("jobDescription")
    template_name = body.get("templateName")
    business_name = body.get("businessName")
    fields = body.get("fields", [])
    rules = body.get("rules", [])

    conn = None
    cursor = None

    try:
        # 1️⃣ Fetch vapiPrivateToken dynamically
        token_url = f"{API_URL}hubspot/companies/{business_id}/account?code={API_CODE}"
        token_resp = requests.get(token_url, headers={"Content-Type": "application/json"})
        logging.info(f"Token URL: {token_url}")
        logging.info(f"Token response status: {token_resp.status_code}")

        if token_resp.status_code != 200:
            raise Exception(f"Failed to fetch Vapi token: {token_resp.text}")

        token_data = token_resp.json()
        vapi_token = token_data.get("vapiPrivateToken")
        if not vapi_token:
            raise Exception("No vapiPrivateToken returned")

        # 2️⃣ Build AgentBuilder request body
        goals = "Collect " + ", ".join(f.get("name") for f in fields)
        forms_fields = [
            {
                "name": f.get("name"),
                "description": None,
                "type": f.get("type", "text"),
                "choices": None,
                "required": True,
                "value": f.get("name").replace(" ", "_").lower(),
            }
            for f in fields
        ]

        agent_builder_payload = {
            "name": agent_name,
            "jobDescription": job_description,
            "jobTitle": template_name,
            "clientId": "01K8A2W1CBTZDNW4HMAM7XWVD0",
            "voiceProfile": agent_voice_profile,
            "goals": goals,
            "companyName": business_name,
            "guardrails": ", ".join(rules),
            "forms": [
                {
                    "name": template_name,
                    "fields": forms_fields,
                    "cardinality": 0,
                }
            ],
            "settings": {
                "language": "en",
                "enableDropVoicemail": True,
                "callbackNumber": None,
                "callbackMessage": None,
                "autoGenerateCallbackMessage": False,
            },
        }

        agent_builder_resp = requests.post(
            AGENT_BUILDER_URL,
            headers={"Content-Type": "application/json"},
            json=agent_builder_payload,
        )
        if agent_builder_resp.status_code not in [200, 201]:
            logging.error(f"AgentBuilder API error: {agent_builder_resp.text}")
            logging.error(json.dumps(agent_builder_payload, indent=2))
            raise Exception("Failed to create agent via AgentBuilder")

        # 3️⃣ Build VAPI assistant using AgentBuilder output
        prompt_text = agent_builder_resp.text
        first_message = "Hello"
        structured_properties = {}
        required_fields = []

        for f in fields:
            field_name = f.get("name")
            # everything as string for now (can be improved later)
            structured_properties[field_name] = {"type": "string"}
            required_fields.append(field_name)

        vapi_payload = {
            "model": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"content": prompt_text, "role": "system"}],
            },
            "voice": {"provider": "vapi", "voiceId": agent_voice_profile},
            "name": agent_name,
            "firstMessage": first_message,
            "voicemailMessage": "",
            "endCallFunctionEnabled": True,
            "endCallMessage": "",
            "transcriber": {
                "provider": "deepgram",
                "model": "nova-2",
                "language": "en",
            },
            "firstMessageMode": "assistant-speaks-first",
            "voicemailDetection": {
                "provider": "vapi",
                "backoffPlan": {
                    "maxRetries": 6,
                    "startAtSeconds": 5,
                    "frequencySeconds": 5,
                },
                "beepMaxAwaitSeconds": 5,
            },
            "server": {
                "url": f"{API_URL}call/listener?code={API_CODE}",
            },
            "serverMessages": [
                "session.created",
                "session.updated",
                "status-update",
                "end-of-call-report",
            ],
            "startSpeakingPlan": {"waitSeconds": 0.7},
            "analysisPlan": {
                "minMessagesThreshold": 2,
                "structuredDataPlan": {
                    "enabled": True,
                    "schema": {
                        "type": "object",
                        "required": required_fields,
                        "properties": structured_properties,
                    },
                },
            },
        }

        headers_vapi = {
            "Authorization": f"Bearer {vapi_token}",
            "Content-Type": "application/json",
        }

        vapi_resp = requests.post(VAPI_URL, headers=headers_vapi, json=vapi_payload)
        if vapi_resp.status_code not in [200, 201]:
            logging.error(f"VAPI API error: {vapi_resp.text}")
            raise Exception("Failed to create assistant in VAPI")

        vapi_data = vapi_resp.json()
        agent_id = vapi_data.get("id")
        if not agent_id:
            raise Exception("No agent ID returned from VAPI")

        # 4️⃣ Create Product in HubSpot
        hubspot_headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        hubspot_payload = {
            "properties": {
                "name": agent_name,
                "price": "0",
                "hs_sku": agent_id,
                "description": job_description,
                "contact_id": contact_id,
            }
        }

        hubspot_resp = requests.post(
            HUBSPOT_URL, headers=hubspot_headers, json=hubspot_payload
        )
        if hubspot_resp.status_code not in [200, 201]:
            logging.error(f"HubSpot API error: {hubspot_resp.text}")
            raise Exception("Failed to create product in HubSpot")

        hubspot_data = hubspot_resp.json()
        product_id = hubspot_data.get("id")
        if not product_id:
            raise Exception("No product ID returned from HubSpot")

        # 5️⃣ Update DB with agent_id, product_id, status='pending'
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE app.subscription_line_items
            SET product_id = %s,
                agent_id = %s,
                status = 'pending'
            WHERE line_item_id = %s
            """,
            (product_id, agent_id, line_item_id),
        )

        conn.commit()
        logging.info(
            f"Agent job completed for line_item_id={line_item_id}, "
            f"agent_id={agent_id}, product_id={product_id}"
        )

    except Exception as e:
        logging.error(f"Error in process_agent_job: {str(e)}")

        # Try to mark status as failed
        try:
            if not conn:
                conn = psycopg2.connect(**DB_CONFIG)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE app.subscription_line_items
                SET status = 'failed'
                WHERE line_item_id = %s
                """,
                (line_item_id,),
            )
            conn.commit()
        except Exception as db_ex:
            logging.error(f"Failed to update status to 'failed': {db_ex}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
