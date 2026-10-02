"""Terminal chat against the root agent with an in-memory session.

The loop itself is pure: it takes an `ask` callable plus `read`/`write`
functions, so it can be tested without a model. `ask_agent` is the only
piece that touches ADK, and `main` wires the two together.
"""

import asyncio
import uuid
from collections.abc import Callable

from google.adk.runners import InMemoryRunner
from google.genai import types

from .agent.agent import build_root_agent

EXIT_PHRASE = "chao pescao"
APP_NAME = "clir"
USER_ID = "local-user"


def chat_loop(
    *,
    ask: Callable[[str], str],
    read: Callable[[], str],
    write: Callable[[str], None],
    exit_phrase: str = EXIT_PHRASE,
) -> None:
    """Forward each line from `read` to `ask` until the exit phrase or EOF.

    Blank lines are ignored. The exit phrase comparison trims whitespace and
    ignores case, so "  Chao Pescao " also ends the session.
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
            if event.is_final_response() and event.content and event.content.parts:
                reply.extend(p.text for p in event.content.parts if p.text)
        return "".join(reply)

    return ask


def main() -> None:
    """Start an interactive session; type "chao pescao" to leave."""
    runner = InMemoryRunner(agent=build_root_agent(), app_name=APP_NAME)
    session_id = uuid.uuid4().hex
    asyncio.run(
        runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
    )
    print(f'Chatting with {runner.agent.name}. Type "{EXIT_PHRASE}" to exit.')
    chat_loop(
        ask=build_ask(runner, session_id),
        read=lambda: input("you> "),
        write=lambda text: print(f"agent> {text}"),
    )
    print("Chao!")


if __name__ == "__main__":
    main()
