"""Course-code normalization.

York writes the same course many ways:
  "SC/MATH2030 Cr=3.00 EN"   (Kuali catalog id)
  "SC/MATH 1014 3.00"        (calendar descriptions)
  "SC MATH 1013 3.00"        (transcripts)
  "MATH 2030"                (students, advisors)
Everything is reduced to one CourseCode so sources can be joined.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Words that look like subject codes but are terms/sessions/notes.
_STOP = {"FALL", "FW", "SU", "WI", "YEAR", "TERM", "PRIOR", "NOTE", "AND", "OR", "THE", "OF",
         "CR", "NCR", "GPA", "TO", "IN", "AT", "LEVEL", "SUMMR"}
# Note: EN (English) and FR (French Studies) are real York subjects, so they are not stop words.

_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?P<fac>[A-Z]{2})\s*[/ ]\s*)?"        # faculty: SC/  AP/  LE  (optional)
    r"(?P<subj>[A-Z]{2,4})\s*"                  # subject: MATH, EN, EECS
    r"(?P<num>[1-6]\d{3})(?P<suffix>[A-Z]?)"    # number: 2030, 1000A
    r"(?![0-9])"
    r"(?:\s*(?:Cr\s*=\s*)?(?P<cr>\d{1,2}\.\d{2}))?"  # credits: 3.00 / Cr=3.00
)


@dataclass(frozen=True)
class CourseCode:
    subj: str
    num: str
    fac: Optional[str] = None
    credits: Optional[float] = None

    @property
    def key(self) -> str:
        """Faculty-independent key, e.g. 'MATH 2030'."""
        return f"{self.subj} {self.num}"

    @property
    def full(self) -> str:
        """Faculty-qualified key, e.g. 'SC/MATH 2030' (falls back to key)."""
        return f"{self.fac}/{self.key}" if self.fac else self.key

    @property
    def level(self) -> int:
        return int(self.num[0]) * 1000

    def same_course(self, other: "CourseCode") -> bool:
        if self.key != other.key:
            return False
        return self.fac is None or other.fac is None or self.fac == other.fac


def parse_code(text: str, known_subjects: Optional[set] = None) -> Optional[CourseCode]:
    codes = find_codes(text, known_subjects)
    return codes[0] if codes else None


def find_codes(text: str, known_subjects: Optional[Iterable[str]] = None) -> list[CourseCode]:
    """Every course code in a piece of text, in order, de-duplicated.

    Without a faculty prefix a match is accepted only if the subject is known
    (when a known-subject set is given) and is not a stop word, so 'FALL 2023'
    is never read as a course.
    """
    known = set(known_subjects) if known_subjects else None
    out: list[CourseCode] = []
    seen = set()
    for m in _CODE_RE.finditer(text or ""):
        fac, subj = m.group("fac"), m.group("subj")
        if subj in _STOP:
            continue
        if fac is None:
            if known is not None and subj not in known:
                continue
        elif fac in _STOP:
            fac = None
        cr = float(m.group("cr")) if m.group("cr") else None
        code = CourseCode(subj=subj, num=m.group("num") + m.group("suffix"), fac=fac, credits=cr)
        if code.full not in seen:
            seen.add(code.full)
            out.append(code)
    return out
