r"""The agent loop as an explicit LangGraph state machine.

    START -> agent --(tool calls?)------> tools -> agent -> ...
                  \--(promised a lookup)-> nudge -> agent       (at most MAX_NUDGES times)
                  \--(final answer)-----------------------------> END

Vocabulary:
  node   a step: a function that takes the state and returns updates to it.
  edge   which node runs next. A conditional edge calls a function
         (route_after_agent) that looks at the state and picks the next node.
  END    stop; the state at this point is the result.

A typical run for "How has Saka been playing?":
  agent  -> model replies with a tool call: search_players(name="saka")
  tools  -> runs it, adds the JSON result to the messages as a ToolMessage
  agent  -> model reads the JSON and writes a text answer (no tool calls)
  END    (or nudge -> agent once, if that answer ends with "let me check...")
"""

import re

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.state import AgentState
from agent.tools import TOOLS
from agent.trim import estimate_tokens, trim_tool_results
from config import settings

# Sent as the first message of every model call. It sets the model's rules;
# the model sees it but the user doesn't.
SYSTEM_PROMPT = """You are Squad Scout, a Fantasy Premier League assistant.

Rules:
- Only answer questions about Fantasy Premier League and Premier League football.
- Never guess player stats, prices or fixtures. Always fetch them with your tools.
- Look players up with search_players before using any player_id or team_id.
- If a tool returns an ERROR, tell the user briefly that the data is unavailable
  right now. Do not invent numbers.
- If the conversation starts with the user's FPL team (loaded by the app), use it
  for questions about their team. Don't ask for it and don't look it up again.

Length:
- Keep answers short: at most about 120 words, or up to 5 bullet points.
  Write more only if the user asks for detail.
- Start with the answer. No introduction, no repeating the question, no closing
  advice the user didn't ask for.
- Cite the numbers you used.

How to work:
- If you need more data, call the tool immediately. Never write "let me fetch"
  or "I will check" - the user only sees your final message.
- Any message without a tool call is your FINAL answer. It must directly
  answer the question using the data you already have.
- For "how is X playing" questions, search_players already returns total
  points, form (average points over recent matches), price, ownership and
  availability status. That is enough to answer.
"""

# Small models often end a turn by announcing a lookup instead of making it.
# If a reply without tool calls matches this, the graph sends it back once.
# The regex means: "let me" / "I will" / ... then up to 40 characters that are
# not a full stop, then a lookup verb. So it matches "Let me check his
# availability" but not "I will be brief. Check the table".
PROMISE_PATTERN = re.compile(
    r"\b(let me|i will|i'll|i am going to|i'm going to|allow me to)\b[^.]{0,40}"
    r"\b(fetch|check|look|get|retrieve|find|pull|gather)\b",
    re.IGNORECASE,
)
MAX_NUDGES = 1

NUDGE_MESSAGE = (
    "You said you would fetch more data but did not call a tool. "
    "Either call a tool now, or give your final answer using only the data "
    "you already have, without promising further lookups."
)


def route_after_agent(state: AgentState) -> str:
    """Decide what runs after the model replies. Returns a node name or END."""
    last = state["messages"][-1]
    # The model asked for tools: go and run them.
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    # Plain text reply. If it promises a lookup it never made (and we haven't
    # nudged too often yet), send it back; otherwise it is the final answer.
    # .text is the answer text only: Claude's replies also hold thinking blocks.
    if PROMISE_PATTERN.search(last.text) and state.get("nudges", 0) < MAX_NUDGES:
        return "nudge"
    return END


# Adds a message as if the user had written it, and counts the nudge.
# Only these two keys change; LangGraph merges them into the state.
def nudge_node(state: AgentState) -> dict:
    return {
        "messages": [HumanMessage(NUDGE_MESSAGE)],
        "nudges": state.get("nudges", 0) + 1,
    }


# Claude models that accept `fallbacks`: if a safety check declines a request,
# Anthropic retries it on a suitable other model instead of returning a refusal.
CLAUDE_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
}

REFUSAL_REPLY = "Sorry, I can't help with that request."

# Prompt caching (Claude only). Every model call re-sends everything: the tool
# list, the system prompt and the whole conversation so far. Claude can keep
# the part it has already processed for 5 minutes and re-read it for about a
# tenth of the normal input price (writing it costs 1.25x, once).
# Caching works on prefixes: a request reuses the cache only up to the first
# byte that differs. So the beginning must stay identical between calls:
# never put changing values (dates, ids) in SYSTEM_PROMPT, never change TOOLS
# mid-conversation, and never edit earlier messages.
CACHE_MARKER = {"type": "ephemeral"}  # "ephemeral" = the 5-minute cache


