"""Token counting and cost (agent/usage.py). No network needed."""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent.usage import Usage, UsageTracker, cost_of, usage_of


def reply(input_tokens, output_tokens, cache_read=0, cache_write=0) -> AIMessage:
    """A model reply carrying token counts, as LangChain attaches them."""
    return AIMessage(
        "hi",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write},
        },
    )


def test_cost_without_caching():
    # Haiku 5.5: $0.10 per million input tokens, $0.50 per million output tokens.
    usage = usage_of(reply(1_000_000, 1_000_000), "claude-haiku-5-5")

    assert usage.cost == pytest.approx(0.60)


def test_cached_input_is_priced_separately():
    # 1M input tokens: 600k read from the cache ($0.01/M), 300k written ($0.125/M),
    # and the remaining 100k at the normal price ($0.10/M).
    usage = usage_of(
        reply(1_000_000, 0, cache_read=600_000, cache_write=300_000), "claude-haiku-5-5"
    )

    assert usage.cost == pytest.approx(0.006 + 0.0375 + 0.01)


def test_cache_writes_reported_per_lifetime_are_counted():
    # The shape langchain-anthropic actually returns: "cache_creation" stays 0 and
    # the writes are under the 5-minute cache's name.
    message = AIMessage(
        "ok",
        usage_metadata={
            "input_tokens": 1543,
            "output_tokens": 5,
            "total_tokens": 1548,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 1539,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    )

    assert usage_of(message, "claude-haiku-5-5").cache_write == 1539


def test_unknown_model_has_no_cost_but_still_counts_tokens():
    usage = usage_of(reply(500, 20), "squad-scout-llm")  # the local vLLM model

    assert (usage.input, usage.output, usage.cost) == (500, 20, None)


def test_totals_add_up_and_unknown_cost_spreads():
    total = Usage()
    total.add(usage_of(reply(100, 10), "claude-haiku-5-5"))
    total.add(usage_of(reply(200, 20, cache_read=100), "claude-haiku-5-5"))

    assert (total.calls, total.input, total.output, total.cache_read) == (2, 300, 30, 100)

    total.add(usage_of(reply(1, 1), "squad-scout-llm"))
    assert total.cost is None


def test_cost_of_matches_the_price_table():
    assert cost_of(Usage(input=1_000_000), "claude-opus-5-5") == pytest.approx(4.00)


async def test_tracker_adds_up_every_model_call(caplog):
    tracker = UsageTracker("claude-haiku-5-5")
    for message in (reply(900, 50, cache_write=800), reply(1000, 100, cache_read=800)):
        await tracker.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

    assert (tracker.total.calls, tracker.total.input, tracker.total.output) == (2, 1900, 150)

    with caplog.at_level("INFO"):
        tracker.log_total()
    assert (
        "chat turn: 2 model calls, in=1900 (cache read 800, cache write 800) out=150" in caplog.text
    )
