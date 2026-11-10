"""What makes a happy life? A deep-research agent over GSS, WHR and ATUS survey data.

A Lead splits the question into tasks and fans out Researchers that explore the
data in parallel by writing SQL against a local DuckDB built offline by
prepare_data.py. A Writer turns their findings into a cited report with charts.
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from context_panel import AgentContexts, PanelHooks, add_context_panel

load_dotenv(Path(__file__).parent / ".env")
set_tracing_disabled(True)

MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
# vertex_project: set GOOGLE_CLOUD_PROJECT locally; on Cloud Run the metadata server supplies it.
SETTINGS = ModelSettings(
    extra_args={
        "vertex_location": "global",
        "vertex_project": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        "timeout": 90,
        "num_retries": 2,
    }
)

MAX_RESEARCHERS = 6
MAX_ROWS = 50  # rows of a query result the model gets to see

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "happiness.duckdb"
# Schema notes built offline by prepare_data.py, from the live database.
DATA_CONTEXT = (HERE / "data" / "context.md").read_text()


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: PanelHooks
    researchers: int = 0
    findings: list[str] = field(default_factory=list)


# --- Tools ---


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return "NULL" if value is None else str(value)


def _query(sql: str) -> str:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    note = ""
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS]
        note = f"\n(More than {MAX_ROWS} rows. Aggregate more if you need the rest.)"
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines) + note


@function_tool
async def run_sql(sql: str) -> str:
    """Run one read-only SQL query (DuckDB dialect) against the happiness database
    and get the result back as a table.

    Args:
        sql: A single SELECT (or WITH ... SELECT) statement.
    """
    if not sql.strip().rstrip(";").lower().startswith(("select", "with")):
        return "Only SELECT queries are allowed."
    try:
        return await asyncio.wait_for(asyncio.to_thread(_query, sql.rstrip().rstrip(";")), timeout=30)
    except Exception as e:
        return f"Query failed: {type(e).__name__}: {e}"


# --- The researcher: answers one task by querying the database ---

researcher = Agent(
    name="Researcher",
    instructions=f"""\
You answer one research task by querying a DuckDB database of happiness survey data with run_sql.

{DATA_CONTEXT}

Method:
1. You have a HARD BUDGET of 6 run_sql calls; after the 6th you must write your findings.
   Plan queries that each answer a whole sub-question (GROUP BY beats many single filters).
2. Start simple: one query that gets at the heart of the task. Check the row counts: a cell
   built on under ~100 respondents is noise, say so or coarsen the grouping.
3. Refine: control for an obvious confounder (age, year range, income) with a second grouping
   or filter when it could flip the story.
4. Reply with at most 8 bullets of findings. Every bullet states numbers from your results and
   names its source in parentheses: (GSS 1972-2024), (WHR 2026) or (ATUS WB 2021).
