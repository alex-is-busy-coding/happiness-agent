import pytest

from context_panel import AgentContexts

REPLY = """```story
{"title": "Tea & Sleep", "hook": "Does a late cup cost you rest?", "theme": "daily life",
 "answer": "Late tea goes with worse sleep.", "stats": [{"value": "41%", "label": "sleep badly"},
 {"value": "", "label": "dropped: no value"}, {"value": "2x", "label": "as likely"},
 {"value": "7", "label": "third"}, {"value": "9", "label": "a fourth, over the limit"}],
 "followups": ["What about coffee?", "And naps?", "By age?", "A fourth, over the limit"]}
```

## late cups, short nights

Tea after eight goes with **41%** sleeping badly (GSS 1972-2024). Countries differ too (WHR 2026).

```chart
{"type": "bar", "labels": ["a", "b", "c"], "series": [{"name": "x", "data": [1, 2, 3]}]}
```
"""


@pytest.fixture
def reply() -> str:
    return REPLY


@pytest.fixture
def contexts() -> AgentContexts:
    """A small run, recorded the way the hooks record one: the lead sends a researcher, who runs two queries."""
    log = AgentContexts()
    system, question = {"role": "system", "content": "lead"}, {"role": "user", "content": "Question: tea?"}
    send = {"role": "assistant", "content": None, "tool_calls": [{"name": "research", "arguments": {"task": "tea"}}]}
    log.set("Lead", [system, question])
    log.add("Lead", [send])

    brief = [{"role": "system", "content": "researcher"}, {"role": "user", "content": "Task: tea"}]
    first = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"name": "run_sql", "arguments": {"sql": "SELECT 1"}}],
    }
    second = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"name": "run_sql", "arguments": {"sql": "SELECT 2"}}],
    }
    log.set("Researcher 1", brief)
    log.add("Researcher 1", [first])
    log.set("Researcher 1", [*brief, first, {"role": "tool", "content": "| one |\n|---|\n| 1 |"}])
    log.add("Researcher 1", [second])
    log.set(
        "Researcher 1",
        [
            *brief,
            first,
            {"role": "tool", "content": "| one |\n|---|\n| 1 |"},
            second,
            {"role": "tool", "content": "| two |\n|---|\n| 2 |"},
        ],
    )
    log.add("Researcher 1", [{"role": "assistant", "content": "- tea findings"}])

    log.set("Lead", [system, question, send, {"role": "tool", "content": "- tea findings"}])
    log.add("Lead", [{"role": "assistant", "content": "DONE"}])
    return log
