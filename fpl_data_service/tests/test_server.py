"""End to end: a real gRPC server on a real (local) port, called through a real channel."""

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

from fpl_data.server import start_server
from squadscout.v1 import fpl_pb2 as pb
from squadscout.v1 import fpl_pb2_grpc as pb_grpc


@pytest.fixture
async def channel(service):
    server, port = await start_server(service, "127.0.0.1:0")  # port 0 = any free port
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    yield channel
    await channel.close()  # close the client side first, so the server has nothing left to cancel
    await server.stop(grace=1)


async def test_a_call_goes_through(channel):
    stub = pb_grpc.FplDataServiceStub(channel)
    response = await stub.SearchPlayers(pb.SearchPlayersRequest(name="saka", limit=1))
    assert response.players[0].web_name == "Saka"


async def test_service_errors_become_grpc_statuses(channel):
    stub = pb_grpc.FplDataServiceStub(channel)
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.GetPlayerRecentForm(pb.GetPlayerRecentFormRequest(player_id=99999))
    assert caught.value.code() == grpc.StatusCode.NOT_FOUND
    assert "SearchPlayers" in caught.value.details()  # the recovery hint reaches the caller


async def test_unexpected_errors_become_internal(channel, service, monkeypatch):
    def broken(self, name, limit):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(
        "fpl_data.db.players.search", lambda store, name, limit: broken(None, name, limit)
    )
    stub = pb_grpc.FplDataServiceStub(channel)
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.SearchPlayers(pb.SearchPlayersRequest(name="saka"))
    assert caught.value.code() == grpc.StatusCode.INTERNAL
    assert "exploded" not in caught.value.details()  # internals aren't leaked to the caller


async def test_every_rpc_in_the_proto_is_served(channel):
    """Each RPC is implemented (a forgotten one would answer UNIMPLEMENTED)."""
    stub = pb_grpc.FplDataServiceStub(channel)
    calls = {
        "SearchPlayers": pb.SearchPlayersRequest(name="saka"),
        "FindPlayers": pb.FindPlayersRequest(),
        "GetFixtures": pb.GetFixturesRequest(team_id=1),
        "GetPlayerFixtures": pb.GetPlayerFixturesRequest(player_id=12),
        "GetPlayerGameweekStats": pb.GetPlayerGameweekStatsRequest(player_id=12, gameweek=1),
        "GetPlayerRecentForm": pb.GetPlayerRecentFormRequest(player_id=12),
        "GetTransferTrends": pb.GetTransferTrendsRequest(),
        "GetPlayerNews": pb.GetPlayerNewsRequest(player_id=1),
        "GetTeamNews": pb.GetTeamNewsRequest(team_id=1),
        "GetManagerTeam": pb.GetManagerTeamRequest(team_id=6),
        "EvaluateTransfers": pb.EvaluateTransfersRequest(
            team_id=6, player_out_ids=[124], player_in_ids=[286]
        ),
        "SuggestTransfers": pb.SuggestTransfersRequest(team_id=6),
    }
    declared = {m.name for m in pb.DESCRIPTOR.services_by_name["FplDataService"].methods}
    assert declared == set(calls)
    for name, request in calls.items():
        await getattr(stub, name)(request)  # raises if the status isn't OK


async def test_health_check(channel):
    stub = health_pb2_grpc.HealthStub(channel)
    response = await stub.Check(health_pb2.HealthCheckRequest(service=""))
    assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test_reflection_lists_the_service(channel):
    """grpcurl depends on reflection to discover the RPCs."""
    stub = reflection_pb2_grpc.ServerReflectionStub(channel)

    async def requests():
        yield reflection_pb2.ServerReflectionRequest(list_services="")

    async for response in stub.ServerReflectionInfo(requests()):
        names = [s.name for s in response.list_services_response.service]
    assert "squadscout.v1.FplDataService" in names


async def test_every_call_is_logged_with_its_outcome(channel, caplog):
    import logging

    stub = pb_grpc.FplDataServiceStub(channel)
    with caplog.at_level(logging.INFO, logger="fpl_data.server"):
        await stub.SearchPlayers(pb.SearchPlayersRequest(name="saka"))
        with pytest.raises(grpc.aio.AioRpcError):
            await stub.GetPlayerRecentForm(pb.GetPlayerRecentFormRequest(player_id=99999))

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("rpc=")]
    assert lines[0].startswith("rpc=search_players code=OK ms=")
    assert lines[1].startswith("rpc=get_player_recent_form code=NOT_FOUND ms=")
