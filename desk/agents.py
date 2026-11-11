"""The desk's three kinds of agent, and one run through them.

A Lead plans and fans out Researchers, which query the database in parallel, each in a fresh
context. The Lead goes back for more when the findings leave a gap. A Writer then turns the
findings into a story. The Lead may instead ask the user to clarify.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

from agents import Agent, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded

from context_panel import PanelHooks
from desk import prompts
from desk.config import (
    LEAD_MAX_TURNS,
    MAX_RESEARCHERS,
    MODEL,
    MODEL_SETTINGS,
    NEW_RESEARCHER_CUTOFF,
    QUERY_BUDGET,
    RESEARCH_DEADLINE,
    RESEARCHER_MAX_TURNS,
)
from desk.database import is_read_only, run_query
from desk.story import build_story, clarify_markdown, collect_tool_calls, parse_clarify

set_tracing_disabled(True)  # traces upload to OpenAI by default, and the desk runs on Gemini


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: PanelHooks
    started: float = field(default_factory=time.monotonic)
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


# --- The researcher: answers one task by querying the database ---


class BudgetedSql:
    """What run_sql does: read-only queries, and only so many of them. The prompt asks a
    researcher for restraint; this enforces it, which is what keeps a run to a few minutes."""

    def __init__(self, budget: int = QUERY_BUDGET):
        self.left = budget

    async def run(self, sql: str) -> str:
        if self.left <= 0:
            return "Query budget spent. Write your findings now, from the results you already have."
        if not is_read_only(sql):
            return "Only SELECT queries are allowed."
        try:
            result = await asyncio.wait_for(asyncio.to_thread(run_query, sql), timeout=30)
        except Exception as e:
            # The model cannot see an exception. Return something it can fix its query from.
            return f"Query failed: {type(e).__name__}: {e}"
        self.left -= 1  # a failed query costs nothing
        return result + (f"\n\n({self.left} queries left.)" if self.left <= 2 else "")


def sql_tool():
    """A run_sql tool with its own budget: each researcher gets a fresh one."""
    queries = BudgetedSql()

    @function_tool
    async def run_sql(sql: str) -> str:
        """Run one read-only SQL query (DuckDB dialect) against the happiness database
        and get the result back as a table.

        Args:
            sql: A single SELECT (or WITH ... SELECT) statement.
        """
        return await queries.run(sql)

    return run_sql


researcher = Agent(name="Researcher", instructions=prompts.RESEARCHER, model=MODEL, model_settings=MODEL_SETTINGS)


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
    if time.monotonic() - run.started > NEW_RESEARCHER_CUTOFF:
        return "No time left for more research. Reply DONE."
    run.researchers += 1
    agent = researcher.clone(name=f"Researcher {run.researchers}", tools=[sql_tool()])
    run.tasks[agent.name] = task
    run.hooks.emit({"type": "task", "agent": agent.name, "task": task})
    try:
        result = await Runner.run(agent, f"Task: {task}", context=run, hooks=run.hooks, max_turns=RESEARCHER_MAX_TURNS)
    except MaxTurnsExceeded:
        run.hooks.emit({"type": "filed", "agent": agent.name, "ok": False})
        return "That researcher ran out of turns without reporting. Narrow the task if you retry."
    except asyncio.CancelledError:  # the run hit its deadline with this researcher still out
        run.hooks.emit({"type": "filed", "agent": agent.name, "ok": False})
        raise
    run.hooks.emit({"type": "filed", "agent": agent.name, "ok": True})
    run.findings.append(f"### Task: {task}\n\n{result.final_output}")
    return str(result.final_output)


# --- The lead plans, delegates and checks for gaps; the writer makes one call at the end ---

lead = Agent(name="Lead", instructions=prompts.LEAD, model=MODEL, model_settings=MODEL_SETTINGS, tools=[research])
writer = Agent(name="Writer", instructions=prompts.WRITER, model=MODEL, model_settings=MODEL_SETTINGS)


def conversation(history: list[dict]) -> str:
    """Earlier turns, as the lead and the writer read them ahead of a follow-up."""
    if not history:
        return ""
    lines = ["Earlier in this conversation:"]
    for turn in history[-3:]:
        said = "The lead asked them to clarify" if turn["kind"] == "clarify" else "The desk answered"
        lines += [f'The user asked: "{turn["question"]}"', f"{said}: {turn['reply']}"]
    return "\n".join(lines) + "\n\n"


def as_turn(question: str, outcome: dict) -> dict:
    """What to remember of a finished run, for the next question in the same conversation."""
    if outcome["type"] == "clarify":
        return {
            "question": question,
            "kind": "clarify",
            "reply": clarify_markdown(outcome["message"], outcome["options"]),
        }
    story = outcome["story"]
    return {"question": question, "kind": "report", "reply": story["answer"] or story["title"]}


async def deep_research(question: str, hooks: PanelHooks, history: list[dict] | None = None) -> dict:
    """Run one question through the desk.

    Returns {"type": "report", "story"} or {"type": "clarify", "message", "options"}.
    """
    run = Research(hooks=hooks)
    prompt = f"{conversation(history or [])}Question: {question}"
    try:
        result = await asyncio.wait_for(
            Runner.run(lead, prompt, context=run, hooks=hooks, max_turns=LEAD_MAX_TURNS), timeout=RESEARCH_DEADLINE
        )
        verdict = str(result.final_output).strip()
    except Exception:
        # Out of time or turns, or the lead's model call failed. Findings already in are worth a story.
        if not run.findings:
            raise
        verdict = "DONE"
    if verdict.startswith("CLARIFY:"):
        message, options = parse_clarify(verdict)
        return {"type": "clarify", "message": message, "options": options}

    findings = "\n\n---\n\n".join(run.findings) or "No findings."
    brief = f"{prompt}\n\nFindings:\n\n{findings}"
    try:
        reply = await Runner.run(writer, brief, hooks=hooks)
    except Exception:
        reply = await Runner.run(writer, brief, hooks=hooks)  # once more: the research is done and paid for
    calls = collect_tool_calls(hooks.contexts)
    story = build_story(question, str(reply.final_output), run.tasks, calls, time.monotonic() - run.started)
    return {"type": "report", "story": story}
