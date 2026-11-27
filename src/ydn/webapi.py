"""Functions the web page calls through Pyodide. JSON in, JSON out. No network, no disk:
the transcript only ever exists in the student's own browser tab."""
from __future__ import annotations

import json

from .agent import SYSTEM, TOOLS, DegreeAgent
from .audit import Auditor, Result, infer_major_subjects
from .catalog import BucketLibrary, Catalog
from .codes import parse_code
from .report import render
from .retrieval import Chunk, Retriever, chunks_from_rules
from .suggest import open_requirements
from .transcript import parse_transcript_text, redact
from .tree import Node

S: dict = {}

FRIENDLY = {"all_of": "Required courses", "credits_from": "Choose from a list", "bucket": "Credits",
            "upper_level": "Upper-level credits (3000 level or above)", "total": "Total credits for the degree"}


class LazyRetriever:
    """Builds the search index on first use. Only the optional AI chat needs it, and building it
    is the slowest part of loading a calendar year, so the checklist no longer waits for it."""

    def __init__(self, chunks):
        self._chunks, self._r = chunks, None

    def search(self, query, year, k=4):
        if self._r is None:
            self._r = Retriever(self._chunks)
        return self._r.search(query, year, k)


def load_year(year: str, courses_json: str, programs_json: str, rules_json: str, chunks_json: str) -> str:
    rules = json.loads(rules_json)
    S.update(year=year, catalog=Catalog.from_export(year, json.loads(courses_json)),
             programs=json.loads(programs_json), buckets=BucketLibrary.from_dict(rules))
    chunks = [Chunk(c["text"], year, c["source"], c["section"], c.get("url", ""))
              for c in json.loads(chunks_json or "[]")]
    S["retriever"] = LazyRetriever(chunks + chunks_from_rules(rules))
    return json.dumps({"courses": len(S["catalog"].courses), "programs": len(S["programs"])})


def preview(transcript: str) -> str:
    att = parse_transcript_text(transcript)
    cat = S.get("catalog")
    out = []
    for a in att:
        c = cat.get(a.code) if cat else None
        out.append({"course": a.code.full, "title": c.title if c else "", "credits": a.credits,
                    "grade": a.grade or "", "status": a.status, "pass_fail": a.pass_fail})
    return json.dumps({"redacted": redact(transcript)[:4000], "courses": out})


# ---------------------------------------------------------------- checklist view
def _course_state(code, attempts):
    """done / in_progress / missing for one required course, with the grade if taken."""
    state, grade = "missing", ""
    for a in attempts:
        if a.code.same_course(code):
            if a.status == "passed":
                return "done", a.grade
            if a.status == "in_progress":
                state, grade = "in_progress", a.grade or "IP"
    return state, grade


def _view(r: Result, attempts, cat, depth: int, section: str, header: str, used_by: dict):
    """One node of the checklist the page draws. Sections are the calendar's top-level headings;
    every leaf gets a readable title (its own label, else the nearest group heading, else a plain name)."""
    n = r.node
    if n.kind == "note":
        return None
    label = n.label if n.label and n.label != n.kind else ""
    if depth == 1:
        section = label or "Requirements"
    v = {"kind": n.kind, "status": r.status, "need": round(r.need, 2), "rule_id": n.rule_id, "section": section}
    if n.kind == "group":
        v["title"] = label
        v["mode"] = n.mode
        kids = []
        for c in r.children:
            # the section heading is shown once above its cards, so it isn't repeated as a card title
            cv = _view(c, attempts, cat, depth + 1, section, (label if depth > 1 else "") or header, used_by)
            if cv is not None:
                kids.append(cv)
        v["children"] = kids
        if n.mode != "all":
            v["options"] = [c.node.label for c in r.children if c.node.label]
            v["chosen"] = [c.node.label for c in r.children if c.status != "not_chosen" and c.node.label]
        return v
    title = label or header or FRIENDLY.get(n.kind, "Requirement")
    v.update(title=title, message=r.message, credits=n.credits,
             used=[{"course": a.code.full, "credits": a.credits, "grade": a.grade or "IP", "status": a.status}
                   for a in r.used])
    if n.kind in ("all_of", "credits_from") and n.courses:
        items = []
        for c in n.courses:
            st, grade = _course_state(c, attempts)
            course = cat.get(c)
            items.append({"course": c.full, "title": course.title if course else "",
                          "credits": c.credits or (course.credits if course else 3.0), "state": st, "grade": grade})
        v["items"] = items
    if n.kind == "bucket":
        v["rule_text"] = n.text[:300]
    if r.status != "not_chosen":
        for a in r.used:
            used_by.setdefault(a.code.key, []).append(f"{section}: {title}" if title != section else section)
    return v


def _checklist(result: Result, attempts, cat):
    used_by: dict = {}
    sections = []
    for sec in result.children:
        sv = _view(sec, attempts, cat, 1, "", "", used_by)
        if sv is not None:
            sections.append(sv)
    courses = []
    for a in attempts:
        c = cat.get(a.code)
        courses.append({"course": a.code.full, "title": c.title if c else "", "credits": a.credits,
                        "grade": a.grade or "", "status": a.status, "pass_fail": a.pass_fail,
                        "counted_in": used_by.get(a.code.key, []), "level": a.code.level})
    return sections, courses


def audit(pid: str, transcript: str, major_override: str = "", stream: str = "") -> str:
    p = S["programs"][pid]
    tree = Node.from_dict(p["tree"])
    cat = S["catalog"]
    major = {m.strip().upper() for m in major_override.split(",") if m.strip()} or \
        infer_major_subjects(tree, p["code"], cat.subjects)
    fac = (p["code"] or "").split("-")[0]
    choices = {}
    if stream:
        for g in tree.walk():
            if g.kind == "group" and g.mode != "all" and any(c.label == stream for c in g.children):
                choices[g.label] = stream
    aud = Auditor(cat, S["buckets"], fac, major, choices)
    att = parse_transcript_text(transcript)
    agent = DegreeAgent(aud, tree, att, S["retriever"], p["title"], S["year"])
    S.update(agent=agent, pid=pid)
    streams = [[c.label for c in g.children] for g in tree.walk()
               if g.kind == "group" and g.mode != "all" and all(c.label for c in g.children) and len(g.children) > 2]
    sections, courses = _checklist(agent.result, att, cat)
    return json.dumps({"program": p["title"], "year": S["year"], "major_subjects": sorted(major),
                       "streams": streams, "result": agent.result.to_dict(), "report": render(agent.result),
                       "open": agent.tool("get_progress", {})["open_requirements"],
                       "sections": sections, "courses": courses, "courses_read": len(att)})


def courses_for(rule_id: str, query: str = "", limit: int = 30) -> str:
    out = S["agent"].tool("find_courses", {"requirement_id": rule_id, "limit": 400})
    if "error" in out:
        return json.dumps(out)
    courses = out["courses"]
    if query.strip():
        q = query.lower().split()
        cat = S["catalog"]

        def score(c):
            course = cat.get(parse_code(c["course"]))
            text = f"{c['title']} {course.desc.text if course else ''}".lower()
            return sum(text.count(w) for w in q)
        courses = [c for c in courses if score(c) > 0]
        courses.sort(key=lambda c: -score(c))
    out["courses"] = courses[:limit]
    out["total_matching"] = len(courses)
    return json.dumps(out)


def does_it_count(code: str) -> str:
    return json.dumps(S["agent"].tool("does_it_count", {"course": code}), default=str)


def tool(name: str, args_json: str) -> str:
    return json.dumps(S["agent"].tool(name, json.loads(args_json or "{}")), default=str)


def agent_spec() -> str:
    a = S["agent"]
    return json.dumps({"system": SYSTEM.format(year=a.year, program=a.program), "tools": TOOLS})


def advisor_summary() -> str:
    a = S["agent"]
    opens = open_requirements(a.result)
    lines = [f"Program: {a.program} (calendar {a.year})", "",
             "What I think is still open (unofficial check, please confirm):"]
    for r in opens:
        lines.append(f"- {r.node.label or r.node.section or r.node.kind}: {r.message[:160]}"
                     + (f" (need {r.need:g} credits)" if r.need else ""))
    manual = [r for r in opens if r.status == "manual"]
    if manual:
        lines += ["", "Rules the tool could not check and I need your help with:"]
        lines += [f"- {r.node.text[:160]}" for r in manual]
    lines += ["", "Generated by an unofficial planning tool from York's public calendar data."]
    return "\n".join(lines)
