"""What the agent is told before the user's question: their FPL team.

When the app knows the user's team id, the server loads the team itself and
puts a compact summary in front of the question. Otherwise the model would
have to ask for the team with a tool call first, and every tool call costs a
whole extra model call (which re-sends everything so far).

The summary goes into the user's first message, not into the system prompt:
the system prompt must stay identical for every user so Claude's prompt cache
can be shared (see CACHE_MARKER in graph.py).
"""

import logging

import grpc
from langchain_core.messages import HumanMessage

from agent import format
from agent.tools import stub
from config import settings
from squadscout.v1 import fpl_pb2 as pb

logger = logging.getLogger(__name__)


async def team_summary(team_id: int) -> str:
    """The user's squad as compact text (~3,000 characters), or a sentence saying
    why it couldn't be loaded. Never raises: the question can still be answered
    without the team."""
    try:
        team = await stub().GetManagerTeam(
            pb.GetManagerTeamRequest(team_id=team_id), timeout=settings.grpc_timeout_s
        )
    except grpc.aio.AioRpcError as err:
        logger.warning("couldn't preload team %d: %s %s", team_id, err.code().name, err.details())
        return (
            f"My FPL team id is {team_id}, but the app couldn't load my team "
            f"({err.code().name}: {err.details()})."
        )
    return format.manager_team(team)


def first_message(question: str, team: str | None) -> HumanMessage:
    """The user's question, with their team in front of it when we have it.

    The labels tell the model which part the app added and which part the
    user typed.
    """
    if team is None:
        return HumanMessage(question)
    return HumanMessage(f"[My FPL team, loaded by the app]\n{team}\n\n[My question]\n{question}")
