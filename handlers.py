from slack_bolt.async_app import AsyncApp
from logging import Logger
from slack_sdk import WebClient
from slack_bolt import Say, Ack, BoltContext
import os
import uuid
from a2a.client import A2AClient
from a2a.types import Message, TextPart, MessageSendParams, MessageResponse

async def invoke_a2a_agent(agent_url: str, input: str, logger: Logger):
    """
    Invokes the A2A agent and returns the response.
    """
    a2a_client = A2AClient(url=agent_url, timeout=600.0)

    # Create Pydantic models
    text_part = TextPart(text=input)
    message = Message(role="user", parts=[text_part])

    # Create MessageSendParams
    message_params = MessageSendParams(
        message=message,
        metadata={}
    )

    logger.info(f"Invoking the agent: {agent_url}")

    # Convert to dict for the client
    payload = message_params.model_dump()
    response = await a2a_client.send_task(payload)
    text = ""
    # The API returns the message directly in the result
    if response.result and response.result.parts:
        for part in response.result.parts:
            if hasattr(part, 'text'):
                text += part.text
    return text

async def mykagent_command(
    client: WebClient, ack: Ack, command, say: Say, logger: Logger, context: BoltContext
):
    await ack()

    user_id = context["user_id"]
    channel_id = context["channel_id"]
    text = command.get("text")

    await client.chat_postEphemeral(
        channel=channel_id,
        user=user_id,
        text="Thinking...",
    )

    # Check if the KAGENT_A2A_URL environment variable is set
    kagent_a2a_url = os.getenv("KAGENT_A2A_URL")
    if not kagent_a2a_url:
        # TODO: Implement the logic for the /mykagent command
        await client.chat_postMessage(
            channel=channel_id,
            user=user_id,
            text="Hello! Once you set the KAGENT_A2A_URL environment variable, you can use the /mykagent command.",
        )
        return

    # Invoke the KAGENT A2A API
    try:
        response = await invoke_a2a_agent(kagent_a2a_url, text, logger)
        await client.chat_postMessage(
            channel=channel_id,
            text=f"*Agent Response:*\n{response}",
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await client.chat_postMessage(
            channel=channel_id,
            user=user_id,
            text=f"Occurred an error while talking to kagent: {e}",
        )


def register_handlers(app: AsyncApp):
    """
    Register all handlers for the bot.
    """

    # Commands
    app.command("/mykagent")(mykagent_command)