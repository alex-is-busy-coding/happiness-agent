"""The survey database: read-only queries, and what the masthead says is in it."""

import duckdb

from desk.config import DB_PATH, MAX_ROWS


def is_read_only(sql: str) -> bool:
    return sql.strip().rstrip(";").lower().startswith(("select", "with"))


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return "NULL" if value is None else str(value)


def run_query(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Run one query and return the result as a markdown table, capped at max_rows."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cursor = con.execute(sql.strip().rstrip(";"))
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
    finally:
        con.close()
    note = ""
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        note = f"\n(More than {max_rows} rows. Aggregate more if you need the rest.)"
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines) + note


def summary() -> dict:
    """How much data each table holds, counted from the database."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        found = {}
        if "gss" in tables:
            people, first, last = con.execute("SELECT COUNT(*), MIN(year), MAX(year) FROM gss").fetchone()
            found["gss"] = {"people": people, "first": int(first), "last": int(last)}
        if "whr" in tables:
            countries, first, last = con.execute(
                "SELECT COUNT(DISTINCT country), MIN(year), MAX(year) FROM whr"
            ).fetchone()
            found["whr"] = {"countries": countries, "first": int(first), "last": int(last)}
        if "atus_wb" in tables:
            episodes, people = con.execute("SELECT COUNT(*), COUNT(DISTINCT case_id) FROM atus_wb").fetchone()
            found["atus_wb"] = {"episodes": episodes, "people": people}
        return found
    finally:
        con.close()
