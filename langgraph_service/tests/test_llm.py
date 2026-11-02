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
    assert llm.model_kwargs == {"cache_control": {"type": "ephemeral"}, "fallbacks": "default"}
    assert llm.betas == ["context-management-2025-06-27", "server-side-fallback-2026-07-01"]


def test_no_fallbacks_for_models_without_them(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("test-key"))

    llm = graph.build_llm()

    assert llm.model_kwargs == {"cache_control": {"type": "ephemeral"}}  # caching only
    assert llm.betas == ["context-management-2025-06-27"]


def test_claude_clears_old_tool_results_on_long_requests(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "claude_clear_tool_results_at_tokens", 20000)

    [edit] = graph.build_llm().context_management["edits"]

    assert edit["type"] == "clear_tool_uses_20250919"
    assert edit["trigger"] == {"type": "input_tokens", "value": 20000}
    assert edit["keep"] == {"type": "tool_uses", "value": 3}


def test_claude_system_prompt_is_marked_for_caching(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")

    [block] = graph.system_message().content

    assert block["text"] == graph.SYSTEM_PROMPT
    assert block["cache_control"] == {"type": "ephemeral"}


def test_vllm_gets_a_plain_system_prompt(monkeypatch):
    # vLLM caches prompt beginnings by itself; Anthropic's markers would only confuse it.
    monkeypatch.setattr(settings, "llm_provider", "vllm")

    assert graph.system_message().content == graph.SYSTEM_PROMPT
    assert graph.build_llm().stream_usage is True  # token counts for the usage log


def test_route_reads_text_next_to_thinking_blocks():
    promise = AIMessage(
        content=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Let me check his fixtures."},
        ]
    )

    assert graph.route_after_agent({"messages": [promise], "nudges": 0}) == "nudge"
