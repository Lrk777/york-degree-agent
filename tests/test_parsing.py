from ydn.codes import find_codes, parse_code
from ydn.descparse import parse_description


def test_code_variants_normalize_to_same_course():
    a = parse_code("SC/MATH2030 Cr=3.00 EN")
    b = parse_code("SC/MATH 2030 3.00")
    c = parse_code("SC MATH 2030 3.00")
    d = parse_code("MATH 2030", known_subjects={"MATH"})
    assert a.full == b.full == c.full == "SC/MATH 2030"
    assert a.credits == 3.0 and d.fac is None
    assert a.same_course(d) and d.same_course(b)


def test_terms_and_years_are_not_courses():
    assert find_codes("FALL 2023 term", known_subjects={"MATH"}) == []
    assert find_codes("SUMMER 2014 and FW 2023") == []


def test_different_faculties_are_different_courses():
    assert not parse_code("AP/GEOG 1000 6.00").same_course(parse_code("EU/GEOG 1000 6.00"))


def test_prereq_one_of_is_single_or_clause():
    d = parse_description("Prerequisite: One of SC/MATH 1014 3.00, SC/MATH 1310 3.00, SC/ISCI 1402 3.00.")
    assert len(d.prereq.clauses) == 1
    assert [c.full for c in d.prereq.clauses[0]] == ["SC/MATH 1014", "SC/MATH 1310", "SC/ISCI 1402"]


def test_prereq_and_gives_two_clauses_and_min_grade():
    d = parse_description("Prerequisites: SC/MATH 2022 3.00 and LE/EECS 1012 3.00 with a minimum grade of C+.")
    assert [[c.full for c in cl] for cl in d.prereq.clauses] == [["SC/MATH 2022"], ["LE/EECS 1012"]]
    assert d.prereq.min_grade == "C+"


def test_non_course_condition_is_kept_for_humans():
    d = parse_description("Prerequisite: 24 credits completed or permission of the instructor.")
    assert d.prereq.clauses == [] and d.prereq.conditions


def test_exclusions_crosslist_and_gened_flag():
    d = parse_description("Text. Course credit exclusions: GL/ECON 2710 3.00, AP/ECON 3580 3.00. "
                          "Cross-listed to: SC/STS 1009 6.00. Note: This course has been approved in the "
                          "Faculty of Liberal Arts & Professional Studies general education.")
    assert [c.full for c in d.exclusions] == ["GL/ECON 2710", "AP/ECON 3580"]
    assert [c.full for c in d.crosslisted] == ["SC/STS 1009"]
    assert d.gen_ed_approved


def test_rule_tree_shape(tree):
    sections = [s.label for s in tree.children]
    assert sections == ["General Education", "Major Requirements", "Stream Requirements",
                        "Science Breadth", "Upper-Level Requirement", "Additional Elective Credits"]
    kinds = [n.kind for n in tree.walk() if n.kind != "group"]
    assert kinds.count("bucket") == 3            # non-science, science breadth, electives
    assert "upper_level" in kinds and "total" in kinds
    stream = next(n for n in tree.walk() if n.kind == "group" and n.mode == "1")
    assert [c.label for c in stream.children] == ["Business", "Computation"]


def test_course_list_from_kuali_links(tree):
    found = next(n for n in tree.walk() if n.rule_id == "General Education:A.2.1")
    assert found.kind == "credits_from" and found.credits == 6
    assert "SC/CHEM 1000" in [c.full for c in found.courses]


def test_types_of_courses_with_only_a_course_is_a_course_rule(tree):
    n = next(n for n in tree.walk() if n.rule_id == "Major Requirements:B.2")
    assert n.kind == "credits_from" and [c.full for c in n.courses] == ["LE/EECS 2011"]
