"""Keeping what we send to a small local model inside its context window.

vLLM serves the local model with room for 4,096 tokens in total
(infrastructure/vllm/vllm_start.sh): the tool list, the system prompt, the
conversation AND the answer must all fit. Tool results are the biggest part
of a conversation and the least needed later (the model has already used them
in its answer), so when the conversation is too long, the oldest ones are
replaced by a short note. The model can call the tool again if it needs one.

What's removed is only left out of the request: the conversation itself (the
graph state) keeps everything.

Claude doesn't use this. Its context is far larger, and Anthropic clears old
tool results on its side instead ("context editing", see build_llm in
graph.py), in a way that keeps its prompt cache working. Changing earlier
messages ourselves would break that cache.
"""

import logging

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

# A rough, cautious conversion: English text with numbers is about 3-4
# characters per token. Counting exactly would need the model's tokenizer.
CHARS_PER_TOKEN = 3

REMOVED_NOTE = "[Earlier tool result removed to save space. Call the tool again if you need it.]"


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """About how many tokens these messages take, tool-call arguments included."""
    chars = 0
    for message in messages:
        chars += len(message.text)
        chars += len(str(getattr(message, "tool_calls", "") or ""))
    return chars // CHARS_PER_TOKEN


def trim_tool_results(messages: list[BaseMessage], budget_tokens: int) -> list[BaseMessage]:
    """The messages to send, with the oldest tool results replaced by REMOVED_NOTE
    until the estimate fits in `budget_tokens`.

    Tool results that belong to the user's latest question are never removed:
    the model is working with them right now. If the messages still don't fit
    after that, they're sent anyway with a warning (vLLM then reports an error).
    """
    if estimate_tokens(messages) <= budget_tokens:
        return messages  # the usual case: nothing to do

    # Results after the latest user message are the current question's.
    latest_question = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0
    )
    trimmed = list(messages)  # a copy: the graph state is left untouched
    for i in range(latest_question):  # oldest first
        message = trimmed[i]
        if isinstance(message, ToolMessage) and message.content != REMOVED_NOTE:
            # Same message (same tool_call_id, so the model still sees which call
            # it answered), new content.
            trimmed[i] = message.model_copy(update={"content": REMOVED_NOTE})
            if estimate_tokens(trimmed) <= budget_tokens:
                return trimmed

    logger.warning(
        "conversation still ~%d tokens after trimming tool results (budget %d)",
        estimate_tokens(trimmed),
        budget_tokens,
    )
    return trimmed
