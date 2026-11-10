"""Offline build step: turn raw survey downloads in data_raw/ into data/happiness.duckdb.

Sources (see README for download instructions):
  - GSS cumulative file 1972-2024 (NORC), Stata format: data_raw/gss7224_r3a.dta
  - World Happiness Report 2026, Figure 2.1 data:      data_raw/WHR26_Data_Figure_2.1.xlsx
  - ATUS Well-Being Module 2021 (BLS), zip files:      data_raw/wbact-2021.zip, wbresp-2021.zip,
                                                       atusact-2021.zip, atussum-2021.zip, atuswho-2021.zip

Also writes data/context.md, the schema notes the agents read before writing SQL, and
data/guesses.json, the guess-the-stat cards the front end shows while a run is in progress.
Run with: uv run --group prep prepare_data.py
"""

import io
import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

RAW = Path(__file__).parent / "data_raw"
OUT = Path(__file__).parent / "data"

# --- GSS answer codes -> labels, from the GSS 2024 R3a codebook ---

GSS_LABELS = {
    "happy": {1: "very happy", 2: "pretty happy", 3: "not too happy"},
    "hapmar": {1: "very happy", 2: "pretty happy", 3: "not too happy"},
    "health": {1: "excellent", 2: "good", 3: "fair", 4: "poor"},
    "life": {1: "exciting", 2: "routine", 3: "dull"},
    "satfin": {1: "pretty well satisfied", 2: "more or less satisfied", 3: "not satisfied at all"},
    "satjob": {1: "very satisfied", 2: "moderately satisfied", 3: "a little dissatisfied", 4: "very dissatisfied"},
    "finrela": {1: "far below average", 2: "below average", 3: "average", 4: "above average", 5: "far above average"},
    "trust": {1: "most people can be trusted", 2: "can't be too careful", 3: "depends"},
    "class": {1: "lower class", 2: "working class", 3: "middle class", 4: "upper class"},
    "sex": {1: "male", 2: "female"},
    "marital": {1: "married", 2: "widowed", 3: "divorced", 4: "separated", 5: "never married"},
    "degree": {0: "less than high school", 1: "high school", 2: "associate/junior college", 3: "bachelor's", 4: "graduate"},
    "wrkstat": {1: "working full time", 2: "working part time", 3: "with a job, not at work", 4: "unemployed",
                5: "retired", 6: "in school", 7: "keeping house", 8: "other"},
    "socfrend": {1: "almost daily", 2: "once or twice a week", 3: "several times a month", 4: "about once a month",
                 5: "several times a year", 6: "about once a year", 7: "never"},
    "attend": {0: "never", 1: "less than once a year", 2: "about once or twice a year", 3: "several times a year",
               4: "about once a month", 5: "2-3 times a month", 6: "nearly every week", 7: "every week",
               8: "several times a week"},
}
GSS_LABELS["socrel"] = GSS_LABELS["socommun"] = GSS_LABELS["socbar"] = GSS_LABELS["socfrend"]

GSS_COLUMNS = ["year", "id", "age", "sex", "degree", "marital", "wrkstat", "childs", "region",
               "happy", "hapmar", "health", "life", "satfin", "satjob", "finrela", "class",
               "realinc", "trust", "socrel", "socommun", "socfrend", "socbar", "attend",
               "wtssall", "wtssps"]

# GSS reports family income in 1986 dollars (REALINC). Readers think in today's money, so convert
# with the ratio of BLS CPI-U annual averages: 313.689 (2024) / 109.6 (1986).
CPI_2024_OVER_1986 = 313.689 / 109.6


def build_gss(con: duckdb.DuckDBPyConnection) -> int:
    df = pd.read_stata(RAW / "gss7224_r3a.dta", columns=GSS_COLUMNS, convert_categoricals=False)
    # Stata extended missing codes come through as huge ints; treat anything out of range as missing.
    for col, labels in GSS_LABELS.items():
        codes = df[col].where(df[col].isin(labels))
        df[col] = codes
        df[col + "_label"] = codes.map(labels)
    df.loc[df["age"] > 89, "age"] = pd.NA  # 89 is top-coded "89 or older"
    df.loc[df["realinc"] <= 0, "realinc"] = pd.NA
    df["family_income_2024usd"] = (df["realinc"] * CPI_2024_OVER_1986).round()
    df["year"] = df["year"].astype(int)
    df["weight"] = df["wtssps"].fillna(df["wtssall"]).fillna(1.0)
    df = df.drop(columns=["realinc", "wtssall", "wtssps"])
    con.execute("CREATE OR REPLACE TABLE gss AS SELECT * FROM df")
    return len(df)


