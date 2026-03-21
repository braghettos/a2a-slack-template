import re
import os
import uuid
import logging
from typing import Tuple

import httpx
from slack_bolt.async_app import AsyncApp
from slack_bolt import Say, Ack, BoltContext
from slack_sdk.web.async_client import AsyncWebClient

from a2a.client import A2AClient
from a2a.types import Message, TextPart, MessageSendParams, SendMessageRequest

logger = logging.getLogger(__name__)

# Reuse a single httpx client for the lifetime of the process to avoid
# opening (and leaking) a new TCP connection pool on every A2A request.
_httpx_client = httpx.AsyncClient(timeout=600.0)


async def invoke_a2a_agent(agent_url: str, input_text: str) -> Tuple[str, str]:
    """Invoke an A2A agent and return (response_text, usage_info)."""
    a2a_client = A2AClient(url=agent_url, httpx_client=_httpx_client)

    text_part = TextPart(text=input_text)
    message = Message(
        role="user", parts=[text_part], message_id=str(uuid.uuid4())
    )
    payload = MessageSendParams(message=message, message_id=str(uuid.uuid4()))

    logger.info("Invoking A2A agent: %s", agent_url)
    response = await a2a_client.send_message(
        SendMessageRequest(id=str(uuid.uuid4()), params=payload)
    )

    text = ""
    usage_info = ""

    if response.root and response.root.result:
        task = response.root.result
        if hasattr(task, "artifacts") and task.artifacts:
            for artifact in task.artifacts:
                if hasattr(artifact, "parts") and artifact.parts:
                    for part in artifact.parts:
                        if hasattr(part, "root") and hasattr(part.root, "text"):
                            text += part.root.text
            usage_info = _extract_usage_info(task)
        else:
            text = "No artifacts found in the response"
    else:
        text = "No response from the agent"

    return text, usage_info


def _extract_usage_info(task) -> str:
    """Extract token usage from A2A task metadata (ADK format)."""
    try:
        metadata = getattr(task, "metadata", None) or {}
        usage = metadata.get("adk_usage_metadata")
        if not usage:
            return ""
        prompt = usage.get("promptTokenCount", 0)
        candidates = usage.get("candidatesTokenCount", 0)
        total = usage.get("totalTokenCount", 0)
        return (
            f"💡 *Token Usage:* "
            f"• Prompt: {prompt:,} "
            f"• Response: {candidates:,} "
            f"• Total: {total:,}"
        )
    except Exception:
        return ""


def extract_full_message_text(event: dict) -> str:
    """Extract the full message content from a Slack event.

    Slack delivers rich content in ``blocks`` and ``attachments`` while the
    top-level ``text`` field is only a plain-text fallback that often omits
    important context (e.g. alert details from HyperDX webhooks).  This
    function merges all three sources to give downstream consumers the
    complete picture.
    """
    parts: list[str] = []

    # 1. Plain-text fallback — strip bot mentions
    text = event.get("text", "")
    clean = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if clean:
        parts.append(clean)

    # 2. Blocks (rich_text, section, etc.)
    for block in event.get("blocks", []):
        btype = block.get("type", "")

        if btype == "section":
            section_text = block.get("text", {})
            if isinstance(section_text, dict):
                t = re.sub(r"<@[A-Z0-9]+>", "", section_text.get("text", "")).strip()
                if t and t not in clean:
                    parts.append(t)

        elif btype == "rich_text":
            for element in block.get("elements", []):
                for sub in element.get("elements", []):
                    if sub.get("type") == "text":
                        t = sub.get("text", "").strip()
                        if t and t not in clean:
                            parts.append(t)

    # 3. Attachments (webhook integrations like HyperDX)
    for att in event.get("attachments", []):
        for field in ("pretext", "text", "fallback"):
            t = att.get(field, "")
            if t and t not in clean:
                parts.append(t)
        for f in att.get("fields", []):
            title = f.get("title", "")
            value = f.get("value", "")
            if title or value:
                parts.append(f"{title}: {value}" if title else value)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Slack command handler
# ---------------------------------------------------------------------------

async def mykagent_command(
    client: AsyncWebClient,
    ack: Ack,
    command,
    say: Say,
    logger: logging.Logger,
    context: BoltContext,
):
    await ack()

    user_id = context["user_id"]
    channel_id = context["channel_id"]
    text = command.get("text")

    await client.chat_postEphemeral(
        channel=channel_id, user=user_id, text="Thinking..."
    )

    kagent_a2a_url = os.getenv("KAGENT_A2A_URL")
    if not kagent_a2a_url:
        await client.chat_postMessage(
            channel=channel_id,
            text=(
                "Hello! Set the `KAGENT_A2A_URL` environment variable "
                "to use the /mykagent command."
            ),
        )
        return

    try:
        response, usage_info = await invoke_a2a_agent(kagent_a2a_url, text)

        if response and response.strip():
            blocks = [
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "image",
                            "image_url": "https://raw.githubusercontent.com/kagent-dev/main/img/kagent-mark.png",
                            "alt_text": "kagent response",
                        },
                        {"type": "mrkdwn", "text": f"*Query:* {text}"},
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": response[:3000]},
                },
            ]
            if usage_info:
                blocks.append({"type": "divider"})
                blocks.append(
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": usage_info}]}
                )

            await client.chat_postMessage(
                channel=channel_id,
                blocks=blocks,
                text=(
                    f"AI Agent Response: {response[:100]}..."
                    if len(response) > 100
                    else f"AI Agent Response: {response}"
                ),
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                text="No response received from the agent.",
            )
    except Exception as e:
        logger.error("Error in /mykagent: %s", e)
        error_blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "❌ Error", "emoji": True},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*Query:* {text}"}],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error Details:*\n```{e}```"},
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "🔄 *Try again* or rephrase your question"}
                ],
            },
        ]
        await client.chat_postMessage(
            channel=channel_id,
            blocks=error_blocks,
            text=f"AI Agent Error: {e}",
        )


# ---------------------------------------------------------------------------
# Slack app_mention handler
# ---------------------------------------------------------------------------

async def handle_app_mention(
    event, say: Say, logger: logging.Logger, client: AsyncWebClient
):
    """Handle ``@bot`` mentions in channels.

    Extracts the full message content (including blocks and attachments
    from webhook integrations) and forwards it to the A2A agent.
    """
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user_id = event.get("user") or event.get("bot_id", "system")

    full_text = extract_full_message_text(event)

    if not full_text:
        full_text = (
            "A pod restart alert was triggered. "
            "Please investigate the recent pod restart events "
            "and suggest remediation steps."
        )

    logger.info("App mention from %s (%d chars)", user_id, len(full_text))

    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text="🔍 Investigating... I'm analyzing the situation.",
    )

    kagent_a2a_url = os.getenv("KAGENT_A2A_URL")
    if not kagent_a2a_url:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="⚠️ KAGENT_A2A_URL is not configured. Cannot reach the autopilot agent.",
        )
        return

    try:
        response, usage_info = await invoke_a2a_agent(kagent_a2a_url, full_text)

        if response and response.strip():
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": response[:3000]},
                },
            ]
            if usage_info:
                blocks.append({"type": "divider"})
                blocks.append(
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": usage_info}]}
                )

            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=blocks,
                text=response[:100],
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="No response received from the agent.",
            )
    except Exception as e:
        logger.error("Error handling app_mention: %s", e)
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"❌ Error: {e}",
        )


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------

def register_handlers(app: AsyncApp):
    """Register all event and command handlers."""
    app.event("app_mention")(handle_app_mention)
    app.command("/mykagent")(mykagent_command)
