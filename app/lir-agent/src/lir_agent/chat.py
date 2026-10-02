"""Terminal chat against the agent with an in-memory session.

The loop itself is pure: it takes an `ask` callable plus `read`/`write` functions, so it can
be tested without a model. `build_ask` is the only piece that touches ADK, and `main` wires
the two together.

The customer is identified before the chat starts and their ID lives in the session state,
together with an expiry and the authentication method; it never passes through the
conversation with the model.
"""

import argparse
import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta

from google.adk.runners import InMemoryRunner
from google.genai import types

from lir_agent.config.settings import get_settings
from lir_agent.domain.session import SessionState
from lir_agent.interface.adk import build_agent
from lir_agent.logging_config import configure_logging

EXIT_PHRASE = "chao pescao"
APP_NAME = "lir"
USER_ID = "local-user"
# `--customer-id` stands in for the bank's identity check (biometric KYC, mocked).
CLI_AUTH_METHOD = "cli_placeholder"

logger = logging.getLogger(__name__)


def chat_loop(
    *,
    ask: Callable[[str], str],
    read: Callable[[], str],
    write: Callable[[str], None],
    exit_phrase: str = EXIT_PHRASE,
) -> None:
    """Forward each line from `read` to `ask` until the exit phrase or EOF.

    Blank lines are ignored. The exit phrase comparison trims whitespace and ignores case,
    so "  Chao Pescao " also ends the session.
    """
    while True:
        try:
            line = read()
        except EOFError:
            return
        message = line.strip()
        if not message:
            continue
        if message.lower() == exit_phrase:
            return
        write(ask(message))


def build_ask(runner: InMemoryRunner, session_id: str) -> Callable[[str], str]:
    """Return a function that sends one user message and returns the reply text."""

    def ask(message: str) -> str:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        reply: list[str] = []
        for event in runner.run(
            user_id=USER_ID, session_id=session_id, new_message=content
        ):
            for call in event.get_function_calls():
                logger.debug("Tool call %s(%s)", call.name, call.args)
            if event.is_final_response() and event.content and event.content.parts:
                reply.extend(p.text for p in event.content.parts if p.text)
        if not reply:
            logger.warning("Agent returned no text for session %s", session_id)
        return "".join(reply)

    return ask


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line, normalizing the customer ID.

    `--customer-id` stands in for the authentication step until a real login exists.
    """
    parser = argparse.ArgumentParser(description="Chat with the Lir agent.")
    parser.add_argument(
        "--customer-id",
        required=True,
        help="ID of the signed-in customer (placeholder for real authentication).",
    )
    args = parser.parse_args(argv)
    args.customer_id = args.customer_id.strip().upper()
    if not args.customer_id:
        parser.error("--customer-id must not be blank")
    return args


def start_session(runner: InMemoryRunner, customer_id: str, ttl: timedelta) -> str:
    """Create a session bound to the customer, with an expiry, and return its ID."""
    state: dict = {}
    SessionState(state).start(customer_id, ttl, CLI_AUTH_METHOD)
    session_id = uuid.uuid4().hex
    asyncio.run(
        runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state=state
        )
    )
    return session_id


def main(argv: Sequence[str] | None = None) -> None:
    """Start an interactive session; type "chao pescao" to leave."""
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    runner = InMemoryRunner(agent=build_agent(settings), app_name=APP_NAME)
    session_id = start_session(
        runner, args.customer_id, timedelta(minutes=settings.session_ttl_minutes)
    )
    logger.info("Chat session %s started with agent %s", session_id, runner.agent.name)
    print(f'Chatting with {runner.agent.name}. Type "{EXIT_PHRASE}" to exit.')
    chat_loop(
        ask=build_ask(runner, session_id),
        read=lambda: input("you> "),
        write=lambda text: print(f"agent> {text}"),
    )
    logger.info("Chat session %s ended", session_id)
    print("Chao!")


if __name__ == "__main__":
    main()
