"""Interpret free-text requirement rules into course filters.

York's calendar has hundreds of free-text rules ("additional credits at the 3000 or
4000 level in anthropology", "outside the major", "non-MATH credits", "free
electives"). Most are built from a few parts:

  scope  – which courses: any / the major / outside the major / named subjects /
           science disciplines outside the major / not-these-subjects
  level  – which levels: "at the 3000 level", "3000 or 4000", "3000 level or above", "any level"
  extras – minimum grade, notes

Rules with a category we can't check yet (approved general-education lists, language
groups, lab courses, internationally-oriented lists) are reported as "needs a list"
and go to an advisor. If a rule has wording we don't understand, it is NOT guessed:
the result is marked unparsed and shown to the student as "confirm with an advisor".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

LEVELS = (1000, 2000, 3000, 4000)

# Science disciplines as listed in the calendar ("computer science, kinesiology and
# health science, psychology, biology, biochemistry, biophysics, chemistry, environmental
# biology, earth and atmospheric science, geography, physics and astronomy, science and
# technology studies"). Faculty-qualified codes.
SCIENCE_SUBJECTS = {"LE/EECS", "HH/KINE", "HH/PSYC", "SC/BIOL", "SC/BCHM", "SC/BPHS", "SC/CHEM", "SC/ENVB",
                    "LE/ESSE", "SC/GEOG", "SC/PHYS", "SC/STS", "SC/MATH", "SC/NRSC"}

_NEEDS_LIST = [
    (r"general education", "the Faculty's approved general-education course list"),
    (r"humanities category|social science category|natural science category|modes of reasoning|"
     r"humanities or social science or natural science|humanities, (?:modes|natural)|\bNATS\b",
     "the general-education category lists (humanities / social science / natural science)"),
    (r"internationally[- ]oriented|pre-approved", "the program's pre-approved course list"),
    (r"laborator", "the list of courses with laboratories"),
    (r"\bgroup \d", "the program's course group lists"),
    (r"modern language|language study|taught in french|in french|language transfer|translation",
     "language course rules"),
    (r"exchange", "exchange-term rules"),
    (r"interdisciplinary program|teaching subject", "program-specific lists"),
    (r"in consultation with|approved by|permission", "approval by the department"),
]
_META = [
    (r"deemed fulfilled by the other program", "double-major / major-minor note"),
    (r"should first be taken to fulfil", None),        # ordering advice on an elective block
]
_FILLER = set("""credits credit additional at the level levels or and above higher in from of courses course to be
must which including include least a an any other for this that with are is fulfil fulfill
upper-level upper requirement requirements total overall chosen selected students may can used
series rubric i.e. offered by e.g. normally subject area areas following plus up also one two three
six nine twelve n their your his her it its as such on within inside both either each no more than
counting towards toward count counts satisfy satisfied minimum maximum complete completed courses.
discipline disciplines studies study major minor outside excluding except non free choice elective
electives department departments faculty faculties program programs degree""".split())


@dataclass
class Rule:
    any: bool = False
    major: bool = False
    minor: bool = False
    outside_major: bool = False
    science_outside_major: bool = False
    subjects: set[str] = field(default_factory=set)          # 'AP/ANTH' or bare 'ANTH'
    exclude_subjects: set[str] = field(default_factory=set)
    faculties: set[str] = field(default_factory=set)
    levels: Optional[set[int]] = None
    min_grade: Optional[str] = None
    needs: Optional[str] = None          # a list we don't have -> advisor
    note: Optional[str] = None
    unparsed: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        has_scope = self.any or self.major or self.outside_major or self.science_outside_major or bool(self.subjects) \
            or bool(self.faculties) or bool(self.exclude_subjects)
        return self.needs is None and not self.unparsed and not self.minor and has_scope

    def describe(self) -> str:
        parts = []
        if self.any:
            parts.append("any course")
        if self.major:
            parts.append("courses in your major")
        if self.outside_major:
            parts.append("courses outside your major")
        if self.science_outside_major:
            parts.append("science courses outside your major")
        if self.subjects:
            parts.append("courses in " + ", ".join(sorted(self.subjects)))
        if self.faculties:
            parts.append("courses offered by " + ", ".join(sorted(self.faculties)))
        if self.exclude_subjects:
            parts.append("not " + ", ".join(sorted(self.exclude_subjects)))
        if self.levels:
            parts.append("at the " + "/".join(str(x) for x in sorted(self.levels)) + " level")
        return "; ".join(parts) or "unclear"

    # ---------------------------------------------------------------- matching
    def check(self, course, major_subjects: set[str]) -> tuple[bool, str]:
        code = course.code
        subj_full = f"{code.fac}/{code.subj}" if code.fac else code.subj
        in_major = code.subj in {m.split("/")[-1] for m in major_subjects}
        if self.levels and code.level not in self.levels:
            return False, f"is {code.level}-level; needs {'/'.join(str(x) for x in sorted(self.levels))}"
        if code.subj in {s.split("/")[-1] for s in self.exclude_subjects if "/" not in s} or subj_full in self.exclude_subjects:
            return False, f"{code.subj} courses are excluded"
        if self.outside_major and in_major:
            return False, f"{code.subj} is your major"
        if self.science_outside_major:
            if subj_full not in SCIENCE_SUBJECTS or in_major:
                return False, "not a science discipline outside your major"
            return True, f"{subj_full} is a science discipline outside your major"
        if self.major:
            return (True, f"{code.subj} is your major") if in_major else (False, f"{code.subj} is not your major")
        if self.subjects:
            ok = subj_full in self.subjects or code.subj in self.subjects
            return (True, f"{subj_full} matches") if ok else (False, f"needs {', '.join(sorted(self.subjects))}")
        if self.faculties:
            ok = (code.fac or "") in self.faculties
            return (True, f"offered by {code.fac}") if ok else (False, f"needs a {'/'.join(self.faculties)} course")
        if self.any or self.outside_major or self.exclude_subjects:
            return True, "counts as " + ("an elective" if self.any else "a course outside your major")
        return False, "rule unclear"


def _strip_subclauses(t: str) -> tuple[str, list[str]]:
    """'..., including at least 6 credits at the 4000 level' / 'of which three credits must be at the
    2000 level or above' are extra conditions on part of the credits, not on the whole rule."""
    notes = []
    for pat in (r",?\s*(?:normally )?including at least [^.;]*", r",?\s*of which [^.;]*", r",?\s*with no more than [^.;]*"):
        for m in re.finditer(pat, t):
            notes.append(m.group(0).strip(" ,"))
        t = re.sub(pat, " ", t)
    return t, notes


