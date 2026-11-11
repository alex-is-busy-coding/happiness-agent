"""Run the desk over evals/samples.json and score what it files.

    uv run python -m evals.run                    every sample: about 15 minutes of model calls
    uv run python -m evals.run --gold             print the gold answers, computed from the database, and stop
    uv run python -m evals.run --only health trust --no-judge

Three kinds of sample. An "answer" sample has a gold answer computed by its own SQL, so the gold
moves with the data: the story must contain it. A "clarify" sample is too vague and a "decline"
sample is outside the data: both must come back as a clarification, not a story.

Two kinds of scorer. Deterministic checks: the right kind of outcome, the gold numbers present,
the right survey cited, and how many of the story's statistics can be found in a query result.
A judge with a rubric: does the story claim causation from a correlation?
"""

import argparse
import asyncio
import json
import math
import re
import statistics
import time
from pathlib import Path

import duckdb
import litellm

from context_panel import AgentContexts
from desk.agents import DeskHooks, deep_research
from desk.config import DB_PATH, MODEL_NAME, VERTEX

HERE = Path(__file__).parent
NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
CITATION = re.compile(r"\((?:GSS|WHR|ATUS)[^)]*\)")
FENCED = re.compile(r"```.*?```", re.DOTALL)

RUBRIC = """\
You are checking a short data story for one fault: claiming causation from a correlation.
The story rests on cross-sectional surveys. They can show that two things go together, not that
one causes the other.

Fail the story if any sentence states or clearly implies that one thing causes, produces, raises,
lowers or protects against another ("marriage makes people happier", "money buys happiness",
"the pandemic caused the drop"), unless that sentence is itself a caution that the data cannot
show it.
Do not fail it for descriptive wording ("is linked to", "goes with", "report more", "are more
likely to"), or for describing how a number changed over time.

Reply with JSON only: {"causal_claim": true or false, "sentence": "<the offending sentence, or empty>"}"""


def numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in NUMBER.findall(text) if match.replace(",", "") not in "-"]


def gold_for(sample: dict) -> list:
    """The gold answer, from the sample's own SQL against the database as it is now: numbers the
    story must state, and names it must mention."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cells = [cell for row in con.execute(sample["gold_sql"]).fetchall() for cell in row]
    finally:
        con.close()
    return [cell if isinstance(cell, str) else float(cell) for cell in cells]


def whole_text(story: dict) -> str:
    stats = " ".join(f"{stat['value']} {stat['label']}" for stat in story["stats"])
    return "\n".join([story["title"], story["hook"], story["answer"], stats, story["body"]])


def has_gold(story: dict, gold: list, tolerance: float) -> bool:
    text = whole_text(story)
    found = numbers_in(text)
    return all(
        value.lower() in text.lower()
        if isinstance(value, str)
        else any(abs(number - value) <= tolerance + 1e-9 for number in found)
        for value in gold
    )


def traceable(story: dict) -> tuple[int, int]:
    """Of the statistics the story states in prose (a percentage, or a number with a decimal),
    how many appear in a query result, allowing for rounding and for shares stored as fractions."""
    results = [n for entry in story["trail"] for query in entry["queries"] for n in numbers_in(query["result"])]
    prose = CITATION.sub("", FENCED.sub("", "\n".join([story["answer"], story["body"]])))
    stated = re.findall(r"(?<![\w.,])(\d[\d,]*(?:\.(\d+))?)(\s?%)?", prose)
    found = total = 0
    for text, decimals, percent in stated:
        if not (percent or decimals):
            continue  # a bare whole number is a year, a rank or a count
        value, slack = float(text.replace(",", "")), 0.5 * 10 ** -len(decimals) + 1e-9
        total += 1
        found += any(abs(result - value) <= slack or abs(result * 100 - value) <= slack for result in results)
    return found, total


async def judge(story: dict) -> dict:
    """The rubric judge's verdict, or {} if it could not be had: a rate limit must not sink the run."""
    for wait in (0, 10, 30, 60):
        await asyncio.sleep(wait)
        try:
            response = await litellm.acompletion(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": RUBRIC}, {"role": "user", "content": whole_text(story)}],
                temperature=0,
                **VERTEX,
            )
            match = re.search(r"\{.*\}", response.choices[0].message.content or "", re.DOTALL)
            return json.loads(match.group(0)) if match else {}
        except Exception as e:
            print(f"judge: {type(e).__name__}, retrying", flush=True)
    return {}


