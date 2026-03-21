import os
import logging
import asyncio

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.http_retry.builtin_async_handlers import (
    AsyncConnectionErrorRetryHandler,
    AsyncRateLimitErrorRetryHandler,
    AsyncServerErrorRetryHandler,
)
from dotenv import load_dotenv

from handlers import register_handlers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> AsyncApp:
    """Create and configure the Slack Bolt async app.

    The default Slack SDK retry configuration only handles TCP-level
    connection errors (``AsyncConnectionErrorRetryHandler`` with 1 retry).
    HTTP 5xx responses — especially the transient 503s Slack returns under
    load — are **not** retried by default, causing ``SlackApiError`` to
    bubble up on the first failure.

    We fix this by constructing an ``AsyncWebClient`` with three explicit
    retry handlers and injecting it into ``AsyncApp``:

    * ``AsyncServerErrorRetryHandler``  – retries HTTP 500 / 503
    * ``AsyncConnectionErrorRetryHandler`` – retries TCP failures
    * ``AsyncRateLimitErrorRetryHandler`` – retries HTTP 429
    """
    retry_handlers = [
        AsyncServerErrorRetryHandler(max_retry_count=3),
        AsyncConnectionErrorRetryHandler(max_retry_count=3),
        AsyncRateLimitErrorRetryHandler(max_retry_count=2),
    ]

    client = AsyncWebClient(
        token=os.environ.get("SLACK_BOT_TOKEN"),
        retry_handlers=retry_handlers,
    )

    app = AsyncApp(client=client)

    async def error_handler(error, body):
        logger.error(f"Unhandled error: {error}")

    app.error(error_handler)
    register_handlers(app)
    return app


async def main():
    logger.info("Starting kagent Slack bot")
    app = create_app()
    handler = AsyncSocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
