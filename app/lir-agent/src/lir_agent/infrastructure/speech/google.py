"""SpeechToText on Google Cloud Speech-to-Text (v1 synchronous `recognize`).

Telegram voice notes are OGG/Opus at 48 kHz. Synchronous recognition accepts up to
60 seconds of audio, the cap on voice notes (`VOICE_MAX_SECONDS`).
"""

from typing import Any

from lir_agent.application.ports import TranscriptionError
from lir_agent.domain.language import Language

# The recognizer locale of each language the agent speaks.
_LOCALES: dict[Language, str] = {"es": "es-US", "pt": "pt-BR"}
_SAMPLE_RATE_HERTZ = 48000
_MODEL = "latest_short"  # tuned for short utterances such as voice notes
_TIMEOUT_SECONDS = 30.0


class GoogleSpeechToText:
    """Transcribes voice notes with `SpeechAsyncClient`, authenticated by ADC."""

    def __init__(self, client: Any = None) -> None:
        """Keep `client` (tests pass a fake); the real one is made on first use.

        The async gRPC client binds to the running event loop, so it is not built here.
        """
        self._client = client

    async def transcribe(self, audio: bytes, language: Language) -> str:
        """The transcript of `audio`, `""` when no speech was recognized.

        Raises:
            TranscriptionError: If the client could not authenticate or the call failed.
        """
        from google.api_core.exceptions import GoogleAPIError
        from google.auth.exceptions import GoogleAuthError
        from google.cloud import speech  # only when Google speech is selected

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
            sample_rate_hertz=_SAMPLE_RATE_HERTZ,
            language_code=_LOCALES[language],
            model=_MODEL,
            enable_automatic_punctuation=True,
        )
        try:
            if self._client is None:
                self._client = speech.SpeechAsyncClient()
            response = await self._client.recognize(
                config=config,
                audio=speech.RecognitionAudio(content=audio),
                timeout=_TIMEOUT_SECONDS,
            )
        except (GoogleAPIError, GoogleAuthError) as error:
            raise TranscriptionError(type(error).__name__) from None
        return " ".join(
            result.alternatives[0].transcript.strip()
            for result in response.results
            if result.alternatives
        ).strip()
