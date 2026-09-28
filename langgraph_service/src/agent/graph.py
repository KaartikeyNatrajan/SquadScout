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

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.state import AgentState
from agent.tools import TOOLS
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
- Keep answers short and cite the numbers you used.

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
    text = last.content if isinstance(last.content, str) else ""
    if PROMISE_PATTERN.search(text) and state.get("nudges", 0) < MAX_NUDGES:
        return "nudge"
    return END


# Adds a message as if the user had written it, and counts the nudge.
# Only these two keys change; LangGraph merges them into the state.
def nudge_node(state: AgentState) -> dict:
    return {
        "messages": [HumanMessage(NUDGE_MESSAGE)],
        "nudges": state.get("nudges", 0) + 1,
    }


def build_graph():
    """Wire up the nodes and edges. Called once at server startup."""
    # vLLM speaks the same HTTP API as OpenAI, so the OpenAI client works as
    # long as base_url points at vLLM. bind_tools attaches the tool schemas to
    # every request, which is how the model learns which tools exist.
    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    ).bind_tools(TOOLS)

    # The model step. The system prompt is added on every call instead of being
    # stored in the state, so it can't pile up or get lost.
    async def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        # response is an AIMessage: either text, or tool_calls, or both.
        response = await llm.ainvoke(messages)
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
