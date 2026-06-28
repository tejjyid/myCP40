"""Extract and summarize professions from CP40 party name fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from surname_search import Role

ProfessionKind = Literal["explicit", "status", "role", "missing"]

# Common county abbreviations appearing between place and occupation.
COUNTY_ABBREVS = {
    "beds", "berks", "bucks", "cams", "cambs", "cornw", "cumb", "derby",
    "devon", "dorset", "durham", "essex", "glos", "gloucs", "hants",
    "heref", "herefs", "herts", "hunts", "kent", "leics", "lincs", "linc",
    "manc", "middx", "monmouth", "nhants", "norf", "northants", "notts",
    "oxon", "rutland", "salop", "shrops", "somerset", "soms", "staffs", "suff",
    "suffk", "surrey", "sussex", "warks", "warws", "wilts", "worcs", "yorks",
}

STATUS_WORDS = {
    "gent", "gentleman", "esq", "yeoman", "husbandman", "widow", "widower",
    "clerk", "chaplain", "mercer", "draper", "butcher", "tailor", "smith",
    "miller", "weaver", "tanner", "glover", "salc", "salter", "fishmonger",
    "goldsmith", "blacksmith", "hosteler", "labourer", "laborer", "brewer",
    "baker", "carpenter", "shoemaker", "merchant", "chapman", "husbandman",
}

FORENAME_SUFFIXES = {"junior", "senior", "sen", "the elder", "the younger"}


def _strip_role_suffix(text: str) -> str | None:
    lower = text.casefold()
    for role in (
        "administratrix of",
        "administrator of",
        "executrix of",
        "executors of",
        "executor of",
    ):
        if role in lower:
            text = text[: lower.index(role)].strip(" ,")
            break
    return text or None

NON_PROFESSION_PHRASES = (
    "on her own account",
    "on his own account",
)

SPECIAL_PHRASES = (
    "single woman",
    "singlewoman",
    "prioress of",
    "prior of",
    "mayor of",
    "churchwardens of",
    "rector of",
    "vicar of",
    "yeoman of the crown",
)


@dataclass
class ParsedParty:
    raw_segment: str
    surname: str
    forename: str
    place: str | None = None
    profession: str | None = None
    profession_kind: ProfessionKind = "missing"
    person_key: str = ""


@dataclass
class ProfessionSummary:
    total_rows: int = 0
    total_segments: int = 0
    deduped_appearances: int = 0
    with_profession: int = 0
    without_profession: int = 0
    profession_counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)


def _clean_segment(segment: str) -> str:
    segment = segment.strip().rstrip(")")
    segment = re.sub(r"\s+", " ", segment)
    return segment


def _normalize_forename(forename: str) -> str:
    forename = forename.strip()
    forename = re.sub(r"\b(junior|senior|the elder|the younger)\b", "", forename, flags=re.I)
    return re.sub(r"\s+", " ", forename).strip().casefold()


def _person_key(surname: str, forename: str, place: str | None) -> str:
    place_key = re.sub(r"\s+", " ", (place or "").casefold()).strip()
    return f"{surname.casefold()}|{_normalize_forename(forename)}|{place_key}"


def _strip_non_profession_phrases(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text.strip())
    lower = cleaned.casefold()
    for phrase in NON_PROFESSION_PHRASES:
        if lower == phrase:
            return None
        suffix = f", {phrase}"
        if lower.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip(" ,")
            lower = cleaned.casefold()
    return cleaned or None


def _drop_non_profession_tail(remainder: list[str]) -> list[str]:
    if not remainder:
        return remainder
    last = remainder[-1].casefold().strip()
    if last in NON_PROFESSION_PHRASES:
        return remainder[:-1]
    return remainder


def _classify_profession(text: str | None) -> tuple[str | None, ProfessionKind]:
    text = _strip_non_profession_phrases(text)
    if not text:
        return None, "missing"

    cleaned = re.sub(r"\s+", " ", text.strip())
    cleaned = _strip_role_suffix(cleaned) or cleaned
    lower = cleaned.casefold()

    if lower in FORENAME_SUFFIXES:
        return None, "missing"

    for phrase in SPECIAL_PHRASES:
        if phrase in lower:
            return cleaned, "role" if "of" in phrase else "status"

    for role in ("executor of", "executors of", "executrix of", "administrator of", "administratrix of"):
        if lower.startswith(role) or lower == role.rstrip(" of"):
            return "(legal representative)", "role"

    first_word = lower.split()[0].rstrip(".")
    if first_word in STATUS_WORDS or first_word.endswith("man") or first_word.endswith("er"):
        kind: ProfessionKind = "status" if first_word in {"gent", "esq", "yeoman", "widow", "widower"} else "explicit"
        return cleaned, kind

    if lower in STATUS_WORDS:
        return cleaned, "status"

    return cleaned, "explicit"


def _looks_like_profession(text: str) -> bool:
    lower = text.casefold().strip()
    if not lower or lower in COUNTY_ABBREVS or lower.startswith("of "):
        return False
    if lower in NON_PROFESSION_PHRASES:
        return False
    first = lower.split()[0].rstrip(".")
    if first in STATUS_WORDS or first.endswith("man") or first.endswith("er"):
        return True
    return lower in STATUS_WORDS or any(phrase in lower for phrase in SPECIAL_PHRASES)


def _parse_remainder(remainder: list[str]) -> tuple[str | None, str | None]:
    remainder = _drop_non_profession_tail(remainder)
    if not remainder:
        return None, None

    if remainder[0].casefold().startswith("of "):
        if len(remainder) == 1:
            return remainder[0][3:].strip(), None

        last = remainder[-1]
        if _looks_like_profession(last):
            place_parts = remainder[:-1]
            profession = last
        else:
            place_parts = remainder
            profession = None

        place_chunks: list[str] = []
        for index, part in enumerate(place_parts):
            if index == 0 and part.casefold().startswith("of "):
                place_chunks.append(part[3:].strip())
            elif part.casefold() in COUNTY_ABBREVS:
                place_chunks.append(part)
            else:
                place_chunks.append(part)
        return ", ".join(place_chunks), profession

    joined = ", ".join(remainder).strip()
    return None, joined or None


def parse_party_segment(segment: str) -> ParsedParty | None:
    segment = _clean_segment(segment)
    if not segment or "," not in segment:
        return None

    lower = segment.casefold()
    for phrase in SPECIAL_PHRASES:
        if phrase in lower:
            parts = [part.strip() for part in segment.split(",", 2)]
            if len(parts) < 2:
                return None
            surname, forename = parts[0], parts[1]
            tail = segment.split(",", 2)[-1].strip()
            place = None
            profession = tail
            if tail.casefold().startswith("of "):
                place = tail[3:].strip()
                profession = phrase
            parsed = ParsedParty(
                raw_segment=segment,
                surname=surname,
                forename=forename,
                place=place,
            )
            parsed.profession, parsed.profession_kind = _classify_profession(profession)
            parsed.person_key = _person_key(parsed.surname, parsed.forename, parsed.place)
            return parsed

    parts = [part.strip() for part in segment.split(",")]
    if len(parts) < 2:
        return None

    surname = parts[0]
    forename = parts[1]
    remainder = parts[2:]

    while remainder and remainder[0].casefold() in FORENAME_SUFFIXES:
        forename = f"{forename}, {remainder[0]}"
        remainder = remainder[1:]

    place, profession = _parse_remainder(remainder)
    profession = _strip_role_suffix(profession) if profession else profession
    profession = _strip_non_profession_phrases(profession)

    if not profession:
        for role in ("executors of", "executor of", "executrix of", "administrator of", "administratrix of"):
            if role in lower:
                profession = "(legal representative)"
                break

    parsed = ParsedParty(
        raw_segment=segment,
        surname=surname,
        forename=forename,
        place=place,
    )
    parsed.profession, parsed.profession_kind = _classify_profession(profession)
    parsed.person_key = _person_key(parsed.surname, parsed.forename, parsed.place)
    return parsed


def extract_matching_segments(field: str, surnames: list[str]) -> list[str]:
    if not field:
        return []

    surname_keys = {name.casefold() for name in surnames}
    found: list[str] = []
    seen: set[str] = set()

    def add(segment: str) -> None:
        cleaned = _clean_segment(segment)
        if not cleaned or cleaned in seen:
            return
        first = cleaned.split(",", 1)[0].strip().casefold()
        if first in surname_keys:
            seen.add(cleaned)
            found.append(cleaned)

    for chunk in field.split("; "):
        chunk = chunk.strip()
        if not chunk:
            continue

        for match in re.finditer(r"\(([^)]+)\)", chunk):
            add(match.group(1))

        base = re.sub(r"\([^)]*\)", "", chunk).strip(" ;")
        if base:
            add(base)

    return found


def extract_parties_from_row(
    row: dict[str, object],
    surnames: list[str],
    role: Role,
) -> list[ParsedParty]:
    parties: list[ParsedParty] = []
    fields: list[str] = []
    if role in ("plaintiff", "both") and row.get("plaintiff"):
        fields.append(str(row["plaintiff"]))
    if role in ("defendant", "both") and row.get("defendant"):
        fields.append(str(row["defendant"]))

    for field_text in fields:
        for segment in extract_matching_segments(field_text, surnames):
            parsed = parse_party_segment(segment)
            if parsed:
                parties.append(parsed)
    return parties


def dedupe_consecutive(parties: list[ParsedParty]) -> list[ParsedParty]:
    if not parties:
        return []

    deduped: list[ParsedParty] = []
    previous_key: str | None = None

    for party in parties:
        if party.person_key == previous_key:
            if not deduped[-1].profession and party.profession:
                deduped[-1] = party
            continue
        deduped.append(party)
        previous_key = party.person_key

    return deduped


def analyze_professions(
    rows: list[dict[str, object]],
    surnames: list[str],
    role: Role = "plaintiff",
) -> ProfessionSummary:
    summary = ProfessionSummary(total_rows=len(rows))
    sequential: list[ParsedParty] = []

    for row in rows:
        parties = extract_parties_from_row(row, surnames, role)
        summary.total_segments += len(parties)
        sequential.extend(parties)

    deduped = dedupe_consecutive(sequential)
    summary.deduped_appearances = len(deduped)

    for party in deduped:
        label = party.profession or "(not stated)"
        if party.profession:
            summary.with_profession += 1
        else:
            summary.without_profession += 1

        summary.profession_counts[label] = summary.profession_counts.get(label, 0) + 1
        examples = summary.examples.setdefault(label, [])
        if len(examples) < 3 and party.raw_segment not in examples:
            examples.append(party.raw_segment)

    return summary


def profession_rows(summary: ProfessionSummary) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profession, count in sorted(
        summary.profession_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        pct = (count / summary.deduped_appearances * 100) if summary.deduped_appearances else 0
        rows.append(
            {
                "profession": profession,
                "appearances": count,
                "share_pct": round(pct, 1),
                "examples": "; ".join(summary.examples.get(profession, [])),
            }
        )
    return rows
