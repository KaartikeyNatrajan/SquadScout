"""FastAPI entrypoint: the only HTTP surface in the system.

Run (from langgraph_service/):  uv run uvicorn server:app --app-dir src --port 8080
Interactive docs:               http://localhost:8080/docs
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
    app.state.graph = build_graph()
    yield
    await close_channel()


app = FastAPI(title="Squad Scout", version="0.1.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, examples=["How has Saka been playing lately?"])
    fpl_team_id: int | None = None


class ChatResponse(BaseModel):
    reply: str


def _initial_state(req: ChatRequest) -> dict:
    return {"messages": [HumanMessage(req.message)], "fpl_team_id": req.fpl_team_id}


def _run_config() -> dict:
    return {"recursion_limit": settings.graph_recursion_limit}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run the graph to completion and return only the final answer."""
    result = await app.state.graph.ainvoke(_initial_state(req), config=_run_config())
    return ChatResponse(reply=result["messages"][-1].content)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Stream progress as Server-Sent Events: tool activity plus answer tokens."""

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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