def build_whr(con: duckdb.DuckDBPyConnection) -> int:
    df = pd.read_excel(RAW / "WHR26_Data_Figure_2.1.xlsx")
    df.columns = ["year", "rank", "country", "life_evaluation", "ci_lower", "ci_upper",
                  "explained_by_gdp_per_capita", "explained_by_social_support",
                  "explained_by_healthy_life_expectancy", "explained_by_freedom",
                  "explained_by_generosity", "explained_by_low_corruption", "dystopia_plus_residual"]
    con.execute("CREATE OR REPLACE TABLE whr AS SELECT * FROM df")
    return len(df)


# --- ATUS Well-Being Module 2021 ---

ATUS_TIER1 = {
    1: "personal care (incl. sleep)", 2: "household activities", 3: "caring for household members",
    4: "caring for non-household members", 5: "work", 6: "education", 7: "shopping",
    8: "professional & personal care services", 9: "household services", 10: "government services",
    11: "eating and drinking", 12: "socializing, relaxing, and leisure", 13: "sports, exercise, and recreation",
    14: "religious and spiritual activities", 15: "volunteering", 16: "telephone calls",
    18: "travel", 50: "other",
}

# TUWHO_CODE values from the ATUS data dictionary.
ATUS_WHO = {
    18: "alone", 19: "alone", 20: "spouse", 21: "unmarried partner", 22: "own household child",
    23: "grandchild", 24: "parent", 25: "brother/sister", 26: "other related person",
    27: "foster child", 28: "housemate/roommate", 29: "roomer/boarder", 30: "other nonrelative",
    40: "own nonhousehold child < 18", 51: "parents (not living in household)",
    52: "other nonhousehold family members < 18", 53: "other nonhousehold family members 18+",
    54: "friends", 55: "co-workers", 56: "neighbors/acquaintances", 57: "other nonhousehold children < 18",
    58: "other nonhousehold adults 18+", 59: "boss or manager", 60: "people whom I supervise",
    61: "co-workers", 62: "customers",
}


def _read_zip_csv(name: str) -> pd.DataFrame:
    path = RAW / name
    with zipfile.ZipFile(path) as z:
        member = next(n for n in z.namelist() if n.endswith((".dat", ".csv")))
        df = pd.read_csv(io.BytesIO(z.read(member)))
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def build_atus(con: duckdb.DuckDBPyConnection) -> int | None:
    needed = ["wbact-2021.zip", "wbresp-2021.zip", "atusact-2021.zip", "atussum-2021.zip", "atuswho-2021.zip"]
    missing = [n for n in needed if not (RAW / n).exists()]
    if missing:
        print(f"SKIPPING ATUS: missing {missing} in {RAW}/ — download from https://www.bls.gov/tus/data/wbdatafiles-2021.htm and https://www.bls.gov/tus/data/datafiles-2021.htm")
        return None

    wbact = _read_zip_csv("wbact-2021.zip")
    act = _read_zip_csv("atusact-2021.zip")
    who = _read_zip_csv("atuswho-2021.zip")
    summ = _read_zip_csv("atussum-2021.zip")

    # One row per sampled activity: affect ratings + what the activity was and how long it took.
    act_cols = ["TUCASEID", "TUACTIVITY_N", "TUTIER1CODE", "TUACTDUR24"]
    merged = wbact.merge(act[[c for c in act_cols if c in act.columns]], on=["TUCASEID", "TUACTIVITY_N"], how="left")

    # Who was in the room: flag the company that research says matters most.
    who = who[who["TUWHO_CODE"] >= 18]
    who["who_label"] = who["TUWHO_CODE"].map(ATUS_WHO)
    grouped = who.groupby(["TUCASEID", "TUACTIVITY_N"])["who_label"].agg(
        lambda labels: "alone" if set(labels) == {"alone"} else ", ".join(sorted(set(labels) - {"alone"})))
    merged = merged.merge(grouped.rename("with_whom"), on=["TUCASEID", "TUACTIVITY_N"], how="left")

    demo = summ[["TUCASEID", "TEAGE", "TESEX", "TRDPFTPT"]].rename(
        columns={"TEAGE": "age", "TESEX": "sex_code", "TRDPFTPT": "fulltime_code"})
    merged = merged.merge(demo, on="TUCASEID", how="left")

    out = pd.DataFrame({
        "case_id": merged["TUCASEID"],
        "activity_n": merged["TUACTIVITY_N"],
        "activity": merged["TUTIER1CODE"].map(ATUS_TIER1),
        "duration_minutes": merged["TUACTDUR24"],
        "with_whom": merged["with_whom"].fillna("alone"),
        "happy_0to6": merged["WUHAPPY"].where(merged["WUHAPPY"].between(0, 6)),
        "meaning_0to6": merged["WUMEANING"].where(merged["WUMEANING"].between(0, 6)),
        "stress_0to6": merged["WUSTRESS"].where(merged["WUSTRESS"].between(0, 6)),
        "tired_0to6": merged["WUTIRED"].where(merged["WUTIRED"].between(0, 6)),
        "sad_0to6": merged["WUSAD"].where(merged["WUSAD"].between(0, 6)),
        "pain_0to6": merged["WUPAIN"].where(merged["WUPAIN"].between(0, 6)),
        "respondent_age": merged["age"],
        "respondent_sex": merged["sex_code"].map({1: "male", 2: "female"}),
        "activity_weight": merged.get("WUFNACTWTC"),
    })
    con.execute("CREATE OR REPLACE TABLE atus_wb AS SELECT * FROM out")
    return len(out)


