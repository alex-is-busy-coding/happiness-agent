"""From the writer's reply to a story the page can render, and what a run did along the way."""

import json
import re
import uuid
from datetime import date

from context_panel import AgentContexts

THEMES = ("money", "relationships", "daily life", "world", "time")

# The datasets a story can cite, as /chat reports them.
SOURCES = {
    "GSS": {
        "name": "General Social Survey 1972-2024 (NORC)",
        "url": "https://gss.norc.org/",
        "updated": "every two years",
    },
    "WHR": {
        "name": "World Happiness Report 2026 (Gallup World Poll)",
        "url": "https://worldhappiness.report/",
        "updated": "every year",
    },
    "ATUS": {
        "name": "American Time Use Survey Well-Being Module 2021 (BLS)",
        "url": "https://www.bls.gov/tus/",
        "updated": "occasionally",
    },
}

STORY_BLOCK = re.compile(r"```story\s*(\{.*?\})\s*```", re.DOTALL)


def collect_tool_calls(contexts: AgentContexts) -> list[dict]:
    """Every agent's tool calls in the order they happened, each paired with its result."""
    calls: list[dict] = []
    waiting: dict[str, list[dict]] = {}  # agent -> its calls that have no result yet
    for event in contexts.events:
        if event["type"] != "message" or "from" in event:  # skip copies shown in another agent's context
            continue
        agent, message = event["agent"], event["message"]
        for call in message.get("tool_calls") or []:
            entry = {"agent": agent, "name": call.get("name"), "args": call.get("arguments")}
            calls.append(entry)
            waiting.setdefault(agent, []).append(entry)
        if message.get("role") == "tool" and waiting.get(agent):
            waiting[agent].pop(0)["result"] = str(message.get("content", ""))[:2000]
    return calls


def parse_story(reply: str) -> tuple[dict, str]:
    """Split the writer's reply into its metadata and its markdown body. A reply without a
    readable metadata block is all body."""
    match = STORY_BLOCK.search(reply)
    if not match:
        return {}, reply.strip()
    try:
        meta = json.loads(match.group(1))
    except ValueError:
        meta = {}
    body = (reply[: match.start()] + reply[match.end() :]).strip()
    return (meta if isinstance(meta, dict) else {}), body


def _text(value, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def build_story(question: str, reply: str, tasks: dict[str, str], calls: list[dict], seconds: float) -> dict:
    """The story as the page and the archive hold it. `tasks` maps each researcher to its task,
    and `calls` is the run's tool calls: together they make the story's paper trail."""
    meta, body = parse_story(reply)
    stats = [
        {"value": _text(stat.get("value"), 12), "label": _text(stat.get("label"), 90)}
        for stat in (meta.get("stats") or [])[:3]
        if isinstance(stat, dict) and _text(stat.get("value"), 12)
    ]
    queries = [call for call in calls if call["name"] == "run_sql"]
    trail = [
        {
            "agent": agent,
            "task": task,
            "queries": [
                {
                    "sql": call["args"].get("sql", "") if isinstance(call["args"], dict) else str(call["args"]),
                    "result": call.get("result", ""),
                }
                for call in queries
                if call["agent"] == agent
            ],
        }
        for agent, task in tasks.items()
    ]
    return {
        "id": uuid.uuid4().hex[:8],
        "question": question,
        "title": _text(meta.get("title"), 60).lower() or question.rstrip("?").lower()[:60],
        "hook": _text(meta.get("hook"), 140),
        "theme": meta.get("theme") if meta.get("theme") in THEMES else "default",
        "answer": _text(meta.get("answer"), 500),
        "stats": stats,
        "followups": [_text(item, 120) for item in (meta.get("followups") or [])[:3] if _text(item, 120)],
        "body": body,
        "sources": [name for name in SOURCES if f"({name} " in body or f", {name} " in body],
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
    """The lead's clarification: what it needs to know, and the sharper questions it offers."""
    lines = [line.strip() for line in reply.removeprefix("CLARIFY:").strip().splitlines() if line.strip()]
    options = [line.lstrip("-* ").strip() for line in lines if line.startswith(("-", "*"))]
    message = " ".join(line for line in lines if not line.startswith(("-", "*")))
    return message, options[:4]


def clarify_markdown(message: str, options: list[str]) -> str:
    return "\n".join([message, *(f"- {option}" for option in options)])
