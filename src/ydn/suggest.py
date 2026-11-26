"""'What can I take?' — course finder for every requirement that is not met yet.

This is the part students struggle with most: electives and breadth rules span
many subject codes, faculties and websites. For an open requirement this returns
real catalog courses that count toward it, each with the reason it counts, whether
the prerequisites are met, and whether it also helps another open requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .audit import Auditor, Result
from .catalog import Course
from .descparse import Prereq
from .transcript import Attempt

_GRADE_ORDER = ["E", "D", "D+", "C", "C+", "B", "B+", "A", "A+"]


@dataclass
class Suggestion:
    course: Course
    counts_because: str
    prereq_status: str                 # met | pending (in progress) | missing | check (non-course conditions)
    prereq_note: str = ""
    also_helps: list[str] = field(default_factory=list)
    offered: object = None              # list of terms, [] = not offered this year, None = unknown

    def to_dict(self):
        c = self.course
        return {"course": c.code.full, "title": c.title, "credits": c.credits, "level": c.code.level,
                "counts_because": self.counts_because, "prerequisites": self.prereq_status,
                "prereq_note": self.prereq_note, "also_helps": self.also_helps,
                "offered_2026_27": self.offered}


def prereq_status(p: Prereq, attempts: list[Attempt]) -> tuple[str, str]:
    if p.empty:
        return "met", "no prerequisites listed"
    passed = [a for a in attempts if a.status == "passed"]
    ip = [a for a in attempts if a.status == "in_progress"]
    missing, pending = [], []
    for clause in p.clauses:
        def ok(a):
            if not any(a.code.same_course(c) for c in clause):
                return False
            if p.min_grade and a.grade in _GRADE_ORDER:
                return _GRADE_ORDER.index(a.grade) >= _GRADE_ORDER.index(p.min_grade)
            return True
        if any(ok(a) for a in passed):
            continue
        if any(ok(a) for a in ip):
            pending.append(" or ".join(c.full for c in clause))
        else:
            missing.append(" or ".join(c.full for c in clause))
    if missing:
        return "missing", "needs " + "; ".join(missing)
    if pending:
        return "pending", "in progress: " + "; ".join(pending)
    if p.conditions:
        return "check", "also requires: " + "; ".join(p.conditions)
    return "met", "prerequisites met"


def _blocked(course: Course, attempts: list[Attempt]) -> bool:
    """Already taken, or excluded by a course already taken (course credit exclusion)."""
    for a in attempts:
        if a.status not in ("passed", "in_progress"):
            continue
        if a.code.same_course(course.code):
            return True
        if any(a.code.same_course(x) for x in course.desc.exclusions + course.desc.not_open_to):
            return True
    return False


def suggest_for(result: Result, auditor: Auditor, attempts: list[Attempt], open_overlays: list[Result],
                limit: int = 15) -> list[Suggestion]:
    n = result.node
    cat = auditor.catalog
    out: list[Suggestion] = []
    if n.kind in ("all_of", "credits_from"):
        cands = [(cat.get(c), "listed for this requirement") for c in n.courses]
        cands = [(c, why) for c, why in cands if c is not None]
    elif n.kind == "bucket":
        spec, _ = auditor.resolve_bucket(n)
        if spec is None:
            return []
        # Courses your program already requires will be used there; suggesting them here
        # would promise the same credits twice.
        required = {c.key for node in _root(result).node.walk() if node.kind in ("all_of", "credits_from")
                    for c in node.courses}
        cands = []
        for c in cat.courses:
            if c.code.key in required:
                continue
            ok, why = spec.check(c, auditor.major)
            if ok:
                cands.append((c, why))
    else:
        return []
    for c, why in cands:
        if _blocked(c, attempts):
            continue
        st, note = prereq_status(c.desc.prereq, attempts)
        helps = []
        for ov in open_overlays:
            if ov.node.kind == "upper_level" and c.code.level >= 3000:
                helps.append(f"{ov.node.label or 'Upper-level requirement'} (3000+ level)")
        offered = cat.offerings.get(c.code.full) if cat.offerings else None
        out.append(Suggestion(c, why, st, note, helps, offered))
    rank = {"met": 0, "check": 1, "pending": 2, "missing": 3}
    off = lambda s: 0 if s.offered else (2 if s.offered == [] else 1)
    out.sort(key=lambda s: (rank[s.prereq_status], off(s), -len(s.also_helps), s.course.code.level, s.course.code.full))
    return out[:limit]


def _root(r: Result) -> Result:
    while getattr(r, "parent", None) is not None:
        r = r.parent
    return r


def link_parents(root: Result) -> Result:
    def walk(r, parent):
        r.parent = parent
        for c in r.children:
            walk(c, r)
    walk(root, None)
    return root


def where_does_it_count(code, root: Result, auditor: Auditor, attempts: list[Attempt]) -> dict:
    """'If I take X, what does it count toward?' — every requirement, with the reason."""
    course = auditor.catalog.get(code)
    if course is None:
        return {"course": code.full, "found": False,
                "answer": f"{code.full} is not in the {auditor.catalog.year} calendar data."}
    hits = []

    def walk(r: Result):
        if r.status == "not_chosen":
            return
        n = r.node
        if n.kind in ("all_of", "credits_from") and any(course.code.same_course(c) for c in n.courses):
            hits.append({"requirement": n.label or n.rule_id, "rule_id": n.rule_id, "status": r.status,
                         "why": "listed by name in this requirement"})
        elif n.kind == "bucket":
            spec, why = auditor.resolve_bucket(n)
            if spec:
                ok, why = spec.check(course, auditor.major)
                hits.append({"requirement": spec.name, "rule_id": n.rule_id, "status": r.status,
                             "counts": ok, "why": why})
            else:
                hits.append({"requirement": n.label or n.rule_id, "rule_id": n.rule_id, "status": r.status,
                             "counts": None, "why": f"ask an advisor ({why})"})
        elif n.kind == "upper_level" and course.code.level >= 3000:
            hits.append({"requirement": "Upper-level credits", "rule_id": n.rule_id, "status": r.status,
                         "why": "3000+ level"})
        for c in r.children:
            walk(c)
    walk(root)
    st, note = prereq_status(course.desc.prereq, attempts)
    blocked = _blocked(course, attempts)
    named = [h for h in hits if h["why"] == "listed by name in this requirement"]
    return {"course": course.code.full, "title": course.title, "credits": course.credits, "found": True,
            "already_taken_or_excluded": blocked, "prerequisites": st, "prereq_note": note,
            "counts_toward": [h for h in hits if h.get("counts", True)],
            "does_not_count_toward": [h for h in hits if h.get("counts") is False],
            "double_count_warning": ("Your program requires this course by name, so it will be used there "
                                     "and normally cannot also fill a general education or elective bucket.")
            if named and len(hits) > len(named) else None}


def open_requirements(root: Result) -> list[Result]:
    out = []

    def walk(r: Result):
        if r.status == "not_chosen":
            return
        if r.node.kind not in ("group", "note") and r.status in ("missing", "manual"):
            out.append(r)
        for c in r.children:
            walk(c)
    walk(root)
    return out