def write_context(con: duckdb.DuckDBPyConnection, counts: dict) -> None:
    """The schema notes every agent reads before writing SQL, built from the live database."""
    lines = ["# The happiness database (DuckDB)\n"]
    docs = {
        "gss": f"""## gss — General Social Survey, {counts.get('gss', 0):,} US adults, 1972-2024 (NORC)
One row per respondent. Self-reported happiness vs. social life, money, health, work.
Ordinal answers come as a numeric code plus a `_label` text column; LOWER code = better/more
(happy: 1 very happy ... 3 not too happy; socfrend 1 almost daily ... 7 never; EXCEPT attend: 0 never ... 8 several times a week).
NULL means the question wasn't asked or wasn't answered that year — always filter `IS NOT NULL`.
Weight every share by `weight` (e.g. SUM(weight * (happy = 1)::INT) / SUM(weight)).
family_income_2024usd is family income before taxes in 2024 dollars, comparable across all years.
The survey records income in brackets, so values cluster: compare bands or NTILE groups.
CAUTION on 2021 and later: until 2018 the survey was done in person; 2021 was done on the web
because of COVID, and 2022 and 2024 mix both. NORC warns that changes between 2018 and 2021
may partly reflect that switch, not real change. Say so whenever a finding rests on the 2020s.""",
        "whr": f"""## whr — World Happiness Report 2026, {counts.get('whr', 0):,} country-year rows, 2011-2025 (Gallup World Poll)
One row per country per year. life_evaluation is the 0-10 Cantril ladder (3-year average).
The explained_by_* columns are the report's estimate of how many ladder points each of six
factors accounts for in that country's score; they plus dystopia_plus_residual sum to
life_evaluation. They are NULL before 2019. What each factor measures:
gdp_per_capita = national income per person; social_support = share who say they have someone
to count on in times of trouble; healthy_life_expectancy = years lived in good health;
freedom = share satisfied with their freedom to choose what to do with their life;
generosity = share who donated to charity recently; low_corruption = share who do NOT see
corruption as widespread in government and business (a view about corruption: do not call it
"trust"). Name them as that, nothing broader.
rank is within that year: 1 is the highest score, so a rank going from 24 to 23 is a rise.""",
        "atus_wb": f"""## atus_wb — American Time Use Survey Well-Being Module 2021, {counts.get('atus_wb', 0):,} activity episodes (BLS)
One row per sampled activity from a respondent's day diary: what they did, for how long, who was
with them, and how happy / meaningful / stressful / tiring / sad / painful it felt, each rated 0-6
(HIGHER = more). Weight by activity_weight. `with_whom` is 'alone' or a list like 'friends' or 'spouse'.
Momentary experience, unlike gss/whr's overall life evaluations.""",
    }
    for table in counts:
        lines.append(docs[table])
        schema = con.execute(f"DESCRIBE {table}").fetchall()
        lines.append("Columns: " + ", ".join(f"{name} ({dtype})" for name, dtype, *_ in schema) + "\n")
    missing = [t for t in docs if t not in counts]
    for t in missing:
        lines.append(f"## {t} — NOT BUILT (raw files missing)\n")
    (OUT / "context.md").write_text("\n".join(lines))