def _levels(text: str) -> Optional[set[int]]:
    t = text.lower()
    if re.search(r"\bany level\b", t):
        return None
    m = re.search(r"at the ([0-9#,/&\s]*(?:(?:or|and)[0-9#,/&\s]*)*)-?\s*levels?\b(\s*or (?:above|higher))?", t)
    if m and re.search(r"\d000", m.group(1)):
        nums = sorted({int(x) * 1000 for x in re.findall(r"(\d)000", m.group(1))})
        if m.group(2):
            return {x for x in LEVELS if x >= min(nums)}
        return set(nums)
    m2 = re.search(r"(\d)000[- ]level or (?:above|higher)", t)
    if m2:
        return {x for x in LEVELS if x >= int(m2.group(1)) * 1000}
    if re.search(r"upper[- ]level", t):
        return {3000, 4000}
    return None


def interpret(text: str, section: str = "", group: str = "", subject_names: Optional[dict[str, set[str]]] = None,
              known_subjects: Optional[set[str]] = None) -> Rule:
    """Turn a free-text rule into a Rule. `subject_names` maps lowercase names ('anthropology')
    to faculty-qualified codes ({'AP/ANTH'}); `known_subjects` are bare codes ('ANTH')."""
    subject_names = subject_names or {}
    known_subjects = known_subjects or set()
    raw = re.sub(r"\s+", " ", text or "").strip()
    t = raw.lower()
    r = Rule()

    for pat, label in _META:
        if re.search(pat, t):
            if label:
                r.note = label
                r.needs = label
                return r
            r.any = True
            r.note = "Use these credits first for your language, upper-level and other Faculty requirements."
            return r
    m = re.search(r"minimum grade of ([a-e][+]?)", t)
    if m:
        r.min_grade = m.group(1).upper()
    for pat, label in _NEEDS_LIST:
        if re.search(pat, raw, re.I):
            r.needs = label
            return r

    work, sub_notes = _strip_subclauses(t)
    if sub_notes:
        r.note = "Also: " + "; ".join(sub_notes)
    # parenthetical hints ("(Could be extra ADMS courses)") don't narrow a rule; keep "(PSYC)"-style codes
    work = re.sub(r"\((?![a-z]{2,4}\))[^)]*\)", " ", work)
    # --- exclusions: "non-MATH", "outside EECS, mathematics, statistics and information technology",
    #     "excluding major FA/VISA or FA/ARTH"
    for m in re.finditer(r"\bnon-([a-z]{2,4})\b", work):
        code = m.group(1).upper()
        if code in known_subjects:
            r.exclude_subjects.add(code)
    work = re.sub(r"\bnon-[a-z]{2,4}\b", " ", work)
    m = re.search(r"outside (?!the major|your major|of the major|of their major|the [a-z ]+ major)([a-z ,&/]+?)(?:\.|;|$)", work)
    if m and "major" not in m.group(1):
        names = re.split(r",|\band\b|\bor\b", m.group(1))
        found = set()
        for nm in names:
            nm = nm.strip()
            if nm.upper() in known_subjects:
                found.add(nm.upper())
            elif nm in subject_names:
                found |= subject_names[nm]
        if found:
            r.exclude_subjects |= found
            work = work.replace(m.group(0), " ")
    m = re.search(r"excluding (?:major )?((?:[a-z]{2}/[a-z]{2,4}(?:\s*(?:or|and|,)\s*)?)+)", work)
    if m:
        r.exclude_subjects |= {x.upper() for x in re.findall(r"[a-z]{2}/[a-z]{2,4}", m.group(1))}
        work = work.replace(m.group(0), " ")
    m = re.search(r"(?:may )?include (?:major )?((?:[a-z]{2}/[a-z]{2,4}(?:\s*(?:or|and|,)\s*)?)+)", work)
    if m:
        work = work.replace(m.group(0), " ")       # "may include X" doesn't narrow an elective

    # --- scope
    if re.search(r"science disciplines\*? outside (?:the|your) major", work):
        r.science_outside_major = True
        work = re.sub(r"(?:in )?science disciplines\*? outside (?:the|your) major", " ", work)
    if re.search(r"outside (?:of )?(?:the|your|their) (?:[a-z ]+ )?major", work):
        r.outside_major = True
        work = re.sub(r"outside (?:of )?(?:the|your|their) (?:[a-z ]+ )?major( discipline)?", " ", work)
    if re.search(r"\b(free[- ]?(?:choice )?electives?|free choice|electives?|any \d* ?credit course)\b", work):
        r.any = True
    if re.search(r"other departments in ampd", work):
        r.faculties.add("FA")
        r.outside_major = True
        work = work.replace("other departments in ampd", " ")
    for m in re.finditer(r"\b([a-z]{2})/([a-z]{2,4})\b", work):
        r.subjects.add(f"{m.group(1).upper()}/{m.group(2).upper()}")
    work = re.sub(r"\b[a-z]{2}/[a-z]{2,4}\b(?:\s*\d{4})?", " ", work)
    for m in re.finditer(r"\(([a-z]{2,4})\)|\b([a-z]{2,4})\b", work):
        code = (m.group(1) or m.group(2)).upper()
        if code in known_subjects and code not in {"ANY", "LEVEL", "MAY", "AND", "THE", "OR", "AT", "IN", "OF", "FOR",
                                                   "ALL", "ONE", "TWO", "SIX", "NINE", "FROM", "WITH", "MUST",
                                                   "MORE", "THAN", "EACH", "BOTH", "ALSO", "USED", "ARE"}:
            if (m.group(1) is not None) or raw.find(code) >= 0:     # bare codes must appear in caps in the source
                r.subjects.add(code)
    work = re.sub(r"\(([a-z]{2,4})\)", " ", work)
    for code in list(r.subjects):
        if "/" not in code:
            work = re.sub(rf"\b{code.lower()}\b", " ", work)
    for name in sorted(subject_names, key=len, reverse=True):
        if len(name) < 4:
            continue
        if re.search(rf"\b{re.escape(name)}\b", work):
            r.subjects |= subject_names[name]
            work = re.sub(rf"\b{re.escape(name)}\b", " ", work)
    if re.search(r"\bmajor\b", work) and not (r.outside_major or r.science_outside_major):
        r.major = True
    if re.search(r"\bminor\b", work):
        r.minor = True

    lv = _levels(_strip_subclauses(t)[0])
    if lv:
        r.levels = lv

    # --- context: a bare level rule ("at the 4000 level") inherits scope from its section
    no_scope = not (r.any or r.major or r.minor or r.outside_major or r.science_outside_major or r.subjects
                    or r.faculties or r.exclude_subjects)
    ctx = f"{section} {group}".lower()
    if no_scope:
        if "major" in ctx:
            r.major = True
        elif "minor" in ctx:
            r.minor = True
        elif "elective" in ctx:
            r.any = True
        elif "upper" in ctx and r.levels:
            r.any = True

    # --- anything left that we didn't understand?
    leftover = re.sub(r"\(.*?\)|[\d#*.,;:()–\-/'’]", " ", work)
    words = [w for w in leftover.split() if w not in _FILLER and len(w) > 2]
    if r.exclude_subjects and not (r.subjects or r.major or r.outside_major or r.science_outside_major or r.faculties):
        r.any = True                       # "non-MATH credits" = any course except MATH
    if words:
        r.unparsed = words
    return r
