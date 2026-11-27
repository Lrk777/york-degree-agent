"""The checklist view the web page draws, on the REAL FW25/26 Data Science BSc Honours requirements."""
import json
from pathlib import Path

from ydn import webapi
from ydn.reqparse import parse_requirements

from test_real_calendar import SYNTH

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures"


def load(catalog):
    import yaml
    tree = parse_requirements((FIX / "real_ds_bsc_honours_fw25_26.html").read_text())
    courses = [{"c": f"{c.code.full} {c.credits:.2f}", "t": c.title, "cr": c.credits} for c in catalog.courses]
    seen = {c["c"].rsplit(" ", 1)[0] for c in courses}
    for n in tree.walk():
        for c in n.courses:
            if c.full not in seen:
                seen.add(c.full)
                courses.append({"c": f"{c.full} {(c.credits or 3.0):.2f}", "t": "", "cr": c.credits or 3.0})
    programs = {"P": {"code": "SC-DASC", "title": "Data Science - Bachelor of Science - Honours",
                      "faculty": "Faculty of Science (SC)", "tree": tree.to_dict()}}
    rules = yaml.safe_load((ROOT / "rules/buckets.yaml").read_text())
    webapi.load_year("FW25/26", json.dumps({"courses": courses, "subjects": {}}), json.dumps(programs),
                     json.dumps(rules, default=str), "[]")
    return json.loads(webapi.audit("P", SYNTH))


def find(views, pred):
    for v in views:
        if pred(v):
            return v
        hit = find(v.get("children", []), pred)
        if hit:
            return hit


def test_sections_and_titles(catalog):
    out = load(catalog)
    assert [s["title"] for s in out["sections"]] == [
        "General Education", "Major Requirements", "Stream Requirements", "Science Breadth",
        "Upper-Level Requirement", "Additional Elective Credits"]
    ns = find(out["sections"], lambda v: v.get("rule_id") == "General Education:A.1.1")
    assert ns["title"] == "Non-Science Requirement" and ns["status"] == "missing" and ns["need"] == 6
    assert [u["course"] for u in ns["used"]] == ["AP/HIST 1010"]


def test_required_courses_have_per_course_state(catalog):
    out = load(catalog)
    core = find(out["sections"], lambda v: v.get("rule_id") == "Major Requirements:C")
    states = {i["course"]: i["state"] for i in core["items"]}
    assert states["SC/MATH 1013"] == "done" and states["SC/MATH 4949"] == "in_progress"
    phil = find(out["sections"], lambda v: v.get("rule_id") == "Major Requirements:H")
    assert phil["items"][0]["course"] == "AP/PHIL 3500" and phil["items"][0]["state"] == "missing"
    comp = find(out["sections"], lambda v: v.get("title") == "Computation")
    assert comp is not None


def test_stream_choice_is_reported(catalog):
    out = load(catalog)
    stream = find(out["sections"], lambda v: v.get("kind") == "group" and len(v.get("options", [])) == 5)
    assert stream["chosen"] == ["Computation"]


def test_course_map_says_where_each_course_counted(catalog):
    out = load(catalog)
    by = {c["course"]: c for c in out["courses"]}
    assert any("Non-Science" in x for x in by["AP/HIST 1010"]["counted_in"])
    assert any(x.startswith("Major Requirements") for x in by["SC/MATH 2030"]["counted_in"])
    assert any(x.startswith("Science Breadth") for x in by["HH/PSYC 1010"]["counted_in"])
    assert by["SC/MATH 4949"]["status"] == "in_progress"


def test_search_index_is_built_lazily(catalog):
    load(catalog)
    r = webapi.S["retriever"]
    assert r._r is None
    r.search("pass fail", "FW25/26")
    assert r._r is not None
