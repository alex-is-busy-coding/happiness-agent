"""The happiness desk: a deep-research agent over GSS, WHR and ATUS survey data.

A Lead splits the question into tasks and fans out Researchers that explore the
data in parallel by writing SQL against a local DuckDB built offline by
prepare_data.py. A Writer turns their findings into a short, charted story.
"""

import asyncio
import itertools
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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
QUERY_BUDGET = 6  # successful queries per researcher
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
    tasks: dict[str, str] = field(default_factory=dict)  # researcher name -> its task


class DeskHooks(PanelHooks):
    """Also emits each SQL query as a researcher issues it, for the page's live research board."""

    async def on_llm_end(self, context, agent, response):
        await super().on_llm_end(context, agent, response)
        for item in response.output:
            if getattr(item, "type", None) == "function_call" and item.name == "run_sql":
                try:
                    sql = json.loads(item.arguments).get("sql", "")
                except (TypeError, ValueError):
                    sql = ""
                self.emit({"type": "query", "agent": agent.name, "sql": sql})


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


def sql_tool():
    """A run_sql tool with its own query budget: each researcher gets a fresh one. The prompt
    asks for restraint; this enforces it, which is what keeps a run inside a few minutes."""
    used = 0

    @function_tool
    async def run_sql(sql: str) -> str:
        """Run one read-only SQL query (DuckDB dialect) against the happiness database
        and get the result back as a table.

        Args:
            sql: A single SELECT (or WITH ... SELECT) statement.
        """
        nonlocal used
        if used >= QUERY_BUDGET:
            return "Query budget spent. Write your findings now, from the results you already have."
        if not sql.strip().rstrip(";").lower().startswith(("select", "with")):
            return "Only SELECT queries are allowed."
        try:
            result = await asyncio.wait_for(asyncio.to_thread(_query, sql.rstrip().rstrip(";")), timeout=30)
        except Exception as e:
            return f"Query failed: {type(e).__name__}: {e}"
        used += 1  # a failed query costs nothing
        left = QUERY_BUDGET - used
        return result + (f"\n\n({left} queries left.)" if left <= 2 else "")

    return run_sql


# --- The researcher: answers one task by querying the database ---

researcher = Agent(
    name="Researcher",
    instructions=f"""\
You answer one research task by querying a DuckDB database of happiness survey data with run_sql.

{DATA_CONTEXT}

Method:
1. You have a hard budget of {QUERY_BUDGET} queries; after that run_sql stops answering.
   Plan queries that each answer a whole sub-question (GROUP BY beats many single filters).
2. Start simple: one query that gets at the heart of the task. Check the row counts: a cell
   built on under ~100 respondents is noise, say so or coarsen the grouping.
3. Refine: control for an obvious confounder (age, year range, income) with a second grouping
   or filter when it could flip the story.
4. Reply with at most 8 bullets of findings. Every bullet states numbers from your results and
   names its source in parentheses: (GSS 1972-2024), (WHR 2026) or (ATUS WB 2021).
   For coded answers report shares (percent "very happy"), not the mean of the codes: an
   average of 1.7 on a 1-3 code tells a reader nothing.
5. After the bullets, include the one or two most important result tables verbatim under
   "Key tables:", each preceded by the SQL that produced it in a ```sql fence.
6. If the data cannot answer part of your task (variable not asked, too few respondents,
   wrong unit of analysis), say exactly that in a final bullet starting "LIMIT:". Only limits
   that affect this task: not tables you didn't need.
Never invent numbers: every number you report must appear in a query result.""",
    model=MODEL,
    model_settings=SETTINGS,
)


