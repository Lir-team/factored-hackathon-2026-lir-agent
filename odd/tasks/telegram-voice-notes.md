# Telegram voice notes

Branch: `feat/telegram-voice-notes` · Status: in progress · Delivery: single-pr (forecast ~350 authored lines)

## Objective

A customer can send a voice note to the Lir Telegram bot. The bot transcribes it with
Google Cloud Speech-to-Text and the agent answers the transcript as if the customer typed it.

## Problem

`interface/http/telegram.py:93-100` drops every message without `text`, so voice notes are
silently ignored today.

## Scope

- In: Telegram `voice` messages (OGG/Opus), up to 60 s (sync `recognize` limit).
- Out: `audio`/document files, long-running recognition, echoing the transcript,
  lir-infra changes (API enablement, IAM, env vars live in the separate `lir-infra` repo).

## Design

- Port `SpeechToText` in `application/ports.py`: `async transcribe(audio: bytes, language: Language) -> str`,
  raising `TranscriptionError` on failure.
- Adapter `infrastructure/speech/google.py` using `google-cloud-speech` (v1 `SpeechAsyncClient`,
  `OGG_OPUS`, 48 kHz, `latest_short` model); language `es` -> `es-US`, `pt` -> `pt-BR`.
  Lazy import + injectable client, like `cases_inbox/gcs.py`.
- Setting `SPEECH_TO_TEXT: Literal["off","google"] = "off"`; `build_speech_to_text` in `container.py`.
- Telegram adapter gains `download_file(file_id) -> bytes` (`getFile` + file URL, token never logged).
- Webhook parses `message.voice` (`file_id`, `duration`); the use case downloads, transcribes and
  feeds the transcript into the existing text path (linking, length cap, conversation), never as a
  `/start` command.
- Polite es/pt replies in `domain/telegram.py`: voice off, too long, could not understand.

## Tasks

- [x] T1 Port, Google adapter, setting, container wiring, dependency, adapter tests. Route: delegated (writer trigger: 2+ non-trivial files). Commit: `afa5a70`.
- [x] T2 Telegram file download, webhook voice parsing, use-case voice path, es/pt replies, webhook tests. Route: delegated (same writer).
- [ ] T3 Docs: `.env.example`, README Telegram section (needs `speech.googleapis.com` + `roles/speech.client` in lir-infra). Route: delegated (same writer).

## Acceptance criteria

- Voice note from a linked chat -> transcript reaches `Conversations.converse` and the reply is sent.
- `SPEECH_TO_TEXT=off` -> polite "send text" reply, no download.
- Voice longer than 60 s -> polite refusal, no download.
- Transcription failure or empty transcript -> polite "could not understand" reply, update not retried forever.
- Unlinked chat -> same "use_form" reply as text.

## Checks

`cd app/lir-agent && uv run pytest -q && uv run ruff check . && uv run pyright`

## Progress

- T1 done: `SpeechToText`/`TranscriptionError` and `ChatFiles`/`FileNotDownloadedError` ports, `GoogleSpeechToText` (v1 async, OGG_OPUS 48 kHz, `latest_short`), `SPEECH_TO_TEXT`/`VOICE_MAX_SECONDS`, `build_speech_to_text`. Checks: pytest 425 passed; ruff/pyright clean except pre-existing errors in `tests/infrastructure/test_retrying_repository.py`.
- T2 done: `TelegramBotMessenger.download_file` (`getFile` + file URL, token never logged), webhook parses `message.voice`, `AnswerTelegramMessage.execute_voice` (link -> off -> duration -> download/transcribe -> length cap -> converse), es/pt `voice_off`/`voice_too_long`/`voice_not_understood`; container wires the built bot as `chat_files`. Checks: pytest 441 passed; ruff/pyright clean except the same pre-existing errors.
- Next: T3.

## Follow-ups

- lir-infra: enable `speech.googleapis.com`, grant `roles/speech.client` to the runtime SA, set `SPEECH_TO_TEXT=google`.
- Fix review points left open on merged PRs #50 and #53 (evals lockfile, hardcoded conclusions, retry scope).
