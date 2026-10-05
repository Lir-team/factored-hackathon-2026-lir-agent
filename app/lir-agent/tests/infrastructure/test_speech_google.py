import asyncio

import pytest
from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable
from google.cloud import speech

from lir_agent.application.ports import TranscriptionError
from lir_agent.container import build_speech_to_text
from lir_agent.infrastructure.speech import GoogleSpeechToText

AUDIO = b"OggS-voice-note"


class FakeSpeechClient:
    """Stands in for `SpeechAsyncClient`: records the request, answers `response`."""

    def __init__(
        self,
        response: speech.RecognizeResponse | None = None,
        error: GoogleAPICallError | None = None,
    ) -> None:
        self.response = response or speech.RecognizeResponse()
        self.error = error
        self.calls: list[tuple[speech.RecognitionConfig, speech.RecognitionAudio]] = []

    async def recognize(
        self,
        *,
        config: speech.RecognitionConfig,
        audio: speech.RecognitionAudio,
        timeout: float,
    ) -> speech.RecognizeResponse:
        self.calls.append((config, audio))
        if self.error:
            raise self.error
        return self.response


def response(*transcripts: str) -> speech.RecognizeResponse:
    return speech.RecognizeResponse(
        results=[
            speech.SpeechRecognitionResult(
                alternatives=[speech.SpeechRecognitionAlternative(transcript=t)]
            )
            for t in transcripts
        ]
    )


def test_transcribes_a_telegram_voice_note():
    client = FakeSpeechClient(response("Hola,", "no reconozco un cargo."))

    text = asyncio.run(GoogleSpeechToText(client=client).transcribe(AUDIO, "es"))

    assert text == "Hola, no reconozco un cargo."
    [(config, audio)] = client.calls
    assert config.encoding == speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    assert config.sample_rate_hertz == 48000
    assert config.model == "latest_short"
    assert config.language_code == "es-US"
    assert config.enable_automatic_punctuation is True
    assert audio.content == AUDIO


def test_portuguese_is_recognized_as_brazilian_portuguese():
    client = FakeSpeechClient(response("Olá"))

    asyncio.run(GoogleSpeechToText(client=client).transcribe(AUDIO, "pt"))

    assert client.calls[0][0].language_code == "pt-BR"


def test_nothing_recognized_is_an_empty_transcript():
    client = FakeSpeechClient(response())

    assert asyncio.run(GoogleSpeechToText(client=client).transcribe(AUDIO, "es")) == ""


def test_an_api_failure_is_a_transcription_error():
    client = FakeSpeechClient(error=ServiceUnavailable("speech down"))

    with pytest.raises(TranscriptionError):
        asyncio.run(GoogleSpeechToText(client=client).transcribe(AUDIO, "es"))


def test_speech_to_text_is_off_by_default(settings):
    assert build_speech_to_text(settings) is None


def test_google_speech_to_text_is_built_when_selected(settings):
    google = settings.model_copy(update={"speech_to_text": "google"})

    assert isinstance(build_speech_to_text(google), GoogleSpeechToText)
