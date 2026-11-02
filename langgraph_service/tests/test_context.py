"""Preloading the user's team (agent/context.py). The data service is faked."""

import grpc
import pytest

from agent import context
from squadscout.v1 import fpl_pb2 as pb


class FakeStub:
    """Stands in for the gRPC client; returns `team` or raises `error`."""

    def __init__(self, team=None, error=None):
        self.team, self.error, self.requests = team, error, []

    async def GetManagerTeam(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.team


class FakeRpcError(grpc.aio.AioRpcError):
    def __init__(self):
        super().__init__(grpc.StatusCode.NOT_FOUND, None, None, details="no FPL team 999")


@pytest.fixture
def team() -> pb.GetManagerTeamResponse:
    return pb.GetManagerTeamResponse(team_id=6, team_name="Bol Uwd Utd", manager_name="Osian")


async def test_team_summary_formats_the_team(monkeypatch, team):
    fake = FakeStub(team=team)
    monkeypatch.setattr(context, "stub", lambda: fake)

    summary = await context.team_summary(6)

    assert summary.startswith("Bol Uwd Utd (team id 6), manager Osian")
    assert fake.requests[0].team_id == 6


async def test_a_team_that_cant_be_loaded_is_explained_not_raised(monkeypatch):
    monkeypatch.setattr(context, "stub", lambda: FakeStub(error=FakeRpcError()))

    summary = await context.team_summary(999)

    assert summary == (
        "My FPL team id is 999, but the app couldn't load my team (NOT_FOUND: no FPL team 999)."
    )


def test_first_message_labels_the_team_and_the_question():
    message = context.first_message("Who should I captain?", "TEAM")

    assert (
        message.content
        == "[My FPL team, loaded by the app]\nTEAM\n\n[My question]\nWho should I captain?"
    )


def test_first_message_without_a_team_is_just_the_question():
    assert context.first_message("Hi", None).content == "Hi"
