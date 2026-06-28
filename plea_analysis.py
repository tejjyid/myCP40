"""Summarise plea types (case_type) from surname search results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PleaSummary:
    total_rows: int = 0
    deduped_cases: int = 0
    with_plea: int = 0
    without_plea: int = 0
    plea_counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)


def normalize_plea(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text.strip())
    return cleaned or None


def case_dedup_key(row: dict[str, object]) -> str:
    parts = [
        str(row.get("year") or ""),
        str(row.get("term") or "").casefold(),
        str(row.get("county") or "").casefold(),
        normalize_plea(str(row.get("case_type") or "")) or "",
        str(row.get("plaintiff") or "").casefold(),
        str(row.get("defendant") or "").casefold(),
    ]
    return "|".join(parts)


def format_case_example(row: dict[str, object]) -> str:
    year = row.get("year") or "?"
    county = row.get("county") or "?"
    plaintiff = str(row.get("plaintiff") or "")[:60]
    defendant = str(row.get("defendant") or "")[:60]
    return f"{year} {county}: {plaintiff} v. {defendant}"


def dedupe_consecutive_cases(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return []

    deduped: list[dict[str, object]] = []
    previous_key: str | None = None

    for row in rows:
        key = case_dedup_key(row)
        if key == previous_key:
            continue
        deduped.append(row)
        previous_key = key

    return deduped


def analyze_pleas(rows: list[dict[str, object]]) -> PleaSummary:
    summary = PleaSummary(total_rows=len(rows))
    deduped = dedupe_consecutive_cases(rows)
    summary.deduped_cases = len(deduped)

    for row in deduped:
        plea = normalize_plea(str(row.get("case_type") or ""))
        label = plea or "(not stated)"
        if plea:
            summary.with_plea += 1
        else:
            summary.without_plea += 1

        summary.plea_counts[label] = summary.plea_counts.get(label, 0) + 1
        examples = summary.examples.setdefault(label, [])
        example = format_case_example(row)
        if len(examples) < 3 and example not in examples:
            examples.append(example)

    return summary


def plea_rows(summary: PleaSummary) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for plea, count in sorted(
        summary.plea_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        pct = (count / summary.deduped_cases * 100) if summary.deduped_cases else 0
        rows.append(
            {
                "plea": plea,
                "cases": count,
                "share_pct": round(pct, 1),
                "examples": "; ".join(summary.examples.get(plea, [])),
            }
        )
    return rows
