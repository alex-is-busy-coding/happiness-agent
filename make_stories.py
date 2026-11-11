"""Offline: research the archive's featured stories with the live agent and save them to stories/.

The front page opens these instantly, so a first-time visitor reads a finished story before
deciding to wait for one of their own.

Run with: uv run make_stories.py        every question that has no story file yet
          uv run make_stories.py 3 5    redo stories number 3 and 5
"""

import asyncio
import json
import re
import sys

from context_panel import AgentContexts
from desk.agents import DeskHooks, deep_research
from desk.config import STORIES_DIR as OUT

QUESTIONS = [
    "Does more money make people happier, and does it ever stop mattering?",
    "Are married people happier than single people?",
    "How much does seeing friends matter for happiness?",
    "Why do Nordic countries top the happiness rankings?",
    "Are Americans getting less happy over time?",
    "Is it better to be healthy or wealthy?",
]


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower().replace("&", "and")).strip("-")


async def write_story(number: int, question: str, limit: asyncio.Semaphore) -> None:
    async with limit:
        print(f"#{number:03d} researching: {question}")
        outcome = await deep_research(question, DeskHooks(AgentContexts(), lambda _event: None))
    if outcome["type"] != "report":
        print(f"#{number:03d} SKIPPED, the lead asked for clarification: {outcome['message']}")
        return
    story = outcome["story"]
    story["number"] = number
    story["id"] = slug(story["title"])
    for old in OUT.glob(f"{number:03d}-*.json"):
        old.unlink()
    path = OUT / f"{number:03d}-{story['id']}.json"
    path.write_text(json.dumps(story, indent=2))
    print(f"#{number:03d} wrote {path.name}: {story['title']} ({story['seconds']} s)")


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    wanted = {int(arg) for arg in sys.argv[1:]}
    todo = [
        (number, question)
        for number, question in enumerate(QUESTIONS, start=1)
        if number in wanted or (not wanted and not list(OUT.glob(f"{number:03d}-*.json")))
    ]
    limit = asyncio.Semaphore(3)
    await asyncio.gather(*(write_story(number, question, limit) for number, question in todo))


if __name__ == "__main__":
    asyncio.run(main())
