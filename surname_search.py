"""Search CP40 cases by party surname."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Role = Literal["plaintiff", "defendant", "both"]
SearchOrder = Literal["display", "analysis"]

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "cp40.db"

DETAIL_COLUMNS = (
    "year",
    "term",
    "county",
    "plaintiff",
    "defendant",
    "case_type",
    "source_file",
)

ANALYSIS_COLUMNS = ("id",) + DETAIL_COLUMNS


@dataclass
class DateRange:
    start_year: int
    period_years: int
    db_min_year: int | None = None
    db_max_year: int | None = None

    @property
    def end_year(self) -> int:
        return self.start_year + self.period_years - 1

    @property
    def is_full_database(self) -> bool:
        if self.db_min_year is None or self.db_max_year is None:
            return False
        return self.start_year <= self.db_min_year and self.end_year >= self.db_max_year

    def label(self) -> str:
        if self.is_full_database:
            if self.db_min_year is not None and self.db_max_year is not None:
                return f"full database ({self.db_min_year}-{self.db_max_year}, incl. undated)"
            return "full database"
        return f"{self.start_year}-{self.end_year}"

    def sql_filter(self) -> tuple[str, list[int]]:
        if self.is_full_database:
            return "", []
        return (
            "year IS NOT NULL AND year >= ? AND year <= ?",
            [self.start_year, self.end_year],
        )


def parse_surnames(primary: str, variants: str | None = None) -> list[str]:
    """Return deduplicated surname list from primary input and optional variants."""
    names: list[str] = []
    seen: set[str] = set()
    for part in [primary, *(variants.split(",") if variants else [])]:
        name = part.strip()
        if not name:
            continue
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def surname_filter_sql(column: str, surnames: list[str]) -> tuple[str, list[str]]:
    """Build a WHERE fragment matching surname at start or after '; '."""
    if not surnames:
        raise ValueError("At least one surname is required")

    parts: list[str] = []
    params: list[str] = []
    for name in surnames:
        parts.append(f"{column} LIKE ?")
        params.append(f"{name},%")
        parts.append(f"{column} LIKE ?")
        params.append(f"%; {name},%")
    return f"({' OR '.join(parts)})", params


def role_filter_sql(role: Role, surnames: list[str]) -> tuple[str, list[str]]:
    if role == "plaintiff":
        return surname_filter_sql("plaintiff", surnames)
    if role == "defendant":
        return surname_filter_sql("defendant", surnames)
    plaintiff_sql, plaintiff_params = surname_filter_sql("plaintiff", surnames)
    defendant_sql, defendant_params = surname_filter_sql("defendant", surnames)
    return f"({plaintiff_sql} OR {defendant_sql})", plaintiff_params + defendant_params


def get_year_bounds(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        "SELECT MIN(year), MAX(year) FROM cases WHERE year IS NOT NULL"
    ).fetchone()
    if row[0] is None or row[1] is None:
        raise ValueError("Database has no dated cases")
    return int(row[0]), int(row[1])


def full_date_range(conn: sqlite3.Connection) -> DateRange:
    db_min, db_max = get_year_bounds(conn)
    return DateRange(
        start_year=db_min,
        period_years=db_max - db_min + 1,
        db_min_year=db_min,
        db_max_year=db_max,
    )


def make_date_range(
    conn: sqlite3.Connection,
    start_year: int | None = None,
    period_years: int | None = None,
) -> DateRange:
    db_min, db_max = get_year_bounds(conn)
    return DateRange(
        start_year=start_year if start_year is not None else db_min,
        period_years=period_years if period_years is not None else db_max - db_min + 1,
        db_min_year=db_min,
        db_max_year=db_max,
    )


def search_where_sql(
    role: Role,
    surnames: list[str],
    date_range: DateRange | None = None,
) -> tuple[str, list[object]]:
    where_sql, params = role_filter_sql(role, surnames)
    if date_range is None:
        return where_sql, params
    date_sql, date_params = date_range.sql_filter()
    if not date_sql:
        return where_sql, params
    return f"{where_sql} AND {date_sql}", [*params, *date_params]


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def count_matches(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    date_range: DateRange | None = None,
) -> int:
    where_sql, params = search_where_sql(role, surnames, date_range)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM cases WHERE {where_sql}",
        params,
    ).fetchone()
    return int(row["n"])


def count_by_county(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    date_range: DateRange | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = search_where_sql(role, surnames, date_range)
    return conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(county), ''), '(no county)') AS county,
            COUNT(*) AS case_count
        FROM cases
        WHERE {where_sql}
        GROUP BY county
        ORDER BY case_count DESC, county
        """,
        params,
    ).fetchall()


def count_by_period(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    date_range: DateRange | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = search_where_sql(role, surnames, date_range)
    return conn.execute(
        f"""
        SELECT
            CASE
                WHEN year IS NULL THEN '(no year)'
                ELSE printf('%d-%d', (year / 50) * 50, (year / 50) * 50 + 49)
            END AS period,
            COUNT(*) AS case_count
        FROM cases
        WHERE {where_sql}
        GROUP BY period
        ORDER BY
            CASE WHEN period = '(no year)' THEN 1 ELSE 0 END,
            period
        """,
        params,
    ).fetchall()


def count_by_interval(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    date_range: DateRange | None = None,
    interval_years: int = 50,
    num_bars: int = 5,
) -> list[dict[str, object]]:
    """Count cases in exactly num_bars consecutive year buckets of interval_years width."""
    if interval_years < 1 or num_bars < 1:
        raise ValueError("interval_years and num_bars must be positive")

    if date_range is None:
        db_min, _db_max = get_year_bounds(conn)
        start_year = db_min
    else:
        start_year = date_range.start_year

    where_sql, params = search_where_sql(role, surnames, date_range)
    bucket_specs: list[tuple[str, int, int]] = []
    select_parts: list[str] = []
    case_params: list[int] = []
    for idx in range(num_bars):
        bucket_start = start_year + idx * interval_years
        bucket_end = bucket_start + interval_years - 1
        label = f"{bucket_start}-{bucket_end}"
        bucket_specs.append((label, bucket_start, bucket_end))
        select_parts.append(
            f"SUM(CASE WHEN year >= ? AND year <= ? THEN 1 ELSE 0 END) AS b{idx}"
        )
        case_params.extend([bucket_start, bucket_end])

    row = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM cases
        WHERE {where_sql} AND year IS NOT NULL
        """,
        [*case_params, *params],
    ).fetchone()

    return [
        {"period": label, "case_count": int(row[f"b{idx}"] or 0)}
        for idx, (label, _start, _end) in enumerate(bucket_specs)
    ]


def search_cases(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    limit: int | None = None,
    order: SearchOrder = "display",
    date_range: DateRange | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = search_where_sql(role, surnames, date_range)
    if order == "analysis":
        columns = ANALYSIS_COLUMNS
        order_clause = "source_file, id"
    else:
        columns = DETAIL_COLUMNS
        order_clause = "year, county, plaintiff, defendant"

    sql = f"""
        SELECT {", ".join(columns)}
        FROM cases
        WHERE {where_sql}
        ORDER BY {order_clause}
    """
    if limit is not None:
        sql += " LIMIT ?"
        params = [*params, limit]
    return conn.execute(sql, params).fetchall()


def search_cases_for_analysis(
    conn: sqlite3.Connection,
    surnames: list[str],
    role: Role = "plaintiff",
    date_range: DateRange | None = None,
) -> list[sqlite3.Row]:
    """Return all matching rows in source order for profession deduplication."""
    return search_cases(conn, surnames, role, order="analysis", date_range=date_range)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]