@function_tool
async def research(ctx: RunContextWrapper[Research], task: str) -> str:
    """Send a researcher to answer one task by querying the happiness database.
    Returns its findings with the numbers and source tables behind them.

    Args:
        task: One specific, answerable thing to find out, phrased as a question.
    """
    run = ctx.context
    if run.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    run.researchers += 1
    agent = researcher.clone(name=f"Researcher {run.researchers}", tools=[sql_tool()])
    run.tasks[agent.name] = task
    run.hooks.emit({"type": "task", "agent": agent.name, "task": task})
    try:
        result = await Runner.run(agent, f"Task: {task}", context=run, hooks=run.hooks, max_turns=14)
    except MaxTurnsExceeded:
        run.hooks.emit({"type": "filed", "agent": agent.name, "ok": False})
        return "That researcher ran out of query budget without reporting. Narrow the task if you retry."
    run.hooks.emit({"type": "filed", "agent": agent.name, "ok": True})
    run.findings.append(f"### Task: {task}\n\n{result.final_output}")
    return str(result.final_output)


# --- The lead: clarifies, plans, fans out, and checks for gaps ---

lead = Agent(
    name="Lead",
    instructions=f"""\
You lead a research team exploring what makes a happy life, using three survey datasets:

{DATA_CONTEXT}

The user asks a question about happiness, wellbeing, or life satisfaction.

First decide if you can research it. Reply with a clarification instead of researching when:
- it is too vague to turn into data questions (e.g. "tell me about happiness"), or ambiguous
  in a way that changes the analysis; or
- it is outside what these datasets cover (e.g. needs medical, genetic, or non-survey data,
  or a table marked NOT BUILT).
A clarification is exactly this shape and nothing else:

CLARIFY: <one or two sentences: what you need to know, or what the data can't cover>
- <a sharper question these datasets can answer>
- <another>
- <another>

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

THEMES = ("money", "relationships", "daily life", "world", "time")

writer = Agent(
    name="Writer",
    instructions="""\
You write for "the happiness desk", a small data publication: curious, plain-spoken, a little
wry, never breathless. You get a reader's question and the research team's findings. Use only
the findings. Your reply has exactly two parts.

PART 1: a metadata block, first thing in the reply.

```story
{"title": "money & happiness", "hook": "Does it ever stop mattering?", "theme": "money", "answer": "...", "stats": [{"value": "39%", "label": "of the richest fifth say they are very happy"}], "followups": ["...", "...", "..."]}
```

- title: 2 to 4 words, lowercase, like a magazine cover line.
- hook: one curious sentence to sit under the title, at most 14 words.
- theme: "money", "relationships", "daily life", "world" (countries) or "time" (trends).
- answer: the answer in one or two sentences a reader could quote, with its most important number.
- stats: the 3 numbers from the findings that most directly answer the question. value is
  short ("39%", "7.8", "2x"); label says what it counts in at most 10 plain words that make
  sense to someone who has not read the story (no "slope", "quintile", "coefficient", and
  never the average of an answer code such as "1.7 happiness score").
- followups: 3 follow-up questions this same data could answer next, at most 12 words each.

PART 2: the story, in markdown.

- 3 to 5 short sections. Each starts with a lowercase "## " heading that states its point
  ("## the gap never closes"), not its topic.
- Paragraphs of at most 3 sentences. Write to the reader as "you" now and then, not in every
  paragraph, and never open with "You might". Vary how sentences start.
- Round for reading: percentages to whole numbers (39%, not 39.10%), scores to one decimal
  (7.8), in the prose and in the metadata block alike.
- When a paragraph turns on one statistic, bold it with **double asterisks**. Never bold a year.
- Every sentence that states a fact ends with its source in parentheses: (GSS 1972-2024),
  (WHR 2026) or (ATUS WB 2021). Use only numbers that appear in the findings.
- Include 2 to 4 charts, each as a fenced block right after the paragraph it supports:

```chart
{"type": "bar", "title": "Married people are almost twice as likely to be very happy", "y_label": "share saying they are very happy", "unit": "%", "labels": ["Married", "Widowed", "Never married"], "series": [{"name": "very happy", "data": [39.1, 23.3, 20.8]}], "highlight": "Married", "note": "Weighted shares, all survey years."}
```

  - type "bar": compares 3 to 8 categories; labels at most 3 words each; optional "highlight"
    names the one label the story is about. Two numbers are a sentence, not a chart.
  - type "line": a trend; labels are the years or periods in order.
  - type "scatter": two measures; each series' data is [{"x": 1, "y": 2, "label": "Finland"}, ...],
    at most 40 points, and it needs "x_label".
  - title states the takeaway as a sentence. y_label names what is measured. unit is "%" or
    "" . At most 3 series, named in plain words (no answer codes like "(1-3)"). note is one line of method or caveat. Numbers come from the
    findings' key tables, with percentages as 39.1, not 0.391.
- If a finding has a LIMIT bullet that bears on the reader's question, end with a section
  "## what this data can't tell you": 2 to 4 plain sentences, only limits a reader of this
  story would care about, no citations needed.
- Call every measure what the data measures, never something broader. Perceiving little
  corruption is not "trust in government"; having someone to count on is not a "safety net";
  a country's average is not a person. Do not explain a finding with a cause that is not in
  the findings.
- These are correlations. No causal verbs for what the data shows: not "lifts", "boosts",
  "drives", "makes", "leads to", "thanks to", in the metadata or the story. Say "goes with",
  "is linked to", "report". Do not blame a change on an event (a pandemic, a recession) the
  findings did not measure: say when it happened, and pass on any caution the findings give
  about how the survey changed.
- Report, don't advise: no "you should", no "choose", no recommendations for how to live.
- No "profoundly", "pivotal", "paving the way", "it is worth noting", or exclamation marks.""",
    model=MODEL,
    model_settings=SETTINGS,
)


# --- From the writer's reply to a story ---

STORY_BLOCK = re.compile(r"```story\s*(\{.*?\})\s*```", re.DOTALL)
SOURCE_NAMES = ("GSS", "WHR", "ATUS")


def parse_story(text: str) -> tuple[dict, str]:
    """Split the writer's reply into its metadata and its markdown body. A reply without a
    readable metadata block is all body."""
    match = STORY_BLOCK.search(text)
    if not match:
        return {}, text.strip()
    try:
        meta = json.loads(match.group(1))
    except ValueError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), (text[: match.start()] + text[match.end() :]).strip()


def _text(value, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def build_story(question: str, reply: str, run: Research, contexts: AgentContexts, seconds: float) -> dict:
    meta, body = parse_story(reply)
    stats = [
        {"value": _text(s.get("value"), 12), "label": _text(s.get("label"), 90)}
        for s in (meta.get("stats") or [])[:3]
        if isinstance(s, dict) and _text(s.get("value"), 12)
    ]
    sql_calls = [c for c in collect_tool_calls(contexts) if c["name"] == "run_sql"]
    trail = [
        {
            "agent": agent,
            "task": task,
            "queries": [
                {"sql": c["arguments"].get("sql", "") if isinstance(c["arguments"], dict) else str(c["arguments"]),
                 "result": c.get("result", "")}
                for c in sql_calls if c["agent"] == agent
            ],
        }
        for agent, task in run.tasks.items()
    ]
    return {
        "id": uuid.uuid4().hex[:8],
        "question": question,
        "title": _text(meta.get("title"), 60).lower() or question.rstrip("?").lower()[:60],
        "hook": _text(meta.get("hook"), 140),
        "theme": meta.get("theme") if meta.get("theme") in THEMES else "default",
        "answer": _text(meta.get("answer"), 500),
        "stats": stats,
        "followups": [_text(f, 120) for f in (meta.get("followups") or [])[:3] if _text(f, 120)],
        "body": body,
        "sources": [name for name in SOURCE_NAMES if f"({name} " in body or f", {name} " in body],
        "trail": trail,
        "created": date.today().isoformat(),
        "seconds": round(seconds),
    }


def story_markdown(story: dict) -> str:
    """A story as one markdown document, for /chat."""
    parts = [f"# {story['title']}"]
    if story["hook"]:
        parts.append(f"*{story['hook']}*")
    if story["answer"]:
        parts.append(f"> {story['answer']}")
    parts.append(story["body"])
    return "\n\n".join(parts)


def parse_clarify(reply: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in reply.removeprefix("CLARIFY:").strip().splitlines() if line.strip()]
    options = [line.lstrip("-* ").strip() for line in lines if line.startswith(("-", "*"))]
    message = " ".join(line for line in lines if not line.startswith(("-", "*")))
    return message, options[:4]


async def deep_research(question: str, hooks: PanelHooks) -> dict:
    """Returns {"type": "report", "story"} or {"type": "clarify", "message", "options"}."""
    started = time.monotonic()
    run = Research(hooks=hooks)
    try:
        result = await Runner.run(lead, f"Question: {question}", context=run, hooks=hooks, max_turns=12)
        lead_output = str(result.final_output).strip()
    except Exception:
        # The lead ran out of turns or its model call failed. Findings already in are worth a story.
        if not run.findings:
            raise
        lead_output = "DONE"
    if lead_output.startswith("CLARIFY:"):
        message, options = parse_clarify(lead_output)
        return {"type": "clarify", "message": message, "options": options}
    findings = "\n\n---\n\n".join(run.findings) or "No findings."
    prompt = f"Question: {question}\n\nFindings:\n\n{findings}"
    try:
        reply = await Runner.run(writer, prompt, hooks=hooks)
    except Exception:
        reply = await Runner.run(writer, prompt, hooks=hooks)  # one more try: the research is done and paid for
    story = build_story(question, str(reply.final_output), run, hooks.contexts, time.monotonic() - started)
    return {"type": "report", "story": story}


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


# --- The archive: stories researched ahead of time by make_stories.py, plus this process's own ---

FEATURED = sorted((json.loads(p.read_text()) for p in (HERE / "stories").glob("*.json")), key=lambda s: s["number"])
stories: dict[str, dict] = {s["id"]: s for s in FEATURED}
story_numbers = itertools.count(len(FEATURED) + 1)


def keep(story: dict) -> dict:
    story["number"] = next(story_numbers)
    stories[story["id"]] = story
    return story


def data_summary() -> dict:
    """What the page's masthead says the desk has to work with, counted from the database."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        summary = {}
        if "gss" in tables:
            people, first, last = con.execute("SELECT COUNT(*), MIN(year), MAX(year) FROM gss").fetchone()
            summary["gss"] = {"people": people, "first": int(first), "last": int(last)}
        if "whr" in tables:
            countries, first, last = con.execute("SELECT COUNT(DISTINCT country), MIN(year), MAX(year) FROM whr").fetchone()
            summary["whr"] = {"countries": countries, "first": int(first), "last": int(last)}
        if "atus_wb" in tables:
            episodes, people = con.execute("SELECT COUNT(*), COUNT(DISTINCT case_id) FROM atus_wb").fetchone()
            summary["atus_wb"] = {"episodes": episodes, "people": people}
        return summary
    finally:
        con.close()


DATA_SUMMARY = data_summary()

# --- FastAPI app ---

sessions: dict[str, AgentContexts] = {}

app = FastAPI()
add_context_panel(app, sessions)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/meta")
def meta():
    return DATA_SUMMARY


@app.get("/guesses")
def guesses():
    return FileResponse(HERE / "data" / "guesses.json")


@app.get("/stories")
def list_stories():
    card = ("id", "number", "question", "title", "hook", "theme", "stats", "sources", "created")
    return [{key: story[key] for key in card} for story in FEATURED]


@app.get("/stories/{story_id}")
def get_story(story_id: str):
    if story_id not in stories:
        raise HTTPException(status_code=404, detail="No such story.")
    return stories[story_id]


@app.get("/run")
async def run(question: str):
    """Research one question and stream what happens as Server-Sent Events."""
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = DeskHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            outcome = await deep_research(question, hooks)
            if outcome["type"] == "report":
                keep(outcome["story"])
            queue.put_nowait(outcome)
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


@app.post("/chat")
async def chat(request: ChatRequest):
    contexts = AgentContexts()
    hooks = PanelHooks(contexts, lambda _event: None)
    try:
        outcome = await deep_research(request.message, hooks)
    except Exception as e:
        return {"response": f"The research run failed: {type(e).__name__}: {str(e)[:500]}", "tool_calls": []}
    if outcome["type"] == "clarify":
        response = "\n".join([outcome["message"], *(f"- {option}" for option in outcome["options"])])
    else:
        response = story_markdown(outcome["story"])
    return {"response": response, "tool_calls": collect_tool_calls(contexts)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
