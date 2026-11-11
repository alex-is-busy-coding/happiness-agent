"""The happiness desk: a deep-research agent over happiness survey data, as a web app.

The agents live in desk/. This file is the HTTP surface: the page, the archive, a run
streamed as Server-Sent Events, and /chat for programs.
"""

import asyncio
import json
import os
import re
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from context_panel import AgentContexts, PanelHooks, add_context_panel
from desk import database
from desk.agents import DeskHooks, as_turn, deep_research
from desk.archive import Archive
from desk.config import DATA_DIR, MAX_CONCURRENT_RUNS, MAX_RUNS_PER_DAY, ROOT, STORIES_DIR
from desk.limits import Recent, RunBudget
from desk.story import SOURCES, clarify_markdown, collect_tool_calls, story_markdown

archive = Archive(STORIES_DIR)
budget = RunBudget(MAX_CONCURRENT_RUNS, MAX_RUNS_PER_DAY)
data_summary = database.summary()
# In memory, in one process: enough for the page's context panel and for follow-up questions.
contexts: Recent = Recent(50)  # run id -> every agent's context, for the context panel
conversations: Recent = Recent(200)  # /chat session id -> its earlier turns

app = FastAPI(title="the happiness desk")
add_context_panel(app, contexts)


@app.middleware("http")
async def forgive_doubled_slashes(request, call_next):
    """A client that joins "https://host/" and "/chat" asks for "//chat". Answer it as "/chat"."""
    request.scope["path"] = re.sub(r"/{2,}", "/", request.scope["path"])
    return await call_next(request)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/meta")
def meta():
    return data_summary


@app.get("/guesses")
def guesses():
    return FileResponse(DATA_DIR / "guesses.json")


@app.get("/stories")
def list_stories():
    return archive.cards()


@app.get("/stories/{story_id}")
def get_story(story_id: str):
    story = archive.get(story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="No such story.")
    return story


def parse_history(raw: str | None) -> list[dict]:
    """The page's earlier turns, from a query string: trusted for nothing, so trimmed hard."""
    try:
        turns = json.loads(raw) if raw else []
    except ValueError:
        return []
    if not isinstance(turns, list):
        return []
    return [
        {
            "question": str(turn.get("question", ""))[:300],
            "kind": "clarify" if turn.get("kind") == "clarify" else "report",
            "reply": str(turn.get("reply", ""))[:800],
        }
        for turn in turns[-3:]
        if isinstance(turn, dict)
    ]


@app.get("/run")
async def run(question: str, history: str | None = None):
    """Research one question and stream what happens as Server-Sent Events."""
    refusal = budget.start()
    run_id = str(uuid.uuid4())
    contexts[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = DeskHooks(contexts[run_id], queue.put_nowait)

    async def work():
        try:
            outcome = await deep_research(question, hooks, parse_history(history))
            if outcome["type"] == "report":
                archive.file(outcome["story"])
            queue.put_nowait(outcome)
        except Exception as e:
            # Auth, quota, a model that won't stop: show it on the page, not as a dropped stream.
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})
        finally:
            budget.finish()

    task = None if refusal else asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"
            if refusal:
                yield f"data: {json.dumps({'type': 'busy', 'message': refusal})}\n\n"
                return
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("report", "clarify", "error"):
                    break
        finally:
            if task:
                task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


# --- /chat: the Project 1 request and response, with each tool call's agent added ---


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class Source(BaseModel):
    name: str
    url: str
    updated: str  # how often the publisher refreshes it


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]  # {"agent", "name", "args", "result"}, the lead's and every researcher's
    sources: list[Source] = []


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    refusal = budget.start()
    if refusal:
        body = ChatResponse(response=refusal, session_id=session_id, tool_calls=[])
        return JSONResponse(status_code=429, content=body.model_dump())
    history = conversations.get(session_id, [])
    run_contexts = AgentContexts()
    try:
        outcome = await deep_research(request.message, PanelHooks(run_contexts, lambda _event: None), history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"The run failed: {type(e).__name__}: {str(e)[:500]}") from e
    finally:
        budget.finish()
    conversations[session_id] = [*history, as_turn(request.message, outcome)][-3:]

    if outcome["type"] == "clarify":
        response, cited = clarify_markdown(outcome["message"], outcome["options"]), []
    else:
        story = archive.file(outcome["story"])
        response, cited = story_markdown(story), story["sources"]
    return ChatResponse(
        response=response,
        session_id=session_id,
        tool_calls=collect_tool_calls(run_contexts),
        sources=[Source(**SOURCES[name]) for name in cited],
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