def write_guesses(con: duckdb.DuckDBPyConnection) -> int:
    """Guess-the-stat cards, each answer computed here so it can never drift from the data."""

    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    def share(condition: str, where: str) -> float:
        return round(one(f"SELECT 100 * SUM(weight * ({condition})::INT) / SUM(weight) FROM gss WHERE {where}"), 1)

    latest = one("SELECT MAX(year) FROM whr")
    ranked = one(f"SELECT COUNT(*) FROM whr WHERE year = {latest}")
    top, top_score = con.execute(f"SELECT country, life_evaluation FROM whr WHERE year = {latest} ORDER BY rank LIMIT 1").fetchone()
    bottom, bottom_score = con.execute(
        f"SELECT country, life_evaluation FROM whr WHERE year = {latest} ORDER BY life_evaluation LIMIT 1").fetchone()
    us_rank, us_score = con.execute(
        f"SELECT rank, life_evaluation FROM whr WHERE year = {latest} AND country = 'United States'").fetchone()
    median_score = one(f"SELECT MEDIAN(life_evaluation) FROM whr WHERE year = {latest}")
    quintiles = dict(con.execute("""
        WITH t AS (SELECT *, NTILE(5) OVER (ORDER BY family_income_2024usd) AS q FROM gss
                   WHERE family_income_2024usd IS NOT NULL AND happy IS NOT NULL)
        SELECT q, ROUND(100 * SUM(weight * (happy = 1)::INT) / SUM(weight), 1) FROM t GROUP BY q""").fetchall())
    happy_70s = share("happy = 1", "happy IS NOT NULL AND year < 1980")
    trust_70s = share("trust = 1", "trust IS NOT NULL AND year < 1980")
    poor_health = share("happy = 1", "happy IS NOT NULL AND health = 4")

    pct = {"min": 0, "max": 100, "step": 1, "unit": "%"}
    ladder = {"min": 0, "max": 10, "step": 0.1, "unit": " / 10"}
    guesses = [
        {"prompt": "What share of married Americans call themselves \"very happy\"?",
         "answer": share("happy = 1", "happy IS NOT NULL AND marital = 1"),
         "reveal": "Across every survey since 1972. For the never-married it is "
                   f"{share('happy = 1', 'happy IS NOT NULL AND marital = 5')}%.", "source": "GSS", **pct},
        {"prompt": f"In the 1970s, {trust_70s}% of Americans said most people can be trusted. What is it in the 2020s?",
         "answer": share("trust = 1", "trust IS NOT NULL AND year >= 2020"),
         "reveal": f"In the 1990s it was {share('trust = 1', 'trust IS NOT NULL AND year BETWEEN 1990 AND 1999')}%.",
         "source": "GSS", **pct},
        {"prompt": f"{top} is the happiest country of {latest}. What does it score out of 10?",
         "answer": round(top_score, 1),
         "reveal": f"People rate their own life on a ladder from 0 to 10. {bottom}, at the bottom, scores {bottom_score:.1f}.",
         "source": "WHR", **ladder},
        {"prompt": f"Of {ranked} countries ranked in {latest}, where does the United States land?",
         "answer": us_rank, "min": 1, "max": ranked, "step": 1, "unit": "",
         "reveal": f"Americans rate their lives {us_score:.1f} out of 10 on average.", "source": "WHR"},
        {"prompt": f"Of Americans surveyed since 1972, {quintiles[1]}% of the poorest fifth say they are \"very happy\". "
                   "What about the richest fifth?",
         "answer": quintiles[5],
         "reveal": f"The middle fifth sits at {quintiles[3]}%.", "source": "GSS", **pct},
        {"prompt": "What share of Americans in the 2020s see friends at least once a week?",
         "answer": share("socfrend <= 2", "socfrend IS NOT NULL AND year >= 2020"),
         "reveal": f"In the 1970s it was {share('socfrend <= 2', 'socfrend IS NOT NULL AND year < 1980')}%.",
         "source": "GSS", **pct},
        {"prompt": f"In the 1970s, {happy_70s}% of Americans called themselves \"very happy\". What is it in the 2020s?",
         "answer": share("happy = 1", "happy IS NOT NULL AND year >= 2020"),
         "reveal": f"It was still {share('happy = 1', 'happy IS NOT NULL AND year BETWEEN 2010 AND 2019')}% in the 2010s.",
         "source": "GSS", **pct},
        {"prompt": f"{poor_health}% of Americans in poor health say they are \"very happy\". What about those in excellent health?",
         "answer": share("happy = 1", "happy IS NOT NULL AND health = 1"),
         "reveal": f"Good health: {share('happy = 1', 'happy IS NOT NULL AND health = 2')}%. "
                   f"Fair: {share('happy = 1', 'happy IS NOT NULL AND health = 3')}%.", "source": "GSS", **pct},
        {"prompt": f"How many of the {ranked} countries ranked in {latest} score 7 or higher out of 10?",
         "answer": one(f"SELECT COUNT(*) FROM whr WHERE year = {latest} AND life_evaluation >= 7"),
         "min": 0, "max": ranked, "step": 1, "unit": "",
         "reveal": f"The middle-ranked country scores {median_score:.1f}.", "source": "WHR"},
    ]
    (OUT / "guesses.json").write_text(json.dumps(guesses, indent=2))
    return len(guesses)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(str(OUT / "happiness.duckdb"))
    counts = {}
    counts["gss"] = build_gss(con)
    counts["whr"] = build_whr(con)
    atus = build_atus(con)
    if atus is not None:
        counts["atus_wb"] = atus
    write_context(con, counts)
    print(f"guesses: {write_guesses(con)} cards")
    con.close()
    for table, n in counts.items():
        print(f"{table}: {n:,} rows")
    print(f"wrote {OUT / 'happiness.duckdb'} and {OUT / 'context.md'}")


if __name__ == "__main__":
    main()
