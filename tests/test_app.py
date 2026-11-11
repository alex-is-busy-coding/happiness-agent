import json

import pytest
from fastapi.testclient import TestClient

import app as web
from desk.limits import RunBudget
from desk.story import build_story


@pytest.fixture
def client(monkeypatch, reply):
    """The app with the agents stubbed out: `asked` records what each run was given."""
    asked = []

    async def fake_research(question, hooks, history=None):
        asked.append({"question": question, "history": history})
        if "vague" in question:
            return {"type": "clarify", "message": "Which part?", "options": ["Money?", "Friends?"]}
        hooks.emit({"type": "task", "agent": "Researcher 1", "task": "tea"})
        return {"type": "report", "story": build_story(question, reply, {}, [], 1)}

    monkeypatch.setattr(web, "deep_research", fake_research)
    monkeypatch.setattr(web, "budget", RunBudget(concurrent=2, per_day=100))
    test_client = TestClient(web.app)
    test_client.asked = asked
    return test_client


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_the_page_and_its_data_are_served(client):
    assert "the happiness desk" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/meta").json()["gss"]["people"] > 70_000
    assert len(client.get("/guesses").json()) > 5


def test_the_archive_lists_cards_and_serves_whole_stories(client):
    cards = client.get("/stories").json()
    assert len(cards) >= 6 and {"id", "number", "title", "hook", "theme", "stats"} <= set(cards[0])
    story = client.get(f"/stories/{cards[0]['id']}").json()
    assert story["body"] and story["trail"]
    assert client.get("/stories/no-such-story").status_code == 404


def test_chat_keeps_the_project_1_shape(client):
    body = client.post("/chat", json={"message": "Does tea hurt sleep?"}).json()
    assert set(body) == {"response", "session_id", "tool_calls", "sources"}
    assert body["response"].startswith("# tea & sleep")
    assert [source["name"][:3] for source in body["sources"]] == ["Gen", "Wor"]


def test_chat_remembers_a_clarification_within_a_session(client):
    first = client.post("/chat", json={"message": "something vague"}).json()
    assert first["response"] == "Which part?\n- Money?\n- Friends?" and first["sources"] == []
    client.post("/chat", json={"message": "the money one", "session_id": first["session_id"]})
    history = client.asked[-1]["history"]
    assert history[0]["question"] == "something vague" and history[0]["kind"] == "clarify"
    client.post("/chat", json={"message": "unrelated, new session"})
    assert client.asked[-1]["history"] == []


def test_chat_says_so_when_the_desk_is_full(client, monkeypatch):
    monkeypatch.setattr(web, "budget", RunBudget(concurrent=0, per_day=100))
    response = client.post("/chat", json={"message": "Does tea hurt sleep?"})
    assert response.status_code == 429
    assert set(response.json()) == {"response", "session_id", "tool_calls", "sources"}
    assert client.asked == []


def test_run_streams_the_board_and_then_the_story(client):
    seen = events(client.get("/run", params={"question": "Does tea hurt sleep?"}))
    assert [event["type"] for event in seen] == ["start", "task", "report"]
    story = seen[-1]["story"]
    assert story["number"] > 6 and client.get(f"/stories/{story['id']}").status_code == 200
    assert web.budget.running == 0


def test_run_passes_on_earlier_turns_and_distrusts_them(client):
    history = [{"question": "q" * 1000, "kind": "nonsense", "reply": "r"}, "not a turn"]
    client.get("/run", params={"question": "the money one", "history": json.dumps(history)})
    (turn,) = client.asked[-1]["history"]
    assert len(turn["question"]) == 300 and turn["kind"] == "report"
    assert web.parse_history("not json") == [] and web.parse_history('{"a": 1}') == []


def test_run_turns_people_away_when_the_desk_is_full(client, monkeypatch):
    monkeypatch.setattr(web, "budget", RunBudget(concurrent=0, per_day=100))
    seen = events(client.get("/run", params={"question": "Does tea hurt sleep?"}))
    assert [event["type"] for event in seen] == ["start", "busy"]


def test_a_doubled_slash_still_reaches_chat(client):
    # Spelled out in full: "//chat" on its own would be read as a URL with the host "chat".
    assert client.post("http://testserver//chat", json={"message": "Does tea hurt sleep?"}).status_code == 200
    assert client.get("http://testserver//stories").status_code == 200
