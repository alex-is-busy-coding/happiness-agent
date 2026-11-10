# What makes a happy life?

A deep-research agent that answers questions about happiness with real survey data instead of
vibes. Ask it *"does money buy happiness?"* and a **Lead** agent splits the question into
tasks, fans out **Researchers** that query a local DuckDB in parallel with SQL, goes back for
more when the findings leave a gap, and a **Writer** turns the findings into a cited report
with charts.

**Research question:** How do our daily habits, social connections and economic realities
combine to create both momentary happiness and lifelong meaning?

**Target user:** the curious non-specialist — students, journalists, and anyone who wonders
whether money, marriage, or friends actually move the needle — who wants answers grounded in
named surveys, with the queries shown.

**Path:** Deep Research (`research`). Three agent shapes (Lead, Researcher, Writer); the Lead
fans out up to 6 Researchers in one turn, each in a fresh context; it loops back for gaps at
most twice; vague questions come back as a `CLARIFY:` request instead of a report; every claim
in the report cites its dataset, and every number must appear in a query result.

## The data

Three datasets, prepared offline by `prepare_data.py` into `data/happiness.duckdb` (committed,
so the deploy ships with it) plus `data/context.md`, the schema notes injected into every
agent's instructions:

| Table | Source | What it is |
|---|---|---|
| `gss` | [GSS 1972–2024 cumulative file, NORC](https://gss.norc.org/us/en/gss/get-the-data.html) (Stata, release 3a) | 75,699 US adults: self-reported happiness vs. social life, marriage, income, health, trust. Codes decoded to labels; person weights kept. |
| `whr` | [World Happiness Report 2026, Data for Figure 2.1](https://worldhappiness.report/data-sharing/) | 2,116 country-years (2011–2025) of Gallup World Poll life evaluations, decomposed into six explanatory factors. |
| `atus_wb` | [ATUS Well-Being Module 2021, BLS](https://www.bls.gov/tus/data/wbdatafiles-2021.htm) | ~10k activity episodes: how happy / meaningful / stressful each activity felt (0–6), what it was, how long, and who was present. |

The model knows happiness research folklore; it does not know this microdata — weighted
cell-level shares from a 75k-row cumulative file, or affect ratings joined to activity codes
and companions, only come out of the queries.

To rebuild: download the raw files into `data_raw/` (links above; the GSS Stata zip and the
five BLS zips `wbact-2021`, `wbresp-2021`, `atusact-2021`, `atussum-2021`, `atuswho-2021`),
then `uv run --group prep prepare_data.py`. BLS blocks non-browser downloads, so fetch those
five by clicking the links.

## Run it

```sh
gcloud auth application-default login
cp .env.example .env   # set GOOGLE_CLOUD_PROJECT (a project with the Vertex AI API enabled)
uv run app.py          # open http://localhost:8000
```

The `/chat` endpoint keeps the Project 1 response shape — `POST {"message": ...}` returns
`{"response", "tool_calls"}`, where sub-agent calls carry an `agent` field:

```sh
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "Are married people happier than single people?"}'
```

The front page streams the same run as Server-Sent Events, with an activity log and a context
panel showing each agent's exact context.

## Sample queries for grading

**1. "Are married people happier than single people?"**
Expected: yes, by the largest margin of any everyday variable in the GSS — about 39% of
married respondents say "very happy" vs. about 21% of the never-married and about 15% of the
separated (weighted, 1972–2024). From the `gss` table (`happy`, `marital`, `weight`).

> *No-tools baseline (same model, no system prompt, via `uv run baseline.py`):* a long essay on
> "the quality factor", selection effects and the honeymoon effect — reasonable folklore, but
> not one verifiable number, and it concludes the averages are "a misleading statistic".
> *Agent:* "39.10% of married respondents report being 'very happy' compared to 20.83% of
> never-married respondents (GSS 1972-2024) ... Separated individuals report the lowest levels
> of happiness, with only 15.26% 'very happy' ... The marital happiness advantage holds without
> exception across both sexes and all four age groups" — every number weighted, queried, cited,
> and charted.

**2. "Why do Nordic countries top the happiness rankings?"**
Expected: in 2025 the five Nordics hold five of the global top six (Finland #1 at 7.76); the
factor decomposition shows their largest relative edge over the world average is low
corruption, then healthy life expectancy and social support. From the `whr` table
(`life_evaluation`, `rank`, `explained_by_*`, year 2025).

> *No-tools baseline:* an essay on the welfare state, *hygge* and *friluftsliv* — plausible,
> uncited, and with no way to check any of it.
> *Agent:* "Finland ranked 1st with 7.764 ... the Nordic average of 7.468 is 1.818 points
> higher than the global average of 5.650 ... the largest relative difference is in the 'low
> corruption' category, where the Nordic average of 0.402 is 171.5% higher than the global
> average of 0.148 (WHR 2026)" — with the 2019–2025 coverage limit of the factor columns
> flagged in "What this data can't tell us".

**3. "Which daily activities feel most meaningful, and are they the same ones that feel good?"**
Expected: no — religious activities and caring for others score highest on meaning while
leisure scores higher on happiness than on meaning; work scores low on both. From the
`atus_wb` table (`happy_0to6`, `meaning_0to6` by `activity`, weighted by `activity_weight`).

> *No-tools baseline:* TODO after the ATUS raw files are downloaded (BLS blocks scripted
> downloads) and `prepare_data.py` is re-run.
> *Agent:* TODO — until then the agent correctly answers that it cannot measure specific daily
> activities and proposes a GSS-based alternative (the "admits what the data can't answer"
> behavior, live).

## What the data can't answer

The agent is instructed to say so, and the data genuinely can't: anything causal (these are
cross-sectional correlations), anything about individuals over time (GSS interviews different
people each wave), momentary feelings outside 2021 in the US, within-country inequality in the
WHR averages, and any question needing medical, genetic, or non-survey evidence. Researchers
flag these as `LIMIT:` bullets and the Writer collects them into a "What this data can't tell
us" section.

## Deploy

Continuous deploy from GitHub to Cloud Run using the included `Dockerfile` (listens on
`$PORT`). The DuckDB ships in the image; no GCS needed. Raise the Cloud Run request timeout to
600 s — a full research run makes a dozen model calls and takes 2-4 minutes.
