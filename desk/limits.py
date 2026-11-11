"""A budget on runs. Each one spends a few dozen model calls, and the service is on the internet."""

from collections import OrderedDict
from datetime import date


class RunBudget:
    """At most `concurrent` runs at once and `per_day` in a day, counted in this process."""

    def __init__(self, concurrent: int, per_day: int):
        self.concurrent = concurrent
        self.per_day = per_day
        self.running = 0
        self.day = date.today()
        self.started_today = 0

    def start(self) -> str | None:
        """Claim a run. Returns None when it may go ahead, or the reason it may not."""
        if date.today() != self.day:
            self.day, self.started_today = date.today(), 0
        if self.started_today >= self.per_day:
            return "The desk has filed all the stories it can afford today. Come back tomorrow, or read the archive."
        if self.running >= self.concurrent:
            return "Every researcher is out on a story right now. Try again in a minute or two."
        self.running += 1
        self.started_today += 1
        return None

    def finish(self) -> None:
        self.running = max(0, self.running - 1)


class Recent(OrderedDict):
    """A dict that remembers only its newest `size` entries, so a long-lived server stays small."""

    def __init__(self, size: int):
        super().__init__()
        self.size = size

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)
