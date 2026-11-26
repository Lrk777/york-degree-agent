"""The free-text interpreter, on rule wordings copied from York's 2025-26 calendar."""
import pytest

from ydn.audit import infer_major_subjects
from ydn.catalog import Catalog
from ydn.freetext import interpret
from ydn.offerings import parse_schedule

NAMES = {"anthropology": {"AP/ANTH"}, "history": {"AP/HIST"}, "psychology": {"HH/PSYC", "GL/PSYC"},
         "mathematics": {"SC/MATH", "GL/MATH"}, "linguistics": {"AP/LING"}, "information technology": {"AP/ITEC"},
         "biology": {"SC/BIOL"}}
KNOWN = {"ANTH", "HIST", "PSYC", "MATH", "LING", "EECS", "ITEC", "CMDS", "CORE", "ADMS", "VISA", "ARTH", "BIOL"}


def I(text, section="", group=""):
    return interpret(text, section, group, NAMES, KNOWN)


@pytest.mark.parametrize("text,section,expect", [
    ("GL/CORE credits at the 1000, 2000, or 3000 level", "", ({"GL/CORE"}, {1000, 2000, 3000})),
    ("additional credits at the 3000 or 4000 level in anthropology;", "Major Requirements", ({"AP/ANTH"}, {3000, 4000})),
    ("psychology (PSYC) at the 3000 level.", "", ({"HH/PSYC", "GL/PSYC", "PSYC"}, {3000})),
    ("at the 4000 level in CMDS", "", ({"CMDS"}, {4000})),
])
def test_subject_and_level(text, section, expect):
    r = I(text, section)
    assert r.usable and r.subjects == expect[0] and r.levels == expect[1]


def test_bare_level_inherits_major_from_section():
    r = I("at the 3000 level.", "Major Requirements")
    assert r.usable and r.major and r.levels == {3000}


def test_outside_major_and_electives():
    assert I("outside the major").outside_major
    assert I("additional elective credits.").any
    r = I("free elective credits – excluding major FA/VISA or FA/ARTH")
    assert r.any and r.exclude_subjects == {"FA/VISA", "FA/ARTH"}


def test_non_subject_and_outside_list():
    r = I("non-MATH credits.")
    assert r.usable and r.any and "MATH" in r.exclude_subjects
    r = I("outside EECS, mathematics, statistics and information technology.")
    assert {"EECS", "SC/MATH", "AP/ITEC"} <= r.exclude_subjects


def test_subclause_is_a_note_not_the_rule_level():
    r = I("additional credits in linguistics (LING), including at least 6 credits at the 4000 level.")
    assert r.usable and r.levels is None and "4000" in r.note


def test_parenthetical_hint_does_not_narrow():
    r = I("Any 3 Credit course (Could be extra ADMS courses).")
    assert r.usable and r.any and not r.subjects


def test_lists_we_do_not_have_go_to_advisor():
    for t in ["at the 1000 level in approved Faculty of Health general education",
              "from natural science (NATS) (with no more than nine credits counting towards this requirement)",
              "from the humanities category from the Faculty of Science Bachelor of Arts approved list"]:
        r = I(t)
        assert not r.usable and r.needs


def test_unknown_wording_is_not_guessed():
    r = I("in history at the 4000 level. 4000-level courses at the Glendon Campus or other universities do not satisfy this requirement.")
    assert not r.usable and r.unparsed
    assert not I("in the minor subject area normally including at least six credits at the 3000 level;").usable


def test_double_major_note():
    r = I("Students must complete a minimum of 30 credits. This requirement is deemed fulfilled by the other program")
    assert not r.usable and "double" in r.needs


def test_rule_checks_courses():
    cat = Catalog.from_records("X", [{"id": "AP/ANTH3100 Cr=3.00 EN"}, {"id": "AP/ANTH2100 Cr=3.00 EN"},
                                     {"id": "SC/MATH3330 Cr=3.00 EN"}, {"id": "HH/PSYC3010 Cr=3.00 EN"}])
    by = {c.code.full: c for c in cat.courses}
    r = I("additional credits at the 3000 or 4000 level in anthropology;", "Major Requirements")
    assert r.check(by["AP/ANTH 3100"], {"ANTH"})[0] and not r.check(by["AP/ANTH 2100"], {"ANTH"})[0]
    assert not I("outside the major").check(by["SC/MATH 3330"], {"MATH"})[0]
    sci = I("in science disciplines outside the major")
    assert sci.check(by["HH/PSYC 3010"], {"MATH"})[0] and not sci.check(by["SC/MATH 3330"], {"MATH"})[0]


def test_infer_major(tree):
    assert infer_major_subjects(tree, "SC-DASC", {"MATH", "EECS"}) == {"MATH"}
    assert infer_major_subjects(tree, "AP-ANTH", {"ANTH"}) == {"ANTH"}


def test_parse_schedule():
    html = ("<td>Term F     Section A</td><td>LECT 01</td> <td>Term W Section M</td>"
            "<td>Term W  Section N</td>")
    assert parse_schedule(html) == {"terms": ["F", "W"], "sections": 3}
    assert parse_schedule("") == {"terms": [], "sections": 0}
