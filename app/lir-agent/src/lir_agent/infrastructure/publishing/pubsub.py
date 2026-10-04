"""CasePublisher on Pub/Sub: each accepted case becomes one message on the cases topic."""

import json
from typing import Any

from lir_agent.application.ports import CasePublishError

# Seconds to wait for Pub/Sub to accept a message before the request fails with 503.
PUBLISH_TIMEOUT = 30.0


class PubSubCasePublisher:
    """Publishes the payload as UTF-8 JSON, ordered per customer.

    The client honours `PUBSUB_EMULATOR_HOST`, so local runs use the emulator unchanged.
    """

    def __init__(self, project: str, topic: str, client: Any = None) -> None:
        """Bind the topic; `client` replaces `pubsub_v1.PublisherClient` in tests."""
        if client is None:
            from google.cloud import pubsub_v1  # only when Pub/Sub is selected

            client = pubsub_v1.PublisherClient(
                publisher_options=pubsub_v1.types.PublisherOptions(
                    enable_message_ordering=True
                )
            )
        self._client = client
        self._topic = client.topic_path(project, topic)

    def publish(
        self, payload: dict[str, Any], attributes: dict[str, str], ordering_key: str
    ) -> None:
        """Publish and wait for the server's acceptance (blocking: call off the event loop)."""
        data = json.dumps(payload, ensure_ascii=False).encode()
        future = self._client.publish(
            self._topic, data, ordering_key=ordering_key, **attributes
        )
        try:
            future.result(timeout=PUBLISH_TIMEOUT)
        except Exception as error:
            # A failure pauses the ordering key: resume it so the client's retry can publish.
            self._client.resume_publish(self._topic, ordering_key)
            raise CasePublishError(type(error).__name__) from error
