"""LangGraph tools = thin gRPC clients for fpl_data_service.

The LLM decides which tool to call from each function's name, type hints and
docstring, so write docstrings for the model, not for humans.

The `squadscout.v1` imports are generated code: run `make proto` first.
"""

import grpc
from google.protobuf.json_format import MessageToJson
from langchain_core.tools import tool

from config import settings
from squadscout.v1 import fpl_pb2 as pb  # message classes (SearchPlayersRequest, ...)
from squadscout.v1 import fpl_pb2_grpc as pb_grpc  # the client "stub" class

# A channel is the network connection to the Go service. It is created on
# first use and then shared by every tool call.
_channel: grpc.aio.Channel | None = None


def _stub() -> pb_grpc.FplDataServiceStub:
    """One shared channel per process; gRPC multiplexes calls over it.

    A stub is a generated object whose methods (stub.SearchPlayers(...)) look
    like local functions but send the request to the Go server.
    """
    global _channel
    if _channel is None:
        _channel = grpc.aio.insecure_channel(settings.fpl_grpc_addr)
    return pb_grpc.FplDataServiceStub(_channel)


async def close_channel() -> None:
    global _channel
    if _channel is not None:
        await _channel.close()
        _channel = None


# Tools must return text for the model, so protobuf responses become JSON.
def _to_json(msg) -> str:
    return MessageToJson(
        msg,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,  # keep 0 goals / 0 points visible
    )


def _error(action: str, err: grpc.aio.AioRpcError) -> str:
    # Returned to the LLM as the tool result so it can apologise or retry,
    # instead of the exception crashing the graph (Phase 5).
    return f"ERROR while {action}: {err.code().name} - {err.details()}"


# @tool turns the function into a LangChain tool: its name, argument types and
# docstring become a JSON schema that is sent to the model with every request.
# When the model asks for a tool call, ToolNode (in graph.py) runs the function
# with the model's arguments and gives the returned string back to the model.
@tool
async def search_players(name: str) -> str:
    """Find Premier League players in Fantasy Premier League by name.

    Use this first whenever the user mentions a player, to get the player's id,
    team, position, price, total points and availability status. Partial names
    work (e.g. "saka", "salah"). Returns up to 5 matches as JSON.
    """
    try:
        # `await` pauses here until Go replies (or the timeout hits), letting
        # the server handle other work meanwhile.
        resp = await _stub().SearchPlayers(
            pb.SearchPlayersRequest(name=name, limit=5), timeout=settings.grpc_timeout_s
        )
        return _to_json(resp)
    except grpc.aio.AioRpcError as err:
        return _error(f"searching players for '{name}'", err)


@tool
async def get_fixtures(team_id: int, next_n: int = 5) -> str:
    """Get upcoming fixtures for a Premier League team, with difficulty ratings (1 easy - 5 hard).

    team_id comes from search_players results. Use this for questions about
    upcoming matches, fixture difficulty, or whether to transfer a player in or out.
    """
    try:
        resp = await _stub().GetFixtures(
            pb.GetFixturesRequest(team_id=team_id, next_n=next_n), timeout=settings.grpc_timeout_s
        )
        return _to_json(resp)
    except grpc.aio.AioRpcError as err:
        return _error(f"fetching fixtures for team {team_id}", err)


@tool
async def get_player_gameweek_stats(player_id: int, gameweek: int) -> str:
    """Get one player's stats for a single gameweek: minutes, goals, assists,
    clean sheets, bonus, FPL points, xG and xA.

    player_id comes from search_players results.
    """
    try:
        resp = await _stub().GetPlayerGameweekStats(
            pb.GetPlayerGameweekStatsRequest(player_id=player_id, gameweek=gameweek),
            timeout=settings.grpc_timeout_s,
        )
        return _to_json(resp)
    except grpc.aio.AioRpcError as err:
        return _error(f"fetching stats for player {player_id} in GW{gameweek}", err)


# TOOLS is the list the model is told about (graph.py: bind_tools and ToolNode).
# Only expose tools the model can actually use well. get_player_gameweek_stats
# needs a gameweek number the model can't know yet, so it tempts the model to
# promise a lookup it never makes. It returns once the Go side can supply
# recent form without the model picking gameweeks.
# get_fixtures stays (even while unimplemented) to test "data unavailable" handling.
TOOLS = [search_players, get_fixtures]
