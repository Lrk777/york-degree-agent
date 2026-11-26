"""Pipeline: York calendar API → cleaned SQLite tables (+ data-quality report)."""
from __future__ import annotations

import json
import re
from collections import Counter

from .codes import find_codes
from .descparse import parse_description
from .kuali import KualiClient, undergrad_catalogs
from .reqparse import parse_requirements
from .store import upsert


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def sync_catalogs(client: KualiClient, con) -> dict[str, str]:
    cats = undergrad_catalogs(client.catalogs(refresh=True))
    for year, c in cats.items():
        upsert(con, "catalogs", {"year": year, "catalog_id": c["_id"],
                                 "start_date": c.get("startDate"), "end_date": c.get("endDate")})
    con.commit()
    return {y: c["_id"] for y, c in cats.items()}


def sync_programs(client: KualiClient, con, year: str, catalog_id: str, codes: list[str]) -> list[dict]:
    progs = client.programs(catalog_id)
    wanted = progs if codes == ["all"] else [p for p in progs if p.get("code") in codes]
    stored = []
    for p in wanted:
        detail = client.program(catalog_id, p["pid"])
        for s in detail.get("specializations", []):
            html = s.get("creditCompletionRequirements") or ""
            tree = parse_requirements(html)
            row = {"year": year, "pid": s["pid"], "code": p["code"], "title": s.get("title", "").strip(),
                   "faculty": (p.get("faculty") or {}).get("name", ""),
                   "credential": (s.get("credentialEarned") or {}).get("name", ""),
                   "requirements_html": html, "rule_tree_json": tree.to_dict()}
            upsert(con, "programs", row)
            stored.append(row)
            # calendar text for retrieval (RAG): one chunk per requirement section + rules text
            for field in ("degreeRequirements", "graduationRequirements", "universityWideRequirement",
                          "continuingRequirements", "description"):
                txt = _strip(s.get(field) or detail.get(field) or "")
                if txt:
                    upsert(con, "calendar_text", {"year": year, "source": s.get("title", ""), "section": field,
                                                   "chunk_no": 0, "text": txt, "url": None})
            for i, sec in enumerate(tree.children):
                upsert(con, "calendar_text", {"year": year, "source": s.get("title", ""), "section": sec.label,
                                               "chunk_no": i, "text": _strip(json.dumps(sec.to_dict())), "url": None})
    con.commit()
    return stored


def referenced_subjects(programs: list[dict], extra: list[str]) -> set[str]:
    """Subjects whose courses we need in full: everything a program names plus bucket subjects."""
    subs = set(extra)
    for p in programs:
        for code in find_codes(json.dumps(p["rule_tree_json"])):
            if code.fac:
                subs.add(f"{code.fac}/{code.subj}")
    return subs


def sync_courses(client: KualiClient, con, year: str, catalog_id: str, subjects: set[str] | None = None,
                 limit: int | None = None) -> dict:
    listing = client.courses(catalog_id)
    ids = Counter(c["__catalogCourseId"] for c in listing)
    dupes = {k: v for k, v in ids.items() if v > 1}
    seen, n, failed = set(), 0, []
    for c in listing:
        cid = c["__catalogCourseId"]
        subj = (c.get("subjectCode") or {}).get("name", "")
        if subjects is not None and subj not in subjects:
            continue
        if cid in seen:                 # duplicate rows in the listing: load once
            continue
        seen.add(cid)
        if subj:
            upsert(con, "subjects", {"year": year, "subject": subj,
                                     "description": (c.get("subjectCode") or {}).get("description", "")})
        url = f"https://york.kuali.co/api/v1/catalog/course/{catalog_id}/{c['pid']}"
        cached = client.cached(url)
        # Kuali gives each course version its own id: re-fetch only when the version changed
        try:
            d = client.get(url, refresh=bool(cached and c.get("id") and cached.get("id") != c.get("id")))
        except Exception as e:          # one missing course must not kill a multi-hour run
            failed.append(f"{cid}: {e}")
            continue
        code = find_codes(d.get("__catalogCourseId", cid))
        if not code:
            continue
        code = code[0]
        credits = float(((d.get("credits") or {}).get("value")) or code.credits or 0)
        desc = parse_description(_strip(d.get("description", "")))
        xl = [x.full for x in desc.crosslisted]
        for x in d.get("crosslistedCourses") or []:            # structured cross-list field
            xc = find_codes(x.get("__catalogCourseId", ""))
            if xc and xc[0].full not in xl:
                xl.append(xc[0].full)
        upsert(con, "courses", {
            "year": year, "course_full": f"{code.fac}/{code.key}" if code.fac else code.key,
            "course_key": code.key, "faculty": code.fac, "subject": code.subj, "number": code.num,
            "level": code.level, "credits": credits, "title": d.get("title", ""),
            "description": desc.text, "prereq_raw": desc.prereq.raw,
            "prereq_json": {"clauses": [[x.full for x in cl] for cl in desc.prereq.clauses],
                            "conditions": desc.prereq.conditions, "min_grade": desc.prereq.min_grade},
            "exclusions_json": [x.full for x in desc.exclusions + desc.not_open_to],
            "crosslisted_json": xl,
            "gen_ed_flag": int(desc.gen_ed_approved), "kuali_pid": c["pid"], "kuali_id": c.get("id")})
        n += 1
        if n % 200 == 0:
            con.commit()
        if limit and n >= limit:
            break
    con.commit()
    return {"loaded": n, "listing_rows": len(listing), "duplicate_ids": len(dupes), "failed": len(failed),
            "failed_examples": failed[:5]}


def dq_report(con, year: str) -> dict:
    """Data-quality checks. 'hard' failures should stop a release; 'soft' ones are reported."""
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    known = {r[0] for r in q("SELECT course_full FROM courses WHERE year=?", year)}
    known_keys = {r[0] for r in q("SELECT course_key FROM courses WHERE year=?", year)}
    hard, soft = {}, {}
    hard["courses_loaded"] = len(known) > 0
    hard["no_zero_credit_courses"] = q("SELECT COUNT(*) FROM courses WHERE year=? AND (credits IS NULL OR credits<=0)", year)[0][0] == 0
    hard["levels_valid"] = q("SELECT COUNT(*) FROM courses WHERE year=? AND level NOT IN (1000,2000,3000,4000,5000,6000)", year)[0][0] == 0
    # every course named by a program rule should exist in that year's catalog
    missing_rule_refs = set()
    for (tree,) in q("SELECT rule_tree_json FROM programs WHERE year=?", year):
        for c in find_codes(tree):
            if c.full not in known and c.key not in known_keys:
                missing_rule_refs.add(c.full)
    soft["program_rule_courses_not_in_catalog"] = sorted(missing_rule_refs)
    dangling = set()
    for (pj,) in q("SELECT prereq_json FROM courses WHERE year=?", year):
        for clause in json.loads(pj)["clauses"]:
            for full in clause:
                key = full.split("/")[-1]
                if full not in known and key not in known_keys:
                    dangling.add(full)
    soft["prereq_refs_not_in_loaded_subjects"] = len(dangling)
    soft["courses_with_unparsed_prereq_conditions"] = q(
        "SELECT COUNT(*) FROM courses WHERE year=? AND prereq_json LIKE '%\"conditions\": [\"%'", year)[0][0]
    return {"year": year, "hard": hard, "hard_ok": all(hard.values()), "soft": soft}
