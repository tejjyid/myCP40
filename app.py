#!/usr/bin/env python3
"""Simple web UI for CP40 surname search. Run with: streamlit run app.py"""

from __future__ import annotations

import io
from pathlib import Path

import streamlit as st

from db_setup import describe_database, ensure_database
from plea_analysis import analyze_pleas, plea_rows
from profession_analysis import analyze_professions, profession_rows
from surname_search import (
    count_by_county,
    count_by_interval,
    count_matches,
    connect,
    get_year_bounds,
    make_date_range,
    parse_surnames,
    rows_to_dicts,
    search_cases,
)

INTERVAL_OPTIONS = list(range(5, 255, 5))
CHART_INTERVALS = 5

st.set_page_config(page_title="CP40 Surname Search", page_icon="⚖️", layout="wide")

@st.cache_resource(show_spinner="Preparing database…")
def get_database_path() -> str:
    return str(ensure_database())

try:
    DB_PATH = get_database_path()
except FileNotFoundError as exc:
    st.error(str(exc))
    st.info(
        "Local: run `python import_cp40.py` for the full database, or "
        "`python build_subset_db.py` for cp40_web.db. "
        "Cloud: commit cp40_web.db to the repo (see README.md)."
    )
    st.stop()

@st.cache_data(show_spinner=False)
def load_year_bounds(db_path: str) -> tuple[int, int]:
    with connect(Path(db_path)) as conn:
        return get_year_bounds(conn)

st.title("CP40 Surname Search")
st.caption(
    "Search Court of Common Pleas index cases by party surname. "
    "Matches surnames at the start of a name field or after `; `. "
    f"Database: **{describe_database(Path(DB_PATH))}**."
)

