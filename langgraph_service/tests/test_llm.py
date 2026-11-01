"""Picking the model (vLLM or Claude) from the settings. No network needed."""

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent import graph
from config import settings


def test_vllm_by_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "vllm")

    assert isinstance(graph.build_llm(), ChatOpenAI)


def test_claude_with_fallbacks(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_model", "claude-opus-5-5")
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("test-key"))

    llm = graph.build_llm()

    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-opus-5-5"
    assert llm.effort == settings.anthropic_effort
    assert llm.temperature is None  # current Claude models reject temperature
    assert llm.model_kwargs == {"fallbacks": "default"}
    assert llm.betas == ["server-side-fallback-2026-07-01"]


def test_no_fallbacks_for_models_without_them(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("test-key"))

    llm = graph.build_llm()

    assert llm.model_kwargs == {}
    assert not llm.betas


def test_route_reads_text_next_to_thinking_blocks():
    promise = AIMessage(
        content=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Let me check his fixtures."},
        ]
    )

    assert graph.route_after_agent({"messages": [promise], "nudges": 0}) == "nudge"
