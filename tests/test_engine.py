from ydn.audit import Auditor
from ydn.suggest import open_requirements, prereq_status, suggest_for
from ydn.transcript import parse_transcript_text, redact

# Synthetic student (not a real record). Mixed formats on purpose.
TRANSCRIPT = """
Name: Test Student
Student number: 219999999
2023 FW SC MATH 1300 3.00 Applied Calculus I A
2023 FW SC MATH 1310 3.00 Applied Calculus II B+
2024 FW SC/MATH 2030 3.00 Elementary Probability C
2023 FW LE EECS 1012 3.00 Net-centric Intro B
2024 FW LE EECS 2011 3.00 Data Structures B
2023 FW SC BIOL 1000 3.00 Biology I B
2023 FW SC CHEM 1000 3.00 Chemical Structure C+
2024 FW SC MATH 2022 3.00 Linear Algebra II B
2024 FW SC MATH 2041 3.00 Symbolic Computation A
2025 FW SC MATH 3241 3.00 Numerical Methods I IP
2023 FW AP HIST 1010 6.00 Making History B
2024 FW AP SOSC 1009 6.00 Science and Society A
2023 FW HH PSYC 1010 6.00 Intro Psychology B
2024 SU AP EN 1001 3.00 Reading Literature P/F P
2022 FW SC PHYS 1411 3.00 Physics Fundamentals 1 F
2023 FW SC PHYS 1411 3.00 Physics Fundamentals 1 C
"""


def attempts():
    return parse_transcript_text(TRANSCRIPT)


def audit(catalog, buckets, tree, **kw):
    a = Auditor(catalog, buckets, program_faculty="SC", major_subjects={"MATH"}, **kw)
    return a, a.run(tree, attempts())


def find(res, label=None, rule_id=None):
    stack = [res]
    while stack:
        r = stack.pop()
        if (label and r.node.label == label) or (rule_id and r.node.rule_id == rule_id):
            return r
        stack.extend(r.children)
    raise KeyError(label or rule_id)


def test_redaction_removes_pii():
    out = redact(TRANSCRIPT)
    assert "219999999" not in out and "Test Student" not in out


def test_transcript_parsing_and_repeats():
    at = {a.code.key: a for a in attempts()}
    assert at["MATH 2030"].grade == "C" and at["MATH 2030"].status == "passed"
    assert at["MATH 3241"].status == "in_progress"
    assert at["PHYS 1411"].grade == "C"          # repeated course: latest attempt counts
    assert at["EN 1001"].pass_fail


def test_named_courses_met(catalog, buckets, tree):
    _, res = audit(catalog, buckets, tree)
    assert find(res, rule_id="Major Requirements:B.1").status == "met"
    assert find(res, rule_id="Major Requirements:B.2").status == "met"


def test_stream_choice_picks_the_one_in_progress(catalog, buckets, tree):
    _, res = audit(catalog, buckets, tree)
    comp, bus = find(res, label="Computation"), find(res, label="Business")
    assert bus.status == "not_chosen"
    assert comp.status == "missing"               # MATH 3090 not taken yet
    assert "SC/MATH 3090" in comp.message


def test_non_science_excludes_sc_crosslist_and_pass_fail(catalog, buckets, tree):
    _, res = audit(catalog, buckets, tree)
    ns = find(res, rule_id="General Education:A.1.1")
    used = {a.code.key for a in ns.used}
    assert "HIST 1010" in used
    assert "SOSC 1009" not in used                # cross-listed with SC/STS
    assert "EN 1001" not in used                  # pass/fail not accepted
    assert ns.status == "missing" and ns.need == 6


def test_two_area_rule_is_enforced(catalog, buckets, tree):
    from ydn.transcript import parse_transcript_text
    a, _ = audit(catalog, buckets, tree)
    one_area = parse_transcript_text("AP HIST 1010 6.00 B\nAP HIST 3500 3.00 A\nAP HIST 1010 6.00 B")
    one_area += parse_transcript_text("AP/HIST 2600 3.00 B")
    res = a.run(tree, one_area)
    ns = find(res, rule_id="General Education:A.1.1")
    assert ns.status == "missing" and "different areas" in ns.message


def test_science_breadth_skips_major_subject(catalog, buckets, tree):
    _, res = audit(catalog, buckets, tree)
    sb = find(res, rule_id="Science Breadth:D.1")
    used = {a.code.key for a in sb.used}
    assert "PSYC 1010" in used and not any(k.startswith("MATH") for k in used)


def test_overlays(catalog, buckets, tree):
    _, res = audit(catalog, buckets, tree)
    up = find(res, rule_id="Upper-Level Requirement:E.1")
    assert up.status == "missing" and "0 of 18" in up.message and "3 in progress" in up.message
    assert find(res, rule_id="Additional Elective Credits:F.2").status == "missing"


def test_suggestions_for_non_science(catalog, buckets, tree):
    aud, res = audit(catalog, buckets, tree)
    ns = find(res, rule_id="General Education:A.1.1")
    overlays = [r for r in open_requirements(res) if r.node.kind == "upper_level"]
    sugg = suggest_for(ns, aud, attempts(), overlays)
    names = [s.course.code.full for s in sugg]
    assert "AP/HIST 1010" not in names            # already taken
    assert "AP/SOSC 1009" not in names            # SC cross-list
    assert "AP/MODR 1650" not in names            # math/stats technique course
    assert "AP/GWST 2510" not in names            # SC course credit exclusion
    assert "AP/PHIL 1100" in names and "AP/ECON 1000" in names and "FA/MUSI 1530" in names
    econ1010 = next(s for s in sugg if s.course.code.full == "AP/ECON 1010")
    assert econ1010.prereq_status == "missing"
    hist3500 = next(s for s in sugg if s.course.code.full == "AP/HIST 3500")
    assert hist3500.prereq_status == "check" and hist3500.also_helps


def test_prereq_min_grade(catalog):
    c = catalog.get(next(iter([x.code for x in catalog.courses if x.code.key == "MATH 3090"])))
    low = parse_transcript_text("SC MATH 2022 3.00 D+")
    ok = parse_transcript_text("SC MATH 2022 3.00 B")
    assert prereq_status(c.desc.prereq, low)[0] == "missing"
    assert prereq_status(c.desc.prereq, ok)[0] == "met"


def test_unknown_free_text_rule_goes_to_advisor(catalog, buckets):
    from ydn.reqparse import parse_requirements
    html = ('<section><header><h2>X</h2></header><ul><li><span>Complete all of the following</span><ul>'
            '<li data-test="ruleView-Z.1"><div data-test="ruleView-Z.1-result">Completed at least 6 credits '
            'from the following types of courses: approved humanities electives</div></li></ul></li></ul></section>')
    a = Auditor(catalog, buckets, program_faculty="LE")
    res = a.run(parse_requirements(html), attempts())
    z = find(res, rule_id="X:Z.1")
    assert z.status == "manual" and "advisor" in z.message
