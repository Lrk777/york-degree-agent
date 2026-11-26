"""Rule-tree node (no third-party imports, so it also runs in the browser)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .codes import CourseCode, find_codes


@dataclass
class Node:
    kind: str                       # group | all_of | credits_from | bucket | upper_level | total | note
    label: str = ""
    rule_id: str = ""
    mode: str = "all"               # groups: 'all' or a number as text ("1")
    credits: float = 0.0
    courses: list[CourseCode] = field(default_factory=list)
    text: str = ""
    children: list["Node"] = field(default_factory=list)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "label": self.label, "rule_id": self.rule_id}
        if self.kind == "group":
            d["mode"] = self.mode
            d["credits"] = self.credits
            d["children"] = [c.to_dict() for c in self.children]
        else:
            d.update(credits=self.credits, text=self.text, courses=[c.full + (f" {c.credits:.2f}" if c.credits else "") for c in self.courses])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        if d["kind"] == "group":
            return cls("group", d.get("label", ""), d.get("rule_id", ""), mode=d.get("mode", "all"),
                       credits=d.get("credits", 0.0) or 0.0,
                       children=[cls.from_dict(c) for c in d.get("children", [])])
        courses = []
        for full in d.get("courses", []):
            c = find_codes(full)
            if c:
                courses.append(c[0])
        return cls(d["kind"], d.get("label", ""), d.get("rule_id", ""), credits=d.get("credits", 0.0) or 0.0,
                   courses=courses, text=d.get("text", ""))

    @property
    def section(self) -> str:
        return self.rule_id.split(":", 1)[0] if ":" in self.rule_id else ""
