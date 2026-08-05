"""Wiring tests for AnthropicClient.

These verify that the wrapper calls the underlying Anthropic SDK with
the expected shape and parses the response correctly. They do not make
real API calls — that would burn user tokens on every test run.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from predictive_review.config import Settings
from predictive_review.llm.anthropic_client import AnthropicClient
from predictive_review.llm.client import LLMClient, Message


def _fake_response(text: str = "model output", model: str = "claude-test-1") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text, type="text")],
        model=model,
    )


def _client_with_response(response) -> tuple[AnthropicClient, MagicMock]:
    sdk = MagicMock()
    sdk.messages.create.return_value = response
    return AnthropicClient(sdk), sdk


def test_anthropic_client_satisfies_llm_client_protocol() -> None:
    client, _ = _client_with_response(_fake_response())
    assert isinstance(client, LLMClient)


def test_complete_passes_messages_through_in_anthropic_format() -> None:
    client, sdk = _client_with_response(_fake_response())
    client.complete(
        system="you are a helpful judge",
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ],
        model="claude-test-1",
        max_tokens=128,
    )
    sdk.messages.create.assert_called_once_with(
        model="claude-test-1",
        system="you are a helpful judge",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        max_tokens=128,
    )


def test_complete_returns_text_and_model_id() -> None:
    client, _ = _client_with_response(
        _fake_response(text="the retry happens on 429", model="claude-sonnet-x")
    )
    result = client.complete(
        system="s", messages=[Message(role="user", content="q")], model="claude-sonnet-x"
    )
    assert result.text == "the retry happens on 429"
    assert result.model_id == "claude-sonnet-x"


def test_complete_joins_multiple_text_blocks() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(text="part one", type="text"),
            SimpleNamespace(text="part two", type="text"),
        ],
        model="m",
    )
    client, _ = _client_with_response(response)
    result = client.complete(
        system="s", messages=[Message(role="user", content="q")], model="m"
    )
    assert result.text == "part one\npart two"


def test_complete_raises_when_response_has_no_text_blocks() -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use")],
        model="m",
    )
    client, _ = _client_with_response(response)
    with pytest.raises(RuntimeError, match="no text blocks"):
        client.complete(
            system="s", messages=[Message(role="user", content="q")], model="m"
        )


def test_complete_requires_non_empty_model_id() -> None:
    client, _ = _client_with_response(_fake_response())
    with pytest.raises(ValueError, match="model id is required"):
        client.complete(
            system="s", messages=[Message(role="user", content="q")], model=""
        )


def test_from_settings_raises_when_api_key_missing() -> None:
    settings = Settings(anthropic_api_key="")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicClient.from_settings(settings)
