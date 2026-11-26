"""Parser + engine against the REAL FW25/26 Data Science BSc Honours requirements."""
from pathlib import Path

import pytest

from ydn.audit import Auditor
from ydn.catalog import Catalog
from ydn.reqparse import parse_requirements
from ydn.suggest import open_requirements, suggest_for
from ydn.transcript import parse_transcript_text

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def real_tree():
    return parse_requirements((FIX / "real_ds_bsc_honours_fw25_26.html").read_text())


def test_real_sections_and_numbers(real_tree):
    assert [s.label for s in real_tree.children] == [
        "General Education", "Major Requirements", "Stream Requirements", "Science Breadth",
        "Upper-Level Requirement", "Additional Elective Credits"]
    leaves = [n for n in real_tree.walk() if n.kind != "group"]
    up = next(n for n in leaves if n.kind == "upper_level")
    tot = next(n for n in leaves if n.kind == "total")
    assert up.credits == 42 and tot.credits == 120
    ids = [n.rule_id for n in leaves if n.rule_id]
    assert len(ids) == len(set(ids)), "rule ids must be unique after section prefixing"


def test_real_streams_and_choices(real_tree):
    choice_groups = [n for n in real_tree.walk() if n.kind == "group" and n.mode == "1"]
    stream = next(g for g in choice_groups if len(g.children) == 5)
    assert [c.label for c in stream.children] == ["Business", "Health", "Computation", "Optimization",
                                                    "Computational Arts"]
    ds_choice = next(g for g in choice_groups if len(g.children) == 2)    # EECS 2011 or ITEC 2620
    kinds = sorted(c.kind for c in ds_choice.children)
    assert kinds == ["all_of", "credits_from"]


def test_real_core_courses(real_tree):
    core = next(n for n in real_tree.walk() if n.rule_id == "Major Requirements:C")
    fulls = [c.full for c in core.courses]
    assert len(fulls) == 15 and "SC/MATH 4949" in fulls and "SC/MATH 2030" in fulls
    phil = next(n for n in real_tree.walk() if n.rule_id == "Major Requirements:H")
    assert phil.kind == "credits_from" and [c.full for c in phil.courses] == ["AP/PHIL 3500"]
    buckets = [n.text[:40] for n in real_tree.walk() if n.kind == "bucket"]
    assert len(buckets) == 3


# A synthetic 4th-year Computation-stream student (NOT a real record).
SYNTH = """
SC MATH 1013 3.00 A
SC MATH 1014 3.00 B+
SC MATH 1019 3.00 B
SC MATH 1025 3.00 A
SC MATH 1130 3.00 A
SC MATH 1131 3.00 B+
SC MATH 2015 3.00 B
SC MATH 2030 3.00 B
SC MATH 2130 3.00 A
SC MATH 2131 3.00 B+
SC MATH 3330 3.00 A
SC MATH 3333 3.00 A
SC MATH 4036 3.00 IP
SC MATH 4931 3.00 IP
SC MATH 4949 6.00 IP
LE EECS 1015 3.00 A
LE EECS 1516 3.00 B
LE EECS 2502 3.00 B
AP ITEC 2620 3.00 B
AP ITEC 3221 3.00 A
AP ITEC 3310 3.00 A
AP WRIT 2202 3.00 B+
SC MATH 2022 3.00 B
SC MATH 2041 3.00 A
SC MATH 3241 3.00 B
SC BIOL 1000 3.00 B
SC CHEM 1000 3.00 C+
HH PSYC 1010 6.00 B
AP HIST 1010 6.00 B
"""


def test_end_to_end_on_real_rules(real_tree, catalog, buckets):
    # catalog = fixture catalog + every course the real program names (credits from the rule list)
    recs = [{"id": c.code.full.replace(" ", "") + f" Cr={c.credits:.2f} EN", "title": c.title,
             "description": c.desc.text} for c in catalog.courses]
    for n in real_tree.walk():
        for c in n.courses:
            recs.append({"id": f"{c.fac}/{c.subj}{c.num} Cr={(c.credits or 3.0):.2f} EN", "title": "", "description": ""})
    cat = Catalog.from_records("FW25/26", recs)
    aud = Auditor(cat, buckets, program_faculty="SC", major_subjects={"MATH"})
    res = aud.run(real_tree, parse_transcript_text(SYNTH))

    def find(rid):
        stack = [res]
        while stack:
            r = stack.pop()
            if r.node.rule_id == rid:
                return r
            stack.extend(r.children)

    assert find("Major Requirements:C").status == "in_progress"      # capstone etc. in progress
    assert find("Major Requirements:H").status == "missing"          # PHIL 3500 not taken
    assert find("Stream Requirements:D.1").status == "missing"       # MATH 3090 left
    assert "SC/MATH 3090" in find("Stream Requirements:D.1").message
    assert find("Stream Requirements:A.1").status == "not_chosen"    # Business not chosen
    ns = find("General Education:A.1.1")
    assert ns.status == "missing" and ns.need == 6
    opens = open_requirements(res)
    labels = {r.node.rule_id for r in opens}
    assert "General Education:A.1.1" in labels and "Upper-Level Requirement:A" in labels
    sugg = suggest_for(ns, aud, parse_transcript_text(SYNTH), [r for r in opens if r.node.kind == "upper_level"])
    names = [s.course.code.full for s in sugg]
    assert "AP/PHIL 1100" in names and "AP/HIST 1010" not in names


def test_where_does_it_count(real_tree, catalog, buckets):
    from ydn.codes import parse_code
    from ydn.suggest import where_does_it_count
    recs = [{"id": c.code.full.replace(" ", "") + f" Cr={c.credits:.2f} EN", "title": c.title,
             "description": c.desc.text} for c in catalog.courses]
    for n in real_tree.walk():
        for c in n.courses:
            recs.append({"id": f"{c.fac}/{c.subj}{c.num} Cr={(c.credits or 3.0):.2f} EN", "title": "", "description": ""})
    cat = Catalog.from_records("FW25/26", recs)
    aud = Auditor(cat, buckets, program_faculty="SC", major_subjects={"MATH"})
    att = parse_transcript_text(SYNTH)
    res = aud.run(real_tree, att)
    phil = where_does_it_count(parse_code("AP/PHIL 3500"), res, aud, att)
    assert phil["double_count_warning"]                         # required by major AND matches non-science
    hist = where_does_it_count(parse_code("AP/HIST 3500 3.00"), res, aud, att)
    reqs = {h["requirement"] for h in hist["counts_toward"]}
    assert "Non-Science Requirement (Faculty of Science)" in reqs and "Upper-level credits" in reqs
    assert hist["prerequisites"] == "check"                     # '24 credits completed' -> human check
    sosc = where_does_it_count(parse_code("AP/SOSC 1009"), res, aud, att)
    assert sosc["already_taken_or_excluded"] is False
    assert any("cross-listed" in h["why"] for h in sosc["does_not_count_toward"])
