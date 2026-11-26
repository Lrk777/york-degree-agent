"""In-memory course catalog for one calendar year, plus bucket filters."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .codes import CourseCode, find_codes
from .descparse import ParsedDescription, parse_description


@dataclass
class Course:
    code: CourseCode                 # faculty-qualified, with credits
    title: str
    desc: ParsedDescription
    pid: str = ""
    crosslisted_faculties: set[str] = field(default_factory=set)

    @property
    def credits(self) -> float:
        return self.code.credits or 0.0

    @property
    def subject_full(self) -> str:
        return f"{self.code.fac}/{self.code.subj}" if self.code.fac else self.code.subj


class Catalog:
    def __init__(self, year: str, courses: list[Course]):
        self.year = year
        self.courses = courses
        self._by_full = {c.code.full: c for c in courses}
        self._by_key: dict[str, list[Course]] = {}
        for c in courses:
            self._by_key.setdefault(c.code.key, []).append(c)
        self.subjects = {c.code.subj for c in courses}
        # 'anthropology' -> {'AP/ANTH'}; filled from Kuali subject descriptions when available
        self.subject_names: dict[str, set[str]] = {}
        self.offerings: dict[str, list[str]] = {}      # 'SC/MATH 3330' -> ['F'] (current year)

    def add_subject_desc(self, subj_full: str, desc: str):
        # "Anthropology (ANTH) - Faculty of Liberal Arts and Professional Studies"
        m = re.match(r"\s*(.+?)\s*\(([A-Z]{2,4})\)", desc or "")
        if m:
            name = m.group(1).lower().replace("&", "and")
            self.subject_names.setdefault(name, set()).add(subj_full)

    def get(self, code: CourseCode) -> Optional[Course]:
        if code.fac and code.full in self._by_full:
            return self._by_full[code.full]
        hits = self._by_key.get(code.key, [])
        return hits[0] if hits else None

    @classmethod
    def from_export(cls, year: str, data: dict) -> "Catalog":
        """Load the compact JSON written by export.py (pre-parsed, so the browser does no regex work)."""
        from .descparse import ParsedDescription, Prereq
        courses = []
        for r in data["courses"]:
            c = find_codes(r["c"])[0]
            code = CourseCode(c.subj, c.num, c.fac, float(r.get("cr") or 0))
            pr = Prereq(clauses=[[find_codes(x)[0] for x in cl] for cl in r.get("pq", [])],
                        conditions=r.get("pc", []), min_grade=r.get("pg"), raw=r.get("pr", ""))
            desc = ParsedDescription(pr, [], [find_codes(x)[0] for x in r.get("ex", [])], [],
                                     [find_codes(x)[0] for x in r.get("xl", [])], bool(r.get("ge")), r.get("d", ""))
            courses.append(Course(code, r.get("t", ""), desc, r.get("pid", ""),
                                  {x.split("/")[0] for x in r.get("xl", [])}))
        cat = cls(year, courses)
        for subj, desc in data.get("subjects", {}).items():
            cat.add_subject_desc(subj, desc)
        cat.offerings = data.get("offerings", {})
        return cat

    @classmethod
    def from_records(cls, year: str, records: list[dict]) -> "Catalog":
        """records: {'id': 'SC/MATH2030 Cr=3.00 EN', 'title':..., 'description':..., 'credits': 3.0}"""
        courses = []
        for r in records:
            codes = find_codes(r["id"])
            if not codes:
                continue
            c = codes[0]
            credits = r.get("credits") or c.credits or 0.0
            code = CourseCode(c.subj, c.num, c.fac, float(credits))
            desc = parse_description(r.get("description", ""))
            xfac = {x.fac for x in desc.crosslisted if x.fac}
            courses.append(Course(code, r.get("title", ""), desc, r.get("pid", ""), xfac))
        return cls(year, courses)


# ---------------------------------------------------------------- buckets

@dataclass
class BucketSpec:
    id: str
    name: str
    match: str
    spec: dict

    def check(self, course: Course, major_subjects: set[str] = frozenset(),
              pass_fail: bool = False) -> tuple[bool, str]:
        inc, exc = self.spec.get("include", {}), self.spec.get("exclude", {})
        code = course.code
        if pass_fail and exc.get("pass_fail"):
            return False, "taken pass/fail, which this requirement does not accept"
        if code.full in exc.get("courses", []) or code.key in exc.get("courses", []):
            return False, f"{code.full} is specifically excluded"
        bad_x = set(exc.get("crosslisted_with_faculties", []))
        if bad_x & (course.crosslisted_faculties | {x.fac for x in course.desc.crosslisted if x.fac}):
            return False, "cross-listed with a Faculty of Science course"
        bad_e = set(exc.get("exclusion_with_faculties", []))
        if bad_e and any(e.fac in bad_e for e in course.desc.exclusions):
            return False, "has a Faculty of Science course as a credit exclusion"
        if exc.get("major_subjects") and code.subj in major_subjects:
            return False, f"{code.subj} is part of your major"
        if inc.get("any"):
            return True, "any course counts as an elective"
        if course.subject_full in inc.get("subjects", []):
            return True, f"all {course.subject_full} courses are an approved area"
        if code.full in inc.get("courses", []):
            return True, f"{code.full} is on the approved list"
        return False, "not on the approved list for this requirement"


class BucketLibrary:
    def __init__(self, specs: list[BucketSpec]):
        self.specs = specs

    @classmethod
    def load(cls, path: str | Path) -> "BucketLibrary":
        import yaml
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))

    @classmethod
    def from_dict(cls, data: dict) -> "BucketLibrary":
        return cls([BucketSpec(b["id"], b["name"], b["match"], b) for b in data["buckets"]])

    def for_text(self, text: str, program_faculty: str = "") -> Optional[BucketSpec]:
        for s in self.specs:
            facs = s.spec.get("program_faculties")
            if facs and program_faculty and program_faculty not in facs:
                continue
            if re.search(re.escape(s.match), text, re.I):
                return s
        return None
