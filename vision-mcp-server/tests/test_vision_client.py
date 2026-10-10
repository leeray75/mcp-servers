from __future__ import annotations

import httpx
import pytest
from tests.conftest import BACKEND_URL, FAKE_API_KEY, FakeBackend

from vision_mcp.config import Settings
from vision_mcp.errors import BackendError
from vision_mcp.images import PreparedImage
from vision_mcp.vision_client import PROMPT_PREFIX, build_payload

IMAGE = PreparedImage("data:image/png;base64,AAAA", "image/png", 4, 3, False)


async def test_request_shape(settings: Settings, backend: FakeBackend) -> None:
    client = backend.client(settings)
    answer = await client.describe([IMAGE, IMAGE], "What colour is it?")
    request = backend.requests[0]
    assert str(request.url) == f"{BACKEND_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    body = backend.last_json
    assert body["model"] == "qwen3.8-27b"
    assert body["max_tokens"] == settings.vision_max_tokens
    assert body["temperature"] == settings.vision_temperature
    (message,) = body["messages"]
    assert message["role"] == "user"
    text, *images = message["content"]
    assert text["type"] == "text"
    assert text["text"].startswith(PROMPT_PREFIX)
    assert text["text"].endswith("Question: What colour is it?")
    assert [p["image_url"]["url"] for p in images] == [IMAGE.data_url, IMAGE.data_url]
    assert answer.text == "A red square."
    assert answer.truncated is False
    await client.aclose()


def test_build_payload_uses_settings(settings: Settings) -> None:
    custom = settings.model_copy(update={"vision_model": "other-model", "vision_max_tokens": 99})
    payload = build_payload(custom, [IMAGE], "q")
    assert payload["model"] == "other-model"
    assert payload["max_tokens"] == 99


async def test_list_content_is_joined(settings: Settings, backend: FakeBackend) -> None:
    backend.content = [
        {"type": "text", "text": "one"},
        {"type": "other"},
        {"type": "text", "text": "two"},
    ]
    assert (await backend.client(settings).describe([IMAGE], "q")).text == "one\ntwo"


async def test_length_finish_reason_marks_truncated(
    settings: Settings, backend: FakeBackend
) -> None:
    backend.finish_reason = "length"
    assert (await backend.client(settings).describe([IMAGE], "q")).truncated is True


@pytest.mark.parametrize("content", ["", "   ", None, [], [{"type": "image"}], 42])
async def test_empty_answers_are_reported(
    settings: Settings, backend: FakeBackend, content: object
) -> None:
    backend.content = content
    with pytest.raises(BackendError, match="no answer"):
        await backend.client(settings).describe([IMAGE], "q")


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (404, "HTTP 404"),
        (429, "HTTP 429"),
        (500, "HTTP 500"),
        (503, "HTTP 503"),
    ],
)
async def test_http_errors_are_mapped(
    settings: Settings, backend: FakeBackend, status: int, message: str
) -> None:
    backend.status = status
    backend.raw_body = b'{"error": "internal detail sk-LEAKYKEY123456789"}'
    with pytest.raises(BackendError, match=message) as info:
        await backend.client(settings).describe([IMAGE], "q")
    assert "LEAKYKEY" not in str(info.value)  # backend bodies are never echoed


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"choices": []}', b'{"choices": [1]}'])
async def test_malformed_responses_are_reported(
    settings: Settings, backend: FakeBackend, body: bytes
) -> None:
    backend.raw_body = body
    with pytest.raises(BackendError, match="unexpected response"):
        await backend.client(settings).describe([IMAGE], "q")


async def test_timeout_is_reported(settings: Settings, backend: FakeBackend) -> None:
    backend.raises = httpx.ReadTimeout("slow")
    with pytest.raises(BackendError, match="did not answer within"):
        await backend.client(settings).describe([IMAGE], "q")


async def test_connection_failure_is_reported(settings: Settings, backend: FakeBackend) -> None:
    backend.raises = httpx.ConnectError("refused")
    with pytest.raises(BackendError, match="Cannot reach"):
        await backend.client(settings).describe([IMAGE], "q")


async def test_redirects_are_not_followed(settings: Settings, backend: FakeBackend) -> None:
    backend.status = 302
    backend.raw_body = b""
    with pytest.raises(BackendError, match="HTTP 302"):
        await backend.client(settings).describe([IMAGE], "q")
    assert len(backend.requests) == 1
