"""Graph state schema: what flows between nodes on every step."""

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # add_messages appends new messages instead of overwriting the list,
    # so each node only returns the messages it produced.
    messages: Annotated[list[AnyMessage], add_messages]

    # Domain keys. Optional for now; the manager's FPL entry id once you add
    # "analyse my team" style tools.
    fpl_team_id: NotRequired[int | None]