5. After the bullets, include the one or two most important result tables verbatim under
   "Key tables:", each preceded by the SQL that produced it in a ```sql fence.
6. If the data cannot answer part of the task (variable not asked, too few respondents,
   wrong unit of analysis), say exactly that in a final bullet starting "LIMIT:".
Never invent numbers: every number you report must appear in a query result.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[run_sql],
)


@function_tool
async def research(ctx: RunContextWrapper[Research], task: str) -> str:
    """Send a researcher to answer one task by querying the happiness database.
    Returns its findings with the numbers and source tables behind them.

    Args:
        task: One specific, answerable thing to find out, phrased as a question.
    """
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    ctx.context.researchers += 1
    agent = researcher.clone(name=f"Researcher {ctx.context.researchers}")
    try:
        result = await Runner.run(agent, f"Task: {task}", context=ctx.context, hooks=ctx.context.hooks, max_turns=14)
    except MaxTurnsExceeded:
        return "That researcher ran out of query budget without reporting. Narrow the task if you retry."
    ctx.context.findings.append(f"### Task: {task}\n\n{result.final_output}")
    return str(result.final_output)


# --- The lead: clarifies, plans, fans out, and checks for gaps ---

lead = Agent(
    name="Lead",
    instructions=f"""\
You lead a research team exploring what makes a happy life, using three survey datasets:

{DATA_CONTEXT}

The user asks a question about happiness, wellbeing, or life satisfaction.

First decide if you can research it:
- If it is too vague to turn into data questions (e.g. "tell me about happiness") or is
  ambiguous in a way that changes the analysis, reply with exactly one line starting
  "CLARIFY: " followed by the question the user should answer, and nothing else.
- If it is entirely outside what these datasets cover (e.g. needs medical, genetic, or
  non-survey data), reply "CLARIFY: " explaining what the data can and cannot cover and
  offering a nearby question it can answer.

Otherwise:
1. Split the question into 2 to 4 independent research tasks that play to different datasets
   (momentary experience -> atus_wb, individual lives over time -> gss, countries -> whr).
   Call research once per task, all in the same turn, so the researchers run in parallel.
2. Read the findings. Only if a core part of the question is still unanswered, call research
   again for just that part. At most two such extra rounds; most questions need none.
3. When the question is covered, reply DONE and nothing else: a writer writes the report.

You never query the data yourself: researchers do. Your job is to plan, delegate, and judge
coverage.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[research],
)

# --- The writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a report in markdown that answers the question, using only the findings you're given.

- Start with a 2-3 sentence answer a reader could quote, then a short section per theme.
- Every sentence that states a fact ends with its source in parentheses: (GSS 1972-2024),
  (WHR 2026) or (ATUS WB 2021). Use only numbers that appear in the findings.
- Include 2 to 4 charts where a picture beats a sentence, each as a fenced block:

```chart
{"type": "bar", "title": "...", "x_label": "...", "y_label": "...", "labels": ["a", "b"], "series": [{"name": "...", "data": [1, 2]}]}
```

  "type" is "bar" (categories), "line" (trends over time) or "scatter" (two measures; then
  each series' data is [{"x": 1, "y": 2, "label": "Finland"}, ...]). At most 3 series per
  chart. Take the numbers from the findings' key tables.
- If any finding has a LIMIT bullet, end with a section "What this data can't tell us"
  that states those limits plainly.
- Write plain, specific sentences. No "profoundly", "pivotal", "paving the way", or
  "it is worth noting". Correlations are correlations: never say the data proves causation.""",
    model=MODEL,
    model_settings=SETTINGS,
)


async def deep_research(question: str, hooks: PanelHooks) -> dict:
    """Returns {"type": "report"|"clarify", "markdown": ...}."""
    run = Research(hooks=hooks)
    result = await Runner.run(lead, f"Question: {question}", context=run, hooks=hooks, max_turns=12)
    lead_output = str(result.final_output).strip()
    if lead_output.startswith("CLARIFY:"):
        return {"type": "clarify", "markdown": lead_output.removeprefix("CLARIFY:").strip()}
    findings = "\n\n---\n\n".join(run.findings) or "No findings."
    report = await Runner.run(writer, f"Question: {question}\n\nFindings:\n\n{findings}", hooks=hooks)
    return {"type": "report", "markdown": str(report.final_output)}


# --- FastAPI app ---

sessions: dict[str, AgentContexts] = {}

app = FastAPI()
add_context_panel(app, sessions)


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/run")
async def run(question: str):
    """Research one question and stream what happens as Server-Sent Events."""
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = PanelHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            outcome = await deep_research(question, hooks)
            queue.put_nowait({"type": outcome["type"], "markdown": outcome["markdown"]})
        except Exception as e:
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("report", "clarify", "error"):
                    break
        finally:
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


# --- /chat: the Project 1 response shape ---


class ChatRequest(BaseModel):
    message: str


def collect_tool_calls(contexts: AgentContexts) -> list[dict]:
    """Every agent's tool calls in the order they happened, paired with their results."""
    calls: list[dict] = []
    open_calls: dict[str, list[dict]] = {}  # agent -> calls awaiting a result
    for event in contexts.events:
        if event["type"] != "message" or "from" in event:  # skip copies shown in other contexts
            continue
        agent, message = event["agent"], event["message"]
        for call in message.get("tool_calls") or []:
            entry = {"agent": agent, "name": call.get("name"), "arguments": call.get("arguments")}
            calls.append(entry)
            open_calls.setdefault(agent, []).append(entry)
        if message.get("role") == "tool" and open_calls.get(agent):
            entry = open_calls[agent].pop(0)
            entry["result"] = str(message.get("content", ""))[:2000]
    return calls


@app.post("/chat")
async def chat(request: ChatRequest):
    contexts = AgentContexts()
    hooks = PanelHooks(contexts, lambda _event: None)
    try:
        outcome = await deep_research(request.message, hooks)
    except Exception as e:
        return {"response": f"The research run failed: {type(e).__name__}: {str(e)[:500]}", "tool_calls": []}
    return {"response": outcome["markdown"], "tool_calls": collect_tool_calls(contexts)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
