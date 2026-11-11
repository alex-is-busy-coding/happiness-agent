from desk.story import (
    build_story,
    clarify_markdown,
    collect_tool_calls,
    parse_clarify,
    parse_story,
    story_markdown,
)


def test_parse_story_splits_metadata_from_body(reply):
    meta, body = parse_story(reply)
    assert meta["title"] == "Tea & Sleep"
    assert body.startswith("## late cups, short nights")
    assert "```story" not in body and "```chart" in body


def test_parse_story_without_a_readable_block_is_all_body():
    assert parse_story("## just a story") == ({}, "## just a story")
    meta, body = parse_story('```story\n{"title": broken}\n```\n\n## still a story')
    assert meta == {} and body == "## still a story"


def test_build_story_cleans_what_the_writer_sent(reply, contexts):
    story = build_story("Does tea hurt sleep?", reply, {"Researcher 1": "tea"}, collect_tool_calls(contexts), 93.4)
    assert story["title"] == "tea & sleep"  # cover lines are lowercase
    assert story["theme"] == "daily life"
    assert [stat["value"] for stat in story["stats"]] == ["41%", "2x"]  # the first three, minus the empty one
    assert len(story["followups"]) == 3
    assert story["sources"] == ["GSS", "WHR"]
    assert story["seconds"] == 93


def test_build_story_falls_back_when_the_metadata_is_missing():
    story = build_story("Does tea hurt sleep?", "## no metadata here", {}, [], 1)
    assert story["title"] == "does tea hurt sleep"
    assert story["theme"] == "default"
    assert story["stats"] == [] and story["trail"] == [] and story["sources"] == []


def test_the_paper_trail_gives_each_researcher_its_own_queries(reply, contexts):
    story = build_story("q", reply, {"Researcher 1": "tea"}, collect_tool_calls(contexts), 1)
    (entry,) = story["trail"]
    assert entry["agent"] == "Researcher 1" and entry["task"] == "tea"
    assert [query["sql"] for query in entry["queries"]] == ["SELECT 1", "SELECT 2"]
    assert "| 2 |" in entry["queries"][1]["result"]


def test_tool_calls_carry_their_agent_and_result(contexts):
    calls = collect_tool_calls(contexts)
    assert [(call["agent"], call["name"]) for call in calls] == [
        ("Lead", "research"),
        ("Researcher 1", "run_sql"),
        ("Researcher 1", "run_sql"),
    ]
    assert calls[0]["args"] == {"task": "tea"} and calls[0]["result"] == "- tea findings"
    assert all("result" in call for call in calls)


def test_story_markdown_leads_with_the_title_and_answer(reply):
    text = story_markdown(build_story("q", reply, {}, [], 1))
    assert text.startswith("# tea & sleep\n\n*Does a late cup cost you rest?*\n\n> Late tea goes with worse sleep.")


def test_parse_clarify_separates_the_ask_from_the_options():
    message, options = parse_clarify("CLARIFY: Too broad.\nWhich part?\n- Money?\n- Friends?\n* Health?")
    assert message == "Too broad. Which part?"
    assert options == ["Money?", "Friends?", "Health?"]
    assert clarify_markdown(message, options) == "Too broad. Which part?\n- Money?\n- Friends?\n- Health?"
