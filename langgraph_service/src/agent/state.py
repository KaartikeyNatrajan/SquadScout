"""Graph state schema: what flows between nodes on every step."""

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


# The "state" is the shared data every graph node reads. A node returns only
# the keys it wants to change, and LangGraph merges that into the state.
# TypedDict = a normal dict with declared key types (for editors/type checkers).
class AgentState(TypedDict):
    # The whole conversation: user question, model replies (including tool
    # calls), tool results, nudges.
    # add_messages appends new messages instead of overwriting the list,
    # so each node only returns the messages it produced.
    messages: Annotated[list[AnyMessage], add_messages]

    # Domain keys. Optional for now; the manager's FPL entry id once you add
    # "analyse my team" style tools.
    fpl_team_id: NotRequired[int | None]

    # How many times the graph has pushed the model to act on a
    # "let me fetch..." promise. Capped so the loop always ends.
    nudges: NotRequired[int]
