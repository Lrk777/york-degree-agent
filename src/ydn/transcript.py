"""Read a student's course history from pasted transcript text or a CSV.

Privacy: the raw text never leaves this function un-redacted. Names, student
numbers, emails, addresses and dates of birth are stripped first, and only
course rows are returned. Nothing is written to disk.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Optional

from .codes import CourseCode, find_codes

PASS_GRADES = {"A+", "A", "B+", "B", "C+", "C", "D+", "D", "P", "PASS", "TR", "CR"}
FAIL_GRADES = {"E", "F", "FAIL"}
IP_GRADES = {"IP", "INP", "", "-", "NGR"}
WITHDRAWN = {"W", "WD", "DRP"}

_GRADE_RE = re.compile(r"(?<![A-Za-z])(A\+|B\+|C\+|D\+|A|B|C|D|E|F|P|PASS|FAIL|IP|INP|NGR|W|WD|TR|CR)(?![A-Za-z+])")
_TERM_RE = re.compile(r"\b(20\d{2})\s*(FW|F|W|SU|S|Y|Fall|Winter|Summer)\b|\b(Fall|Winter|Summer|FW|SU)\s*(20\d{2})\b",
                      re.IGNORECASE)
_PII = [
    (re.compile(r"\b\d{9}\b"), "[STUDENT#]"),                         # York student numbers
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?im)^\s*(name|student name|student)\s*:.*$"), "[NAME LINE]"),
    (re.compile(r"(?im)^\s*(address|date of birth|dob|birth date)\s*:.*$"), "[PII LINE]"),
]


@dataclass
class Attempt:
    code: CourseCode
    credits: float
    grade: str
    status: str            # passed | failed | in_progress | withdrawn
    term: str = ""
    pass_fail: bool = False

    @property
    def counts(self) -> bool:
        return self.status in ("passed", "in_progress")


def redact(text: str) -> str:
    for pat, rep in _PII:
        text = pat.sub(rep, text)
    return text


def _status(grade: str) -> str:
    g = grade.upper()
    if g in PASS_GRADES:
        return "passed"
    if g in FAIL_GRADES:
        return "failed"
    if g in WITHDRAWN:
        return "withdrawn"
    return "in_progress"


def parse_transcript_text(text: str) -> list[Attempt]:
    """One course per line; tolerant of column order. Lines without a course code are ignored."""
    out: list[Attempt] = []
    for line in redact(text).splitlines():
        codes = find_codes(line)
        if not codes:
            continue
        code = codes[0]
        tail = line[line.find(code.num) + len(code.num):]
        cr = code.credits
        if cr is None:
            m = re.search(r"\b(\d{1,2}\.\d{2})\b", tail)
            cr = float(m.group(1)) if m else 3.0
        # grade = last grade-looking token after the course code
        grades = _GRADE_RE.findall(tail.replace(f"{cr:.2f}", " "))
        grade = grades[-1].upper() if grades else ""
        tm = _TERM_RE.search(line)
        term = (tm.group(0).upper() if tm else "")
        pf = bool(re.search(r"\b(P/F|pass/fail)\b", line, re.I))
        out.append(Attempt(CourseCode(code.subj, code.num, code.fac, cr), cr, grade, _status(grade), term, pf))
    return _dedupe(out)


def parse_transcript_csv(text: str) -> list[Attempt]:
    """CSV with columns: course, credits, grade[, term, pass_fail]."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        code = find_codes(row.get("course", ""))
        if not code:
            continue
        c = code[0]
        cr = float(row.get("credits") or c.credits or 3.0)
        g = (row.get("grade") or "").strip().upper()
        out.append(Attempt(CourseCode(c.subj, c.num, c.fac, cr), cr, g, _status(g),
                           row.get("term", ""), (row.get("pass_fail", "").lower() in ("1", "y", "yes", "true"))))
    return _dedupe(out)


def _dedupe(attempts: list[Attempt]) -> list[Attempt]:
    """York rule: for a repeated course only the latest attempt counts."""
    latest: dict[str, Attempt] = {}
    for a in attempts:
        latest[a.code.key] = a          # later rows win
    return list(latest.values())
