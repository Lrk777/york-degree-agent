"""Pull prerequisites, exclusions and flags out of a course description.

Kuali stores these as free text inside the description, e.g.
  "... Prerequisite: One of SC/MATH 1014 3.00, SC/MATH 1310 3.00.
   Course credit exclusions: GL/ECON 2710 3.00, AP/ECON 3580 3.00."
Prerequisites become a small expression (all-of / any-of groups). Anything that
is not a course (permission of instructor, '24 credits', a minimum grade) is kept
as a condition the engine reports for a human to check, never silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .codes import CourseCode, find_codes

_SECTION_RE = re.compile(
    r"(?P<label>Pre-?requisites?|Co-?requisites?|Course credit exclusions?|Not open to"
    r"|Cross-?listed(?: to| with)?|Note)\s*(?:\(s\))?\s*:?",
    re.IGNORECASE,
)
_GENED_RE = re.compile(r"approved .{0,60}general education", re.IGNORECASE)
_MIN_GRADE_RE = re.compile(r"(?:minimum|min\.?) grade of\s*([A-E][+]?)", re.IGNORECASE)
_NON_COURSE_RE = re.compile(
    r"permission|consent|credits? (?:completed|passed)|at least \d+ credits|\d+ credits|"
    r"enrol+ment in|registered in|students in|admission|standing|program",
    re.IGNORECASE,
)


@dataclass
class Prereq:
    """Conjunction of clauses; each clause is satisfied by ANY of its courses."""
    clauses: list[list[CourseCode]] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)   # non-course text for humans
    min_grade: str | None = None
    raw: str = ""

    @property
    def empty(self) -> bool:
        return not self.clauses and not self.conditions


@dataclass
class ParsedDescription:
    prereq: Prereq
    corequisites: list[CourseCode]
    exclusions: list[CourseCode]
    not_open_to: list[CourseCode]
    crosslisted: list[CourseCode]
    gen_ed_approved: bool
    text: str


def _sections(text: str) -> dict[str, str]:
    """Split description into labelled sections (first occurrence wins)."""
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        label = m.group("label").lower().replace("-", "")
        if label.startswith("prereq"):
            key = "prereq"
        elif label.startswith("coreq"):
            key = "coreq"
        elif label.startswith("course credit"):
            key = "exclusions"
        elif label.startswith("not open"):
            key = "not_open_to"
        elif label.startswith("cross"):
            key = "crosslisted"
        else:
            key = "note"
        out.setdefault(key, text[m.end():end].strip(" .;"))
    return out


def parse_prereq(raw: str) -> Prereq:
    p = Prereq(raw=raw)
    if not raw:
        return p
    g = _MIN_GRADE_RE.search(raw)
    if g:
        p.min_grade = g.group(1).upper()
    # Top level: ';' and ' and ' separate required clauses.
    parts = re.split(r";|\band\b(?![^()]*\))", raw)
    for part in parts:
        part = part.strip(" ,.")
        if not part:
            continue
        codes = find_codes(part)
        if codes:
            if re.search(r"\bone of\b|\bor\b|\beither\b", part, re.IGNORECASE) or len(codes) == 1:
                p.clauses.append(codes)
            else:
                # comma list without 'one of' / 'or' means each is required
                p.clauses.extend([[c] for c in codes])
            leftover = re.sub(r"(?:[A-Z]{2}\s*/\s*)?[A-Z]{2,4}\s*\d{4}[A-Z]?(?:\s*\d\.\d\d)?", "", part)
            if _NON_COURSE_RE.search(leftover):
                p.conditions.append(part)
        elif _NON_COURSE_RE.search(part) or len(part) > 3:
            p.conditions.append(part)
    return p


def parse_description(text: str) -> ParsedDescription:
    text = re.sub(r"\s+", " ", text or "").strip()
    s = _sections(text)
    return ParsedDescription(
        prereq=parse_prereq(s.get("prereq", "")),
        corequisites=find_codes(s.get("coreq", "")),
        exclusions=find_codes(s.get("exclusions", "")),
        not_open_to=find_codes(s.get("not_open_to", "")),
        crosslisted=find_codes(s.get("crosslisted", "")),
        gen_ed_approved=bool(_GENED_RE.search(text)),
        text=text,
    )