with st.sidebar:
    st.header("Search")
    surname = st.text_input("Surname", placeholder="e.g. Webbe")
    variants = st.text_input(
        "Extra spellings (optional)",
        placeholder="e.g. Webb, Webber, Webbes",
        help="Comma-separated variants, same as --variants on the CLI.",
    )
    role = st.radio("Search as", ("plaintiff", "defendant", "both"), horizontal=True)

    try:
        db_min_year, db_max_year = load_year_bounds(DB_PATH)
    except (FileNotFoundError, ValueError) as exc:
        db_min_year, db_max_year = 1349, 1596
        st.caption(f"Date range defaults only: {exc}")

    st.subheader("Date range")
    start_year = st.number_input(
        "Start year",
        min_value=db_min_year,
        max_value=db_max_year,
        value=db_min_year,
        step=1,
    )
    db_span = db_max_year - db_min_year + 1
    default_report_span = min(250, ((db_span + 4) // 5) * 5)
    report_span_years = st.select_slider(
        "Report span (years)",
        options=INTERVAL_OPTIONS,
        value=default_report_span,
        help="Total years covered by the search and chart. Must be a multiple of 5 (max 250).",
    )
    period_years = int(report_span_years)
    bar_interval_years = period_years // CHART_INTERVALS
    end_year = int(start_year) + period_years - 1
    if end_year > db_max_year:
        st.warning(f"End year {end_year} exceeds database maximum ({db_max_year}).")
    st.caption(
        f"End year: {end_year} · Chart: {CHART_INTERVALS} bars of "
        f"{bar_interval_years} years each ({period_years} years total)"
    )

    max_rows = st.number_input("Max rows to display", min_value=10, max_value=5000, value=200, step=10)
    analyze_professions_option = st.checkbox(
        "Analyse professions",
        value=True,
        help=(
            "Summarise occupations/statuses from name fields. "
            "Consecutive rows for the same person are counted once; "
            "many entries have no explicit profession."
        ),
    )
    analyze_pleas_option = st.checkbox(
        "Analyse pleas",
        value=False,
        help=(
            "Summarise plea types from the case_type column. "
            "Consecutive duplicate case rows are counted once."
        ),
    )

search_clicked = st.button("Search", type="primary", use_container_width=True)

if search_clicked:
    surnames = parse_surnames(surname, variants or None)
    if not surnames:
        st.error("Please enter a surname.")
    else:
        try:
            conn = connect(Path(DB_PATH))
        except FileNotFoundError as exc:
            st.error(str(exc))
        else:
            with conn:
                date_range = make_date_range(
                    conn,
                    start_year=int(start_year),
                    period_years=int(period_years),
                )
                total = count_matches(conn, surnames, role, date_range)
                county_rows = rows_to_dicts(count_by_county(conn, surnames, role, date_range))
                period_rows = count_by_interval(
                    conn,
                    surnames,
                    role,
                    date_range,
                    interval_years=bar_interval_years,
                    num_bars=CHART_INTERVALS,
                )
                detail_rows = rows_to_dicts(
                    search_cases(conn, surnames, role, limit=int(max_rows), date_range=date_range)
                )
                profession_summary = None
                profession_table: list[dict[str, object]] = []
                plea_summary = None
                plea_table: list[dict[str, object]] = []
                analysis_rows: list[dict[str, object]] = []
                if analyze_professions_option or analyze_pleas_option:
                    analysis_rows = rows_to_dicts(
                        search_cases(conn, surnames, role, order="analysis", date_range=date_range)
                    )
                if analyze_professions_option:
                    profession_summary = analyze_professions(analysis_rows, surnames, role)
                    profession_table = profession_rows(profession_summary)
                if analyze_pleas_option:
                    plea_summary = analyze_pleas(analysis_rows)
                    plea_table = plea_rows(plea_summary)

            st.session_state["results"] = {
                "surnames": surnames,
                "role": role,
                "date_range_label": date_range.label(),
                "report_span_years": period_years,
                "bar_interval_years": bar_interval_years,
                "total": total,
                "county_rows": county_rows,
                "period_rows": period_rows,
                "detail_rows": detail_rows,
                "max_rows": int(max_rows),
                "profession_summary": profession_summary,
                "profession_table": profession_table,
                "plea_summary": plea_summary,
                "plea_table": plea_table,
            }

if "results" in st.session_state:
    results = st.session_state["results"]
    surnames = results["surnames"]
    total = results["total"]

    st.subheader(f"{', '.join(surnames)} ({results['role']})")
    st.caption(f"Date range: {results.get('date_range_label', 'full database')}")
    st.metric("Matching cases", f"{total:,}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**By county**")
        if results["county_rows"]:
            st.bar_chart(
                {row["county"]: row["case_count"] for row in results["county_rows"]},
                horizontal=True,
            )
            st.dataframe(results["county_rows"], use_container_width=True, hide_index=True)
        else:
            st.info("No results.")

    with col2:
        bar_years = results.get("bar_interval_years", 50)
        st.markdown(f"**Cases per {bar_years}-year interval**")
        if results["period_rows"]:
            st.bar_chart({row["period"]: row["case_count"] for row in results["period_rows"]})
            st.dataframe(results["period_rows"], use_container_width=True, hide_index=True)
        else:
            st.info("No results.")

    if results.get("profession_summary"):
        summary = results["profession_summary"]
        stated_pct = (
            summary.with_profession / summary.deduped_appearances * 100
            if summary.deduped_appearances
            else 0
        )
        st.markdown("**Profession analysis**")
        st.caption(
            "Parsed from the surname party's name field. Consecutive appearances of the "
            "same person (same forename and place) are counted once. Many index entries "
            "give only a name, or state a place without an occupation."
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Case rows analysed", f"{summary.total_rows:,}")
        m2.metric("Party mentions", f"{summary.total_segments:,}")
        m3.metric("After dedup", f"{summary.deduped_appearances:,}")
        m4.metric("Profession stated", f"{stated_pct:.1f}%")

        prof_rows = [
            row for row in results["profession_table"]
            if row["profession"] != "(not stated)"
        ]
        if prof_rows:
            st.bar_chart(
                {row["profession"]: row["appearances"] for row in prof_rows[:20]},
                horizontal=True,
            )
        st.dataframe(results["profession_table"], use_container_width=True, hide_index=True)

    if results.get("plea_summary"):
        summary = results["plea_summary"]
        stated_pct = (
            summary.with_plea / summary.deduped_cases * 100
            if summary.deduped_cases
            else 0
        )
        st.markdown("**Plea analysis**")
        st.caption(
            "From the case_type column. Consecutive rows with the same year, term, "
            "county, plea, plaintiff, and defendant are counted once."
        )
        p1, p2, p3 = st.columns(3)
        p1.metric("Case rows analysed", f"{summary.total_rows:,}")
        p2.metric("After dedup", f"{summary.deduped_cases:,}")
        p3.metric("Plea stated", f"{stated_pct:.1f}%")

        plea_chart_rows = [
            row for row in results["plea_table"]
            if row["plea"] != "(not stated)"
        ]
        if plea_chart_rows:
            st.bar_chart(
                {row["plea"]: row["cases"] for row in plea_chart_rows[:20]},
                horizontal=True,
            )
        st.dataframe(results["plea_table"], use_container_width=True, hide_index=True)

    st.markdown("**Matching cases**")
    shown = len(results["detail_rows"])
    if results["detail_rows"]:
        st.dataframe(results["detail_rows"], use_container_width=True, hide_index=True)
        if total > shown:
            st.caption(f"Showing {shown:,} of {total:,} rows.")
    else:
        st.info("No matching cases.")

    if results["detail_rows"]:
        import csv

        buffer = io.StringIO()
        columns = list(results["detail_rows"][0].keys())
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results["detail_rows"])
        st.download_button(
            "Download shown rows as CSV",
            buffer.getvalue(),
            file_name=f"cp40_{'_'.join(surnames).lower()}.csv",
            mime="text/csv",
        )
else:
    st.info("Enter a surname in the sidebar and click Search.")

st.divider()
st.markdown(
    "**CLI:** `python search_surname.py Webbe --variants Webb,Webber,Webbes`"
)
