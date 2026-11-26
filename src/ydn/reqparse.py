"""Parse York's Kuali requirement HTML into a rule tree.

The calendar API returns each program's requirements as rendered HTML:

  <section><header><h2>General Education</h2> 18 Total Credits</header>
    <ul><div><span class=rules_groupHeader_..>Non-Science Requirement</span>
      <li><span>Complete all of the following</span>
        <ul><li data-test="ruleView-A.1.1"><div data-test="ruleView-A.1.1-result">
            Completed at least 12 credits from the following types of courses: ...

Leaf rules seen in the 2025-26 calendar:
  "Passed the following: <courses>"                       -> all_of
  "Earned at least N credits from the following: <list>"  -> credits_from
  "Completed at least N credits from the following types of courses: <text>"
        -> credits_from when the text is only course codes, otherwise bucket
           (free text such as non-science or electives; rules/*.yaml turns it into a filter)
  "A minimum of N credits must be at the 3000 level ..."  -> upper_level
  "As required for an overall total of at least N credits" -> total
  anything else                                           -> note
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from .codes import CourseCode, find_codes
from .tree import Node  # noqa: F401  (re-exported)

_NUM = r"(\d+(?:\.\d+)?)"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _leaf(li: Tag, label: str) -> Node:
    rid = li.get("data-test", "").replace("ruleView-", "")
    res = li.find(attrs={"data-test": re.compile(r"-result$")}) or li
    text = _clean(res.get_text(" "))
    # Course codes: prefer link text (Kuali links each course), else parse the text.
    linked = [find_codes(_clean(a.get_text(" "))) for a in res.find_all("a")]
    courses = [c[0] for c in linked if c] or find_codes(text)

    m = re.match(r"Passed the following", text, re.I)
    if m:
        return Node("all_of", label, rid, courses=courses, text=text,
                    credits=sum(c.credits or 0 for c in courses))
    m = re.match(rf"Earned at least {_NUM} credits from the following", text, re.I)
    if m:
        return Node("credits_from", label, rid, credits=float(m.group(1)), courses=courses, text=text)
    m = re.match(rf"Completed at least {_NUM} credits from the following types of courses:?\s*(.*)",
                 text, re.I)
    if m:
        rest = m.group(2)
        if re.search(r"total of at least|which meet the faculty's degree", rest, re.I):
            tm = re.search(rf"(?:total of )?at least {_NUM} credits", rest, re.I)
            return Node("total", label, rid, credits=float(tm.group(1)) if tm else float(m.group(1)), text=rest)
        # "LE/EECS 2011 - Fundamentals of Data Structures (3.00)" is really a course rule;
        # "in science disciplines outside the major" or "additional elective credits" is a bucket.
        generic = re.search(r"\b(electives?|outside|areas?|level|disciplines?|approved|any|types?|"
                            r"different|non-?science|general education)\b", rest, re.I)
        if courses and not generic:
            return Node("credits_from", label, rid, credits=float(m.group(1)), courses=courses, text=text)
        return Node("bucket", label, rid, credits=float(m.group(1)), text=rest or text)
    m = re.search(rf"minimum of {_NUM} credits must be at the (\d)000 level", text, re.I)
    if m:
        return Node("upper_level", label, rid, credits=float(m.group(1)), text=text)
    m = re.search(rf"overall total of at least {_NUM} credits", text, re.I)
    if m:
        return Node("total", label, rid, credits=float(m.group(1)), text=text)
    return Node("note", label, rid, text=text)


def _group_from_li(li: Tag, label: str) -> Optional[Node]:
    span = li.find("span", recursive=False)
    head = _clean(span.get_text(" ")) if span else ""
    m = re.match(r"Complete (all|\d+) of the following", head, re.I)
    if not m:
        return None
    g = Node("group", label=label, mode=m.group(1).lower())
    ul = li.find("ul", recursive=False)
    if ul is not None:
        g.children = _parse_list(ul)
    return g


def _parse_list(ul: Tag) -> list[Node]:
    out: list[Node] = []
    for child in ul.find_all(["div", "li"], recursive=False):
        if child.name == "div":
            hdr = child.find("span", class_=re.compile(r"rules_groupHeader"), recursive=False)
            label = _clean(hdr.get_text(" ")) if hdr else ""
            for li in child.find_all("li", recursive=False):
                out.append(_parse_li(li, label))
        else:
            out.append(_parse_li(child, ""))
    return [n for n in out if n is not None]


def _parse_li(li: Tag, label: str) -> Optional[Node]:
    if li.get("data-test", "").startswith("ruleView-"):
        return _leaf(li, label)
    g = _group_from_li(li, label)
    if g is not None:
        return g
    # plain list item (e.g. a bare course) – treat as note so nothing is lost
    return Node("note", label, text=_clean(li.get_text(" ")))


def parse_requirements(html: str) -> Node:
    """Whole program → root group with one child group per calendar section."""
    soup = BeautifulSoup(html or "", "html.parser")
    root = Node("group", label="Program", mode="all")
    for sec in soup.find_all("section"):
        h2 = sec.find("h2")
        name = _clean(h2.get_text(" ")) if h2 else "Requirements"
        total = None
        header = sec.find("header")
        if header:
            m = re.search(rf"{_NUM}\s*Total Credits", _clean(header.get_text(" ")))
            total = float(m.group(1)) if m else None
        group = Node("group", label=name, mode="all", credits=total or 0.0)
        for ul in sec.find_all("ul", recursive=True):
            if ul.find_parent("li") is None:      # top-level lists of this section only
                group.children.extend(_parse_list(ul))
        # Kuali rule ids repeat across sections ("A.1" appears several times): make them unique.
        for n in group.walk():
            if n.rule_id:
                n.rule_id = f"{name}:{n.rule_id}"
        root.children.append(group)
    return root