async def run_sample(sample: dict, limit: asyncio.Semaphore, use_judge: bool) -> dict:
    result = {"id": sample["id"], "kind": sample["kind"], "question": sample["question"], "checks": {}}
    async with limit:
        started = time.monotonic()
        try:
            outcome = await deep_research(sample["question"], DeskHooks(AgentContexts(), lambda _event: None))
        except Exception as e:
            outcome = {"type": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"}
        result["seconds"] = round(time.monotonic() - started)
    result["outcome"] = outcome["type"]

    if sample["kind"] != "answer":
        result["passed"] = outcome["type"] == "clarify"
        result["reply"] = outcome.get("message", outcome.get("story", {}).get("answer", ""))
        return result

    gold = gold_for(sample)
    result["gold"] = [value if isinstance(value, str) else round(value, 2) for value in gold]
    if outcome["type"] != "report":
        result["passed"] = False
        result["reply"] = outcome.get("message", "")
        return result
    story = outcome["story"]
    found, total = traceable(story)
    result["checks"] = {
        "gold": has_gold(story, gold, sample["tolerance"]),
        "cited": sample["source"] in story["sources"],
        "traceable": [found, total],
    }
    if use_judge:
        verdict = await judge(story)
        if "causal_claim" in verdict:  # left out when the judge could not be reached
            result["checks"]["no_causal_claim"] = verdict["causal_claim"] is False
            result["flagged"] = verdict.get("sentence", "")
    result["passed"] = result["checks"]["gold"]
    result["title"], result["answer"] = story["title"], story["answer"]
    return result


def summarise(results: list[dict]) -> dict:
    def rate(rows, key=lambda row: row["passed"]):
        return [sum(bool(key(row)) for row in rows), len(rows)]

    answers = [row for row in results if row["kind"] == "answer"]
    filed = [row for row in answers if row["checks"]]
    found = sum(row["checks"]["traceable"][0] for row in filed)
    stated = sum(row["checks"]["traceable"][1] for row in filed)
    seconds = [row["seconds"] for row in filed]
    summary = {
        "samples": len(results),
        "margin_of_error": round(1 / math.sqrt(len(results)), 3) if results else None,
        "passed": rate(results),
        "answer: gold answer in the story": rate(answers),
        "clarify: asked instead of guessing": rate([row for row in results if row["kind"] == "clarify"]),
        "decline: said the data can't answer": rate([row for row in results if row["kind"] == "decline"]),
        "stories citing the expected survey": rate(filed, lambda row: row["checks"]["cited"]),
        "stated statistics found in a query result": [found, stated],
        "seconds: median, slowest": [round(statistics.median(seconds)), max(seconds)] if seconds else None,
    }
    judged = [row for row in filed if "no_causal_claim" in row["checks"]]
    if judged:
        summary["stories with no causal claim (judge)"] = rate(judged, lambda row: row["checks"]["no_causal_claim"])
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="sample ids to run")
    parser.add_argument("--gold", action="store_true", help="print the gold answers and stop")
    parser.add_argument("--no-judge", action="store_true", help="skip the rubric judge")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out", default=str(HERE / "results" / "latest.json"), help="where to save the scores")
    args = parser.parse_args()

    samples = json.loads((HERE / "samples.json").read_text())
    if args.only:
        samples = [sample for sample in samples if sample["id"] in args.only]
    if args.gold:
        for sample in samples:
            if sample["kind"] == "answer":
                gold = ", ".join(value if isinstance(value, str) else f"{value:.2f}" for value in gold_for(sample))
                print(f"{sample['id']:<16} {gold:<48} {sample['from']}")
        return

    limit = asyncio.Semaphore(args.concurrency)
    order = {sample["id"]: i for i, sample in enumerate(samples)}
    results: list[dict] = []
    for finished in asyncio.as_completed([run_sample(sample, limit, not args.no_judge) for sample in samples]):
        row = await finished
        results.append(row)
        results.sort(key=lambda r: order[r["id"]])
        # Saved after every sample: twenty minutes of model calls should survive a crash.
        Path(args.out).write_text(json.dumps({"summary": summarise(results), "results": results}, indent=2) + "\n")
        print(
            f"{'PASS' if row['passed'] else 'FAIL'}  {row['id']:<16} {row['seconds']:>4}s  {row['outcome']}", flush=True
        )
    summary = summarise(results)
    print()
    for name, value in summary.items():
        print(f"{name:<45} {value}")
    for row in results:
        if not row["passed"]:
            print(f"\nFAILED {row['id']}: {row['question']}\n  outcome: {row['outcome']}  gold: {row.get('gold')}")
            print(f"  reply: {str(row.get('answer') or row.get('reply'))[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
