"""The agent loop as an explicit LangGraph state machine.

    START -> agent --(tool calls?)--> tools -> agent -> ... -> END
                  \--(plain text)----------------------------> END
"""

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.state import AgentState
from agent.tools import TOOLS
from config import settings

SYSTEM_PROMPT = """You are Squad Scout, a Fantasy Premier League assistant.

Rules:
- Only answer questions about Fantasy Premier League and Premier League football.
- Never guess player stats, prices or fixtures. Always fetch them with your tools.
- Look players up with search_players before using any player_id or team_id.
- If a tool returns an ERROR, tell the user briefly that the data is unavailable
  right now. Do not invent numbers.
- Keep answers short and cite the numbers you used.
"""


def build_graph():
    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    ).bind_tools(TOOLS)

    async def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    # handle_tool_errors=True: any exception a tool raises becomes a ToolMessage
    # the model can read, instead of crashing the run.
    graph.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))

    graph.add_edge(START, "agent")
    # tools_condition routes to the node named "tools" if the last AI message
    # has tool calls, otherwise to END.
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    return graph.compile()
