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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from agent import context
from agent.graph import build_graph
from agent.tools import close_channel
from agent.usage import UsageTracker
from config import settings

# Show our own INFO lines (the model in use, tokens and cost per chat) in the
# terminal next to uvicorn's. Python prints only warnings unless told otherwise.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# What the client is told when the agent can't finish. The details go to the log.
_TOO_MANY_STEPS = (
    "The agent took too many steps without reaching an answer. Try a simpler question."
)
_AGENT_FAILED = "The agent failed (is the model reachable?). See the server log for details."


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code before `yield` runs once at server startup, code after it at shutdown.
    # Building the graph once and storing it on app.state means every request
    # reuses the same compiled graph.
    app.state.graph = build_graph()
    # Which model answers is set in .env (LLM_PROVIDER); say so, since a wrong
    # setting otherwise only shows up as failing chats.
    if settings.llm_provider == "anthropic":
        logger.info(
            "model: Claude (%s, effort %s)", settings.anthropic_model, settings.anthropic_effort
        )
    else:
        logger.info("model: vLLM at %s (%s)", settings.llm_base_url, settings.llm_model)
    yield
    await close_channel()  # close the gRPC connection to the data service


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
# just the user's question, with their team in front of it when the request
# gives a team id (agent/context.py: saves the model a tool round trip).
async def _initial_state(req: ChatRequest) -> dict:
    team = await context.team_summary(req.fpl_team_id) if req.fpl_team_id else None
    return {
        "messages": [context.first_message(req.message, team)],
        "fpl_team_id": req.fpl_team_id,
    }


# recursion_limit caps the number of graph steps, so a model that keeps
# calling tools forever gets stopped with an error instead of looping.
# callbacks: LangChain calls these on every model call inside the graph; the
# UsageTracker logs each call's tokens and cost.
def _run_config(tracker: UsageTracker) -> dict:
    return {"recursion_limit": settings.graph_recursion_limit, "callbacks": [tracker]}


def _new_tracker() -> UsageTracker:
    """A fresh token counter for one chat request, priced for the model in use."""
    model = settings.anthropic_model if settings.llm_provider == "anthropic" else settings.llm_model
    return UsageTracker(model)


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
    tracker = _new_tracker()
    try:
        state = await _initial_state(req)
        result = await app.state.graph.ainvoke(state, config=_run_config(tracker))
    except GraphRecursionError as err:
        logger.warning("chat stopped at the step limit: %s", err)
        raise HTTPException(status_code=504, detail=_TOO_MANY_STEPS) from err
    except Exception as err:  # model down or erroring, a bug in a node, ...
        logger.exception("chat failed")
        raise HTTPException(status_code=502, detail=_AGENT_FAILED) from err
    finally:
        tracker.log_total()  # also for failed requests: they cost money too
    # .text, not .content: with Claude, content also holds thinking blocks.
    return ChatResponse(reply=result["messages"][-1].text)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Stream progress as Server-Sent Events: tool activity plus answer tokens."""

    # events() is an async generator: each `yield` sends one chunk to the client
    # right away, instead of building the whole response first.
    # astream_events reports everything that happens inside the graph; we only
    # forward three kinds of event and ignore the rest.
    # Note: this forwards tokens from EVERY model call, including a reply that
    # the nudge node later sends back, so the client can see two drafts.

    # The HTTP status (200) is sent before the graph runs, so a failure part-way
    # can't change it: it's reported as an "error" event, and "done" always ends
    # the stream so the client knows to stop waiting.
    async def events() -> AsyncIterator[str]:
        tracker = _new_tracker()
        try:
            state = await _initial_state(req)
            async for event in app.state.graph.astream_events(
                state, config=_run_config(tracker), version="v2"
            ):
                kind = event["event"]
                if kind == "on_tool_start":
                    yield _sse(
                        "tool_start", {"tool": event["name"], "input": event["data"].get("input")}
                    )
                elif kind == "on_tool_end":
                    yield _sse("tool_end", {"tool": event["name"]})
                elif kind == "on_chat_model_stream":
                    token = event["data"]["chunk"].text  # answer text only, no thinking
                    if token:
                        yield _sse("token", {"text": token})
        except GraphRecursionError as err:
            logger.warning("chat stream stopped at the step limit: %s", err)
            yield _sse("error", {"message": _TOO_MANY_STEPS})
        except Exception:
            logger.exception("chat stream failed")
            yield _sse("error", {"message": _AGENT_FAILED})
        tracker.log_total()
        yield _sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream")


# Server-Sent Events format: an "event:" line, a "data:" line, then a blank
# line marking the end of the event.
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
