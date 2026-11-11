"""The stories the desk has filed: the featured ones shipped in stories/, plus this process's own."""

import itertools
import json
from pathlib import Path

from desk.limits import Recent

CARD_FIELDS = ("id", "number", "question", "title", "hook", "theme", "stats", "sources", "created")


class Archive:
    def __init__(self, directory: Path, keep: int = 200):
        files = sorted(directory.glob("*.json"))
        self.featured = sorted((json.loads(path.read_text()) for path in files), key=lambda story: story["number"])
        self._featured = {story["id"]: story for story in self.featured}
        self._filed = Recent(keep)  # stories filed since startup, so their links work while it is up
        self._numbers = itertools.count(len(self.featured) + 1)

    def cards(self) -> list[dict]:
        """What the front page needs to draw the shelf of featured stories."""
        return [{field: story[field] for field in CARD_FIELDS} for story in self.featured]

    def get(self, story_id: str) -> dict | None:
        return self._featured.get(story_id) or self._filed.get(story_id)

    def file(self, story: dict) -> dict:
        """Number a newly written story and keep it."""
        story["number"] = next(self._numbers)
        self._filed[story["id"]] = story
        return story