def system_message() -> SystemMessage:
    """The system prompt as the first message of every model call.

    For Claude it carries a cache marker: "cache everything up to here", which
    is the tool list and the system prompt. They are the same on every call,
    so after the first call they are always read from the cache.
    """
    if settings.llm_provider == "anthropic":
        block = {"type": "text", "text": SYSTEM_PROMPT, "cache_control": CACHE_MARKER}
        return SystemMessage(content=[block])
    return SystemMessage(SYSTEM_PROMPT)


# Anthropic's "context editing" (a beta feature): once a request gets long,
# Anthropic removes old tool results from it before the model reads it, keeping
# the newest few. The model sees a note where each one was. This is Claude's
# version of agent/trim.py, done on Anthropic's side so the prompt cache and
# Claude's thinking blocks stay valid.
CONTEXT_EDITING_BETA = "context-management-2025-06-27"
TOOL_RESULTS_KEPT = 3


def clear_old_tool_results() -> dict:
    return {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {
                    "type": "input_tokens",
                    "value": settings.claude_clear_tool_results_at_tokens,
                },
                "keep": {"type": "tool_uses", "value": TOOL_RESULTS_KEPT},
            }
        ]
    }


def build_llm() -> BaseChatModel:
    """The chat model the agent talks to, picked by LLM_PROVIDER."""
    if settings.llm_provider == "anthropic":
        # model_kwargs are added to every request as-is. A top-level
        # `cache_control` turns on automatic caching: Claude moves a cache marker
        # to the end of the conversation on each call, so the next call (the
        # model reading a tool result, or a follow-up question) reads all of it
        # from the cache. This complements the fixed marker in system_message().
        request_options: dict = {"cache_control": CACHE_MARKER}
        betas = [CONTEXT_EDITING_BETA]
        if settings.anthropic_model in CLAUDE_FALLBACK_MODELS:
            betas.append("server-side-fallback-2026-07-01")
            request_options["fallbacks"] = "default"
        # Only pass the key if one is configured; otherwise the client reads
        # ANTHROPIC_API_KEY from the environment. No temperature: current Claude
        # models reject it (effort is the setting that matters instead).
        key = settings.anthropic_api_key.get_secret_value()
        return ChatAnthropic(
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            effort=settings.anthropic_effort,
            default_request_timeout=settings.llm_timeout_s,
            max_retries=1,
            model_kwargs=request_options,
            context_management=clear_old_tool_results(),
            betas=betas,
            **({"api_key": key} if key else {}),
        )
    # vLLM speaks the same HTTP API as OpenAI, so the OpenAI client works as
    # long as base_url points at vLLM. vLLM caches repeated prompt beginnings by
    # itself (prefix caching, see infrastructure/vllm/vllm_start.sh), so no
    # markers are needed. stream_usage=True: report token counts when streaming
    # too, for the usage log (agent/usage.py).
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_s,
        max_retries=1,
        stream_usage=True,
    )


def build_graph():
    """Wire up the nodes and edges. Called once at server startup."""
    # bind_tools attaches the tool schemas to every request, which is how the
    # model learns which tools exist.
    llm = build_llm().bind_tools(TOOLS)
    system = system_message()

    # The model step. The system prompt is added on every call instead of being
    # stored in the state, so it can't pile up or get lost.
    async def agent_node(state: AgentState) -> dict:
        history = state["messages"]
        if settings.llm_provider == "vllm":
            # The local model's window is small: leave out old tool results if
            # needed (only from this request; the state keeps them). The budget
            # covers the system prompt too, so its share is taken off first.
            budget = settings.vllm_context_budget_tokens - estimate_tokens([system])
            history = trim_tool_results(history, budget)
        messages = [system, *history]
        # response is an AIMessage: either text, or tool_calls, or both.
        response = await llm.ainvoke(messages)
        # Claude's safety checks can decline a request (rare, and usually
        # rescued by the fallback above); its reply is then empty.
        if response.response_metadata.get("stop_reason") == "refusal":
            response = AIMessage(REFUSAL_REPLY)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    # handle_tool_errors=True: any exception a tool raises becomes a ToolMessage
    # the model can read, instead of crashing the run.
    # ToolNode is a prebuilt node: it reads the tool_calls on the last message,
    # runs each matching tool, and appends one ToolMessage per call.
    graph.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    graph.add_node("nudge", nudge_node)

    graph.add_edge(START, "agent")
    # The list gives every possible destination, so LangGraph can draw and check the graph.
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "nudge", END])
    graph.add_edge("tools", "agent")
    graph.add_edge("nudge", "agent")

    # compile() checks the wiring and returns a runnable object with
    # ainvoke() and astream_events() (used in server.py).
    return graph.compile()
