"""Counting the tokens each model call uses, and what they cost.

Every model call reports how many tokens it read (input) and wrote (output).
With Claude's prompt caching, part of the input may come from the cache
(cheaper) or be written to it (slightly dearer). This module turns those
numbers into log lines, so we can see where the money (or, with a local model,
the context window) goes:

    model call: claude-haiku-5-5 in=952 (cache read 0, cache write 930) out=48 cost=$0.00012
    chat turn: 2 model calls, in=3580 (cache read 930, cache write 930) out=453 cost=$0.00045

How it's used: server.py creates one UsageTracker per chat request and passes
it to the graph as a "callback". LangChain calls `on_llm_end` after every
model call, wherever in the graph it happens.
"""

import logging
from dataclasses import dataclass

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float  # input read from the prompt cache
    cache_write: float  # input written to the cache (5-minute lifetime)


# Anthropic's prices (November 2026). A cache read costs about a tenth of normal
# input (a twentieth on Opus 5.5); a cache write costs 1.25x normal input.
# Models not listed here (a local vLLM model) are free: tokens are still
# logged, the cost is not.
PRICES = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
}


@dataclass
class Usage:
    """Token counts for one model call, or added up over several."""

    calls: int = 0
    input: int = 0  # all input tokens, including the cached ones below
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0  # includes the model's thinking
    cost: float | None = 0.0  # dollars; None when the model's price is unknown

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input += other.input
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.output += other.output
        # One unknown price makes the total unknown too.
        if self.cost is None or other.cost is None:
            self.cost = None
        else:
            self.cost += other.cost

    def describe(self) -> str:
        cost = "unknown" if self.cost is None else f"${self.cost:.5f}"
        return (
            f"in={self.input} (cache read {self.cache_read}, cache write {self.cache_write}) "
            f"out={self.output} cost={cost}"
        )


def usage_of(message: AIMessage, model: str) -> Usage:
    """The token counts LangChain attached to a model's reply, priced for `model`."""
    meta = message.usage_metadata or {}
    details = meta.get("input_token_details", {})
    # langchain-anthropic reports cache writes per cache lifetime
    # ("ephemeral_5m_input_tokens") and leaves "cache_creation" at 0; other
    # integrations use "cache_creation". Take whichever is filled in. (We only
    # use the 5-minute cache, so all writes are priced at its rate.)
    by_lifetime = details.get("ephemeral_5m_input_tokens", 0) + details.get(
        "ephemeral_1h_input_tokens", 0
    )
    usage = Usage(
        calls=1,
        input=meta.get("input_tokens", 0),
        cache_read=details.get("cache_read", 0) or 0,
        cache_write=details.get("cache_creation", 0) or by_lifetime,
        output=meta.get("output_tokens", 0),
    )
    usage.cost = cost_of(usage, model)
    return usage


def cost_of(usage: Usage, model: str) -> float | None:
    """What the tokens cost in dollars, or None if the model's price isn't known."""
    price = PRICES.get(model)
    if price is None:
        return None
    # `input` counts every input token; the cached ones are charged at their own rate.
    uncached = usage.input - usage.cache_read - usage.cache_write
    dollars_per_million = (
        uncached * price.input
        + usage.cache_read * price.cache_read
        + usage.cache_write * price.cache_write
        + usage.output * price.output
    )
    return dollars_per_million / 1_000_000


class UsageTracker(AsyncCallbackHandler):
    """Adds up the usage of every model call in one chat request.

    LangChain calls `on_llm_end` after each model call it runs with this
    handler in its `callbacks`; the graph passes callbacks down to every node.
    """

    def __init__(self, model: str) -> None:
        super().__init__()
        self.model = model
        self.total = Usage()

    async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        for generations in response.generations:
            for generation in generations:
                if isinstance(generation, ChatGeneration) and isinstance(
                    generation.message, AIMessage
                ):
                    call = usage_of(generation.message, self.model)
                    logger.info("model call: %s %s", self.model, call.describe())
                    self.total.add(call)

    def log_total(self) -> None:
        """One line for the whole request: call it when the request finishes."""
        logger.info("chat turn: %d model calls, %s", self.total.calls, self.total.describe())
