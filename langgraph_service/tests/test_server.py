"""The HTTP endpoints report agent failures instead of crashing."""

import pytest
from fastapi.testclient import TestClient
from langgraph.errors import GraphRecursionError

import server


class FailingGraph:
    """Stands in for the compiled graph; raises `error` from both ways of running it."""

    def __init__(self, error: Exception):
        self.error = error

    async def ainvoke(self, *args, **kwargs):
        raise self.error

    async def astream_events(self, *args, **kwargs):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": type("C", (), {"content": "Hi"})()},
        }
        raise self.error


@pytest.fixture
def client():
    # Not used as a context manager, so the lifespan (which builds the real graph) doesn't run.
    return TestClient(server.app)


@pytest.mark.parametrize(
    ("error", "status"),
    [(GraphRecursionError("too many steps"), 504), (ConnectionError("vLLM down"), 502)],
)
def test_chat_reports_failures(client, error, status):
    server.app.state.graph = FailingGraph(error)

    response = client.post("/chat", json={"message": "hi"})

    assert response.status_code == status
    assert response.json()["detail"]


def test_chat_stream_sends_an_error_event_then_done(client):
    server.app.state.graph = FailingGraph(ConnectionError("vLLM down"))

    body = client.post("/chat/stream", json={"message": "hi"}).text

    assert body.index("event: token") < body.index("event: error") < body.index("event: done")
