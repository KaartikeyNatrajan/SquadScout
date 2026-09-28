"""FastAPI entrypoint: the only HTTP surface in the system.

Run (from langgraph_service/):  uv run uvicorn server:app --app-dir src --port 8080
Interactive docs:               http://localhost:8080/docs

Request flow: a client POSTs {"message": "..."} -> we wrap it in the graph's
starting state -> run the LangGraph agent (agent/graph.py) -> return the answer,
either all at once (/chat) or piece by piece (/chat/stream).

The endpoints are `async def`: while one request waits on the model or on
gRPC, the server can work on other requests.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agent.graph import build_graph
from agent.tools import close_channel
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code before `yield` runs once at server startup, code after it at shutdown.
    # Building the graph once and storing it on app.state means every request
    # reuses the same compiled graph.
    app.state.graph = build_graph()
    yield
    await close_channel()  # close the gRPC connection to the Go service


app = FastAPI(title="Squad Scout", version="0.1.0", lifespan=lifespan)


# Pydantic models describe the JSON shape of requests and responses. FastAPI
# validates incoming JSON against them (a missing or empty "message" gets an
# automatic 422 error) and uses them for the /docs page.
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, examples=["How has Saka been playing lately?"])
    fpl_team_id: int | None = None


class ChatResponse(BaseModel):
    reply: str


# The graph's starting state (see agent/state.py): the conversation so far is
# just the user's question.
def _initial_state(req: ChatRequest) -> dict:
    return {"messages": [HumanMessage(req.message)], "fpl_team_id": req.fpl_team_id}


# recursion_limit caps the number of graph steps, so a model that keeps
# calling tools forever gets stopped with an error instead of looping.
def _run_config() -> dict:
    return {"recursion_limit": settings.graph_recursion_limit}


# Decorators like @app.get / @app.post register the function below them as the
# handler for that HTTP method and path.
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run the graph to completion and return only the final answer."""
    # ainvoke runs the whole graph (model -> tools -> model ...) to the end and
    # returns the final state. The last message in it is the model's answer.
    result = await app.state.graph.ainvoke(_initial_state(req), config=_run_config())
    return ChatResponse(reply=result["messages"][-1].content)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Stream progress as Server-Sent Events: tool activity plus answer tokens."""

    # events() is an async generator: each `yield` sends one chunk to the client
    # right away, instead of building the whole response first.
    # astream_events reports everything that happens inside the graph; we only
    # forward three kinds of event and ignore the rest.
    # Note: this forwards tokens from EVERY model call, including a reply that
    # the nudge node later sends back, so the client can see two drafts.

    async def events() -> AsyncIterator[str]:
        async for event in app.state.graph.astream_events(
            _initial_state(req), config=_run_config(), version="v2"
        ):
            kind = event["event"]
            if kind == "on_tool_start":
                yield _sse("tool_start", {"tool": event["name"], "input": event["data"].get("input")})
            elif kind == "on_tool_end":
                yield _sse("tool_end", {"tool": event["name"]})
            elif kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    yield _sse("token", {"text": token})
        yield _sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream")


# Server-Sent Events format: an "event:" line, a "data:" line, then a blank
# line marking the end of the event.
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
