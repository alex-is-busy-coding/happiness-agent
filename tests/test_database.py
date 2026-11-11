import asyncio

from desk.agents import BudgetedSql, as_turn, conversation
from desk.database import is_read_only, run_query, summary


def test_only_selects_are_read_only():
    assert is_read_only("  select 1;") and is_read_only("WITH t AS (SELECT 1) SELECT * FROM t")
    assert not is_read_only("DROP TABLE gss") and not is_read_only("INSERT INTO gss VALUES (1)")


def test_run_query_returns_a_markdown_table():
    table = run_query("SELECT 1 AS one, 2.5::DOUBLE AS half, NULL AS nothing;")
    assert table.splitlines() == ["| one | half | nothing |", "|---|---|---|", "| 1 | 2.500 | NULL |"]


def test_run_query_caps_the_rows_and_says_so():
    table = run_query("SELECT * FROM range(100)", max_rows=5)
    assert table.count("\n| ") == 5
    assert "More than 5 rows" in table


def test_summary_counts_what_is_in_the_database():
    found = summary()
    assert found["gss"]["people"] > 70_000 and found["gss"]["first"] == 1972
    assert found["whr"]["countries"] > 100


def test_a_researcher_gets_only_so_many_queries():
    queries = BudgetedSql(budget=2)

    async def scenario():
        assert "Only SELECT" in await queries.run("DELETE FROM gss")
        assert "Query failed" in await queries.run("SELECT * FROM no_such_table")
        assert queries.left == 2  # neither of those cost anything
        assert "| 1 |" in await queries.run("SELECT 1")
        assert "(0 queries left.)" in await queries.run("SELECT 2")
        assert "budget spent" in await queries.run("SELECT 3")

    asyncio.run(scenario())


def test_earlier_turns_are_put_before_a_follow_up():
    assert conversation([]) == ""
    clarified = as_turn("tell me about happiness", {"type": "clarify", "message": "Which part?", "options": ["Money?"]})
    filed = as_turn("Money?", {"type": "report", "story": {"answer": "Richer people report more.", "title": "money"}})
    text = conversation([clarified, filed])
    assert 'The user asked: "tell me about happiness"' in text
    assert "The lead asked them to clarify: Which part?\n- Money?" in text
    assert "The desk answered: Richer people report more." in text
