"""Offline build step: turn raw survey downloads in data_raw/ into data/happiness.duckdb.

Sources (see README for download instructions):
  - GSS cumulative file 1972-2024 (NORC), Stata format: data_raw/gss7224_r3a.dta
  - World Happiness Report 2026, Figure 2.1 data:      data_raw/WHR26_Data_Figure_2.1.xlsx
  - ATUS Well-Being Module 2021 (BLS), zip files:      data_raw/wbact-2021.zip, wbresp-2021.zip,
                                                       atusact-2021.zip, atussum-2021.zip, atuswho-2021.zip

Also writes data/context.md: the schema notes the agents read before writing SQL.
Run with: uv run --group prep prepare_data.py
"""

import io
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
               "realinc", "coninc", "trust", "socrel", "socommun", "socfrend", "socbar", "attend",
               "wtssall", "wtssps"]


def build_gss(con: duckdb.DuckDBPyConnection) -> int:
    df = pd.read_stata(RAW / "gss7224_r3a.dta", columns=GSS_COLUMNS, convert_categoricals=False)
    # Stata extended missing codes come through as huge ints; treat anything out of range as missing.
    for col, labels in GSS_LABELS.items():
        codes = df[col].where(df[col].isin(labels))
        df[col] = codes
        df[col + "_label"] = codes.map(labels)
    df.loc[df["age"] > 89, "age"] = pd.NA  # 89 is top-coded "89 or older"
    df.loc[df["realinc"] <= 0, "realinc"] = pd.NA
    df.loc[df["coninc"] <= 0, "coninc"] = pd.NA
    df["weight"] = df["wtssps"].fillna(df["wtssall"]).fillna(1.0)
    df = df.rename(columns={"realinc": "family_income_1986usd", "coninc": "family_income_const_usd"})
    df = df.drop(columns=["wtssall", "wtssps"])
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
Weight every average/share by `weight` (e.g. SUM(weight * x) / SUM(weight)).
family_income_1986usd is inflation-adjusted to 1986 dollars (comparable across all years).""",
        "whr": f"""## whr — World Happiness Report 2026, {counts.get('whr', 0):,} country-year rows, 2011-2025 (Gallup World Poll)
One row per country per year. life_evaluation is the 0-10 Cantril ladder (3-year average).
The explained_by_* columns decompose each country's score into six factors
(GDP per capita, social support, healthy life expectancy, freedom, generosity, low corruption);
they plus dystopia_plus_residual sum to life_evaluation. rank is within that year.""",
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
    con.close()
    for table, n in counts.items():
        print(f"{table}: {n:,} rows")
    print(f"wrote {OUT / 'happiness.duckdb'} and {OUT / 'context.md'}")


if __name__ == "__main__":
    main()
