"""Deterministic degree audit.

The language model never decides whether a requirement is met: this module does,
from the rule tree and the transcript, and every result carries the courses it
used and the reason. Allocation follows how advisors work through a checklist:
named courses first, then credits-from lists, then free-text buckets (electives
last), then whole-degree checks (upper-level credits, total credits).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .catalog import BucketLibrary, Catalog, Course
from .codes import CourseCode
from .descparse import parse_description
from .freetext import Rule, interpret
from .tree import Node
from .transcript import Attempt

PHASE = {"all_of": 0, "credits_from": 1, "bucket": 2, "upper_level": 3, "total": 3, "note": 4}


@dataclass
class Result:
    node: Node
    status: str = "missing"           # met | in_progress | missing | not_chosen | info | manual
    used: list[Attempt] = field(default_factory=list)
    need: float = 0.0                 # credits still needed (after counting in-progress)
    message: str = ""
    children: list["Result"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.node.label or self.node.kind, "kind": self.node.kind, "rule_id": self.node.rule_id,
            "status": self.status, "need": round(self.need, 2), "message": self.message,
            "used": [f"{a.code.full} ({a.grade or 'in progress'})" for a in self.used],
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class BucketRule:
    """One free-text requirement, resolved either from rules/buckets.yaml or by the interpreter."""
    name: str
    check_fn: object
    min_areas: Optional[int] = None
    note: str = ""
    source: str = ""            # 'rules file' or 'interpreted'

    def check(self, course, major, pass_fail=False):
        return self.check_fn(course, major, pass_fail)


class Auditor:
    def __init__(self, catalog: Catalog, buckets: BucketLibrary, program_faculty: str = "",
                 major_subjects: set[str] = frozenset(), choices: Optional[dict[str, str]] = None):
        self.catalog, self.buckets = catalog, buckets
        self.fac, self.major = program_faculty, set(major_subjects)
        self.choices = choices or {}          # group label -> chosen child label (e.g. stream)

    # ------------------------------------------------------------ buckets
    def resolve_bucket(self, n: Node):
        """Returns (BucketRule, None) or (None, reason-for-advisor)."""
        spec = self.buckets.for_text(n.text, self.fac)
        if spec is not None:
            return BucketRule(spec.name, lambda c, m, pf: spec.check(c, m, pf), spec.spec.get("min_areas"),
                              " ".join(spec.spec.get("notes", [])), "rules file"), None
        r: Rule = interpret(n.text, n.section, n.label, self.catalog.subject_names, self.catalog.subjects)
        if not r.usable:
            if r.needs:
                why = f"needs {r.needs}, which isn't loaded yet"
            elif r.minor:
                why = "depends on your minor subject, which isn't set"
            else:
                why = "wording not understood: " + ", ".join(r.unparsed[:6])
            return None, why

        def fn(course, major, pass_fail, r=r):
            return r.check(course, major)
        return BucketRule(r.describe(), fn, None, r.note or "", "interpreted"), None

    # ------------------------------------------------------------ helpers
    def course_for(self, a: Attempt) -> Course:
        c = self.catalog.get(a.code)
        if c:
            return c
        return Course(a.code, "", parse_description(""))   # retired/unknown course: subject-level checks only

    @staticmethod
    def _matches(a: Attempt, codes: list[CourseCode]) -> bool:
        return any(a.code.same_course(c) for c in codes)

    # ------------------------------------------------------------ choice groups
    def _choose(self, node: Node, pool: list[Attempt]) -> list[Node]:
        if node.mode == "all":
            return node.children
        n = int(node.mode)
        wanted = self.choices.get(node.label) or self.choices.get(node.children[0].label if node.children else "")
        if wanted:
            picked = [c for c in node.children if c.label == wanted]
            if picked:
                return picked[:n]
        scored = []
        for c in node.children:
            probe = Auditor(self.catalog, self.buckets, self.fac, self.major, self.choices)
            r = probe.run(c, [Attempt(a.code, a.credits, a.grade, a.status, a.term, a.pass_fail) for a in pool])
            scored.append((_progress(r), c))
        scored.sort(key=lambda t: -t[0])
        return [c for _, c in scored[:n]]

    # ------------------------------------------------------------ main
    def run(self, root: Node, attempts: list[Attempt]) -> Result:
        pool = [a for a in attempts if a.counts]
        used_ids: set[int] = set()
        results: dict[int, Result] = {}

        def build(node: Node, active: bool) -> Result:
            r = Result(node)
            results[id(node)] = r
            if node.kind == "group":
                chosen = self._choose(node, pool) if active else []
                for c in node.children:
                    cr = build(c, active and any(c is x for x in chosen))
                    r.children.append(cr)
                if not active:
                    r.status = "not_chosen"
            elif not active:
                r.status = "not_chosen"
            return r

        top = build(root, True)
        leaves = [n for n in root.walk() if n.kind != "group" and results[id(n)].status != "not_chosen"]
        ordered = sorted(leaves, key=lambda n: (PHASE.get(n.kind, 5),
                                                 1 if (n.kind == "bucket" and _is_elective(n)) else 0))
        for n in ordered:
            self._eval_leaf(n, results[id(n)], pool, used_ids)
        _rollup(top)
        from .suggest import link_parents
        return link_parents(top)

    def _take(self, pool, used_ids, pred, need):
        got, credits = [], 0.0
        for status in ("passed", "in_progress"):
            for a in pool:
                if credits >= need > 0:
                    break
                if id(a) in used_ids or a.status != status or not pred(a):
                    continue
                got.append(a)
                credits += a.credits
        for a in got:
            used_ids.add(id(a))
        return got

    def _eval_leaf(self, n: Node, r: Result, pool, used_ids):
        if n.kind == "note":
            r.status, r.message = "info", n.text
            return
        if n.kind == "all_of":
            got = self._take(pool, used_ids, lambda a: self._matches(a, n.courses), 1e9)
            missing = [c.full for c in n.courses if not any(a.code.same_course(c) for a in got)]
            r.used = got
            r.need = sum((c.credits or 3.0) for c in n.courses if c.full in missing)
            r.status = _status(got, missing_any=bool(missing))
            r.message = "Still to take: " + ", ".join(missing) if missing else "All listed courses done."
            return
        if n.kind == "credits_from":
            got = self._take(pool, used_ids, lambda a: self._matches(a, n.courses), n.credits)
            self._credit_result(r, got, n.credits)
            return
        if n.kind == "bucket":
            spec, why = self.resolve_bucket(n)
            if spec is None:
                r.status = "manual"
                r.need = n.credits
                r.message = f"Confirm with an advisor ({why}): “{n.text[:160]}”"
                return
            pred = lambda a: spec.check(self.course_for(a), self.major, a.pass_fail)[0]
            got = self._take(pool, used_ids, pred, n.credits)
            self._credit_result(r, got, n.credits)
            areas = {self.course_for(a).subject_full for a in got if a.status == "passed"}
            if spec.min_areas and r.status == "met" and len(areas) < spec.min_areas:
                r.status = "missing"
                r.message = (f"Credits are there, but they come from {len(areas)} area(s); "
                             f"{spec.name} needs at least {spec.min_areas} different areas.")
            else:
                r.message = f"{r.message} — counts: {spec.name}" + (f". {spec.note}" if spec.note and spec.source == "interpreted" else "")
            return
        if n.kind in ("upper_level", "total"):
            lvl = 3000 if n.kind == "upper_level" else 0
            done = sum(a.credits for a in pool if a.status == "passed" and a.code.level >= lvl)
            ip = sum(a.credits for a in pool if a.status == "in_progress" and a.code.level >= lvl)
            r.need = max(0.0, n.credits - done - ip)
            r.status = "met" if done >= n.credits else ("in_progress" if done + ip >= n.credits else "missing")
            r.message = f"{done:g} of {n.credits:g} credits done" + (f", {ip:g} in progress" if ip else "")

    @staticmethod
    def _credit_result(r: Result, got: list[Attempt], need: float):
        done = sum(a.credits for a in got if a.status == "passed")
        ip = sum(a.credits for a in got if a.status == "in_progress")
        r.used, r.need = got, max(0.0, need - done - ip)
        r.status = "met" if done >= need else ("in_progress" if done + ip >= need else "missing")
        r.message = f"{done:g} of {need:g} credits" + (f" (+{ip:g} in progress)" if ip else "")


def _is_elective(n: Node) -> bool:
    return "elective" in (n.text + n.label).lower()


def _status(got: list[Attempt], missing_any: bool) -> str:
    if missing_any:
        return "missing"
    return "in_progress" if any(a.status == "in_progress" for a in got) else "met"


def _rollup(r: Result):
    if r.node.kind != "group" or r.status == "not_chosen":
        return
    for c in r.children:
        _rollup(c)
    active = [c for c in r.children if c.status not in ("not_chosen", "info")]
    r.need = sum(c.need for c in active)
    if not active:
        r.status = "info"
    elif all(c.status == "met" for c in active):
        r.status = "met"
    elif all(c.status in ("met", "in_progress") for c in active):
        r.status = "in_progress"
    elif any(c.status == "manual" for c in active) and all(c.status in ("met", "in_progress", "manual") for c in active):
        r.status = "manual"
    else:
        r.status = "missing"


def _progress(r: Result) -> float:
    """Share of an option already done (partial credit), used to guess which stream/option a student is on."""
    leaves = [x for x in _iter(r) if x.node.kind not in ("group", "note")]
    if not leaves:
        return 0.0
    total = 0.0
    for x in leaves:
        n = x.node
        if n.kind == "all_of" and n.courses:
            total += len({a.code.key for a in x.used}) / len(n.courses)
        elif n.credits:
            total += min(1.0, sum(a.credits for a in x.used) / n.credits)
        else:
            total += 1.0 if x.status == "met" else 0.0
    return total / len(leaves)


def _iter(r: Result):
    yield r
    for c in r.children:
        yield from _iter(c)


def infer_major_subjects(tree: Node, program_code: str = "", known_subjects: set[str] = frozenset()) -> set[str]:
    """Best guess at the student's major subject(s), shown to the student so they can correct it.

    1. Program code suffix if it is a real subject ('AP-ANTH' -> ANTH).
    2. Otherwise the most common subject among named courses in the major section(s)
       (Data Science BSc: 15 MATH, 3 EECS, 3 ITEC ... -> MATH).
    """
    suffix = program_code.split("-")[-1].upper() if program_code else ""
    if suffix and suffix in known_subjects:
        return {suffix}
    counts: dict[str, int] = {}
    sections = [s for s in tree.children if "major" in s.label.lower()] or tree.children
    for s in sections:
        for n in s.walk():
            for c in n.courses:
                counts[c.subj] = counts.get(c.subj, 0) + 1
    if not counts:
        return set()
    top = max(counts.values())
    return {s for s, v in counts.items() if v >= 0.6 * top}
