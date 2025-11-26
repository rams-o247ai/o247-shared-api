import os
import json
import azure.functions as func
import logging

from agno.agent import Agent, RunOutput
from agno.models.azure import AzureOpenAI

bp = func.Blueprint()

# https://o247-shared-api-gmercrdnhua7hvc0.eastus-01.azurewebsites.net/api/chat/completions


@bp.function_name("chat_completions")
@bp.route(route="deployments/{deployment_id}/chat/completions", methods=["POST"])
def chat_completions(req: func.HttpRequest) -> func.HttpResponse:
    """Forwards the incoming JSON body to Azure OpenAI Chat Completions and returns the AI response.

    Expects these environment variables to be set:
    - AZURE_OPENAI_ENDPOINT (e.g. https://my-openai-resource.openai.azure.com)
    - AZURE_OPENAI_API_KEY
    - AZURE_OPENAI_DEPLOYMENT_NAME (the deployment/model name to call)
    """
    logging.info("/chat/completions triggered")

    try:
        body = req.get_body().decode('utf-8')
    except ValueError:
        logging.error('Invalid or missing JSON body')
        return func.HttpResponse('Invalid or missing JSON body', status_code=400)

    logging.info('Request body: %s', body)

    endpoint = os.environ['AZURE_OPENAI_ENDPOINT']
    api_key = os.environ['AZURE_OPENAI_API_KEY']
    deployment = os.environ['AZURE_OPENAI_DEPLOYMENT_NAME']
    api_version = os.environ['OPENAI_API_VERSION']

    if not endpoint or not api_key or not deployment:
        msg = 'Azure OpenAI configuration is missing. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME.'
        logging.error(msg)
        return func.HttpResponse(msg, status_code=500)
    

    # Create agno agent
    agent = Agent(
        model=AzureOpenAI(id=deployment,
                          azure_endpoint=endpoint,
                          api_key=api_key,
                          api_version=api_version),
        markdown=False,
        telemetry=False
    )

    logging.info('Message to send to Azure OpenAI: %s', body)

    response: RunOutput = agent.run(input=body)
    return func.HttpResponse(body=response.content, status_code=200, mimetype='application/text')