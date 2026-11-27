"""SQLite → static JSON for the web app, plus a coverage report.

site/data/index.json                 years, programs per year, coverage
site/data/<slug>/courses.json        compact pre-parsed courses + subject names + offerings
site/data/<slug>/programs.json       rule trees per program (specialization)
site/data/<slug>/chunks.json         calendar text for search
site/data/rules.json                 rules/buckets.yaml as JSON
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from .audit import Auditor, infer_major_subjects
from .catalog import BucketLibrary, Catalog
from .tree import Node


def slug(year: str) -> str:
    return year.lower().replace("/", "-")


def export_year(con, year: str, out: Path, ay_offerings: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT course_full, title, credits, description, prereq_raw, prereq_json, exclusions_json, "
                       "crosslisted_json, gen_ed_flag, kuali_pid FROM courses WHERE year=? ORDER BY course_full",
                       (year,)).fetchall()
    offer = {}
    if ay_offerings:
        offer = {f: t.split(",") if t else [] for f, t in
                 con.execute("SELECT course_full, terms FROM offerings WHERE ay=?", (ay_offerings,)).fetchall()}
        if offer and not any(offer.values()):
            offer = {}          # nothing parsed at all = a scraper problem, not "nothing is offered": show as unknown
    # Courses that exist only in a newer calendar (e.g. AP/PHIL 3500 is required by older Data Science calendars
    # but listed only from FW26/27) are added from the newest year that has them, tagged with that year.
    have = {r[0] for r in rows}
    newer = {}
    for r in con.execute("SELECT year, course_full, title, credits, description, prereq_raw, prereq_json, "
                         "exclusions_json, crosslisted_json, gen_ed_flag, kuali_pid FROM courses WHERE year<>? "
                         "ORDER BY year", (year,)).fetchall():
        if r[1] not in have:
            newer[r[1]] = r                     # later years overwrite earlier ones
    rows = [(r, None) for r in rows] + [(r[1:], r[0]) for r in sorted(newer.values(), key=lambda r: r[1])]
    courses = []
    for (full, title, cr, desc, praw, pj, ej, xj, ge, pid), from_year in rows:
        pq = json.loads(pj or "{}")
        rec = {"c": f"{full} {cr or 0:.2f}", "t": title, "cr": cr, "d": (desc or "")[:700], "pid": pid}
        if pq.get("clauses"):
            rec["pq"] = pq["clauses"]
        if pq.get("conditions"):
            rec["pc"] = pq["conditions"]
        if pq.get("min_grade"):
            rec["pg"] = pq["min_grade"]
        if praw:
            rec["pr"] = praw[:300]
        for k, v in (("ex", json.loads(ej or "[]")), ("xl", json.loads(xj or "[]"))):
            if v:
                rec[k] = v
        if ge:
            rec["ge"] = 1
        if from_year:
            rec["y"] = from_year
            rec["t"] = f"{title} (listed in the {from_year} calendar)"
        courses.append(rec)
    subjects = dict(con.execute("SELECT subject, description FROM subjects WHERE year=?", (year,)).fetchall())
    (out / "courses.json").write_text(json.dumps({"year": year, "courses": courses, "subjects": subjects,
                                                  "offerings": offer, "offerings_year": ay_offerings},
                                                 separators=(",", ":")))
    progs = {}
    for pid, code, title, fac, cred, tree in con.execute(
            "SELECT pid, code, title, faculty, credential, rule_tree_json FROM programs WHERE year=? ORDER BY title",
            (year,)).fetchall():
        progs[pid] = {"code": code, "title": title, "faculty": fac, "credential": cred, "tree": json.loads(tree)}
    (out / "programs.json").write_text(json.dumps(progs, separators=(",", ":")))
    chunks = [{"text": t, "source": s, "section": sec, "url": u or ""} for s, sec, t, u in con.execute(
        "SELECT source, section, text, url FROM calendar_text WHERE year=?", (year,)).fetchall()]
    (out / "chunks.json").write_text(json.dumps(chunks, separators=(",", ":")))
    return {"courses": len(courses), "from_other_years": len(newer), "programs": len(progs), "chunks": len(chunks),
            "offerings": sum(1 for v in offer.values() if v)}


def coverage(con, year: str, buckets: BucketLibrary) -> dict:
    """How much of every program's rules the engine can check automatically."""
    recs = [{"id": f, "title": "", "description": ""} for (f,) in
            con.execute("SELECT course_full FROM courses WHERE year=?", (year,)).fetchall()]
    cat = Catalog.from_records(year, recs)
    for subj, desc in con.execute("SELECT subject, description FROM subjects WHERE year=?", (year,)).fetchall():
        cat.add_subject_desc(subj, desc)
    kinds, reasons = Counter(), Counter()
    by_fac = defaultdict(lambda: Counter())
    for code, fac, tree in con.execute("SELECT code, faculty, rule_tree_json FROM programs WHERE year=?", (year,)).fetchall():
        root = Node.from_dict(json.loads(tree))
        fac_code = (code or "").split("-")[0]
        aud = Auditor(cat, buckets, fac_code, infer_major_subjects(root, code, cat.subjects))
        for n in root.walk():
            if n.kind in ("group", "note"):
                continue
            if n.kind == "bucket":
                spec, why = aud.resolve_bucket(n)
                if spec is None:
                    k = "advisor"
                    reasons[why.split(":")[0].split(",")[0]] += 1
                else:
                    k = "bucket_" + spec.source.replace(" ", "_")
            else:
                k = "structured"
            kinds[k] += 1
            by_fac[fac_code][k] += 1
    total = sum(kinds.values()) or 1
    checkable = total - kinds["advisor"]
    return {"year": year, "rules": total, "checkable": checkable, "checkable_pct": round(100 * checkable / total, 1),
            "by_kind": dict(kinds), "advisor_reasons": dict(reasons.most_common(12)),
            "by_faculty": {f: {"rules": sum(c.values()), "checkable_pct":
                               round(100 * (sum(c.values()) - c["advisor"]) / max(1, sum(c.values())), 1)}
                           for f, c in sorted(by_fac.items())}}


def export_all(con, out: Path, rules_path: Path, ay_offerings: str | None = None) -> dict:
    import yaml
    out.mkdir(parents=True, exist_ok=True)
    rules = yaml.safe_load(rules_path.read_text())
    (out / "rules.json").write_text(json.dumps(rules, default=str))
    lib = BucketLibrary.from_dict(rules)
    years = [y for (y,) in con.execute("SELECT DISTINCT year FROM programs ORDER BY year").fetchall()]
    index = {"generated": time.strftime("%Y-%m-%d"), "years": [], "coverage": {}}
    for y in years:
        # 2026-27 offerings apply to every entry year: students on older calendars take this year's courses too
        stats = export_year(con, y, out / slug(y), ay_offerings)
        progs = json.loads((out / slug(y) / "programs.json").read_text())
        index["years"].append({"year": y, "slug": slug(y), **stats,
                               "programs": [{"pid": k, "code": v["code"], "title": v["title"], "faculty": v["faculty"]}
                                            for k, v in progs.items()]})
        index["coverage"][y] = coverage(con, y, lib)
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return index
