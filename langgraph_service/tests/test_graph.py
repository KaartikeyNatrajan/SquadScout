"""Smoke tests that need neither vLLM nor the Go service running."""

from fastapi.testclient import TestClient


def test_graph_compiles():
    from agent.graph import build_graph

    graph = build_graph()
    assert {"agent", "tools"} <= set(graph.get_graph().nodes)


def test_health():
    from server import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
