"""The York degree agent: an LLM that can only answer through tools.

Design rules (written into the system prompt and enforced by the tools):
  1. Whether a requirement is met, and how many credits are left, comes ONLY from
     the audit engine. The model explains; it never computes degree progress itself.
  2. Policy answers must quote calendar text returned by search_calendar, for the
     student's own calendar year, with the source.
  3. If a rule is not encoded or the data is unclear, say so and route to an advisor.
  4. End with a short summary the student can bring to their advisor to confirm.

The LLM is pluggable: Anthropic's Messages API, or any OpenAI-compatible endpoint
(OpenAI, Groq, a local Ollama server). Tests use a scripted fake.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from .audit import Auditor, Result
from .codes import parse_code
from .report import render
from .retrieval import Retriever
from .suggest import open_requirements, suggest_for, where_does_it_count
from .transcript import Attempt

SYSTEM = """You are an unofficial degree-planning assistant for York University students.
Rules you must follow:
- Use the tools for every fact about the student's progress, courses, credits or prerequisites.
  Never decide yourself whether a requirement is met; report what get_progress / does_it_count return.
- For policy questions call search_calendar and quote the calendar text you rely on, naming the
  calendar year and source. The student's calendar year is {year}; do not use other years' rules.
- If a tool says a rule is not encoded, a prerequisite needs a human check, or data is missing,
  say so plainly and tell the student to confirm with their Faculty's advising office.
- Never ask for or repeat student numbers, names or other personal details.
- Finish every answer with "To confirm with your advisor:" and 1-3 bullet points.
Student program: {program} (calendar {year})."""

TOOLS = [
    {"name": "get_progress", "description": "Degree audit for the student: every requirement with status "
     "(met, in_progress, missing, manual) and credits still needed.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_courses", "description": "Courses that would count toward an unmet requirement, with the reason "
     "each counts and whether prerequisites are met. Use requirement_id from get_progress.",
     "input_schema": {"type": "object", "properties": {"requirement_id": {"type": "string"},
                                                       "limit": {"type": "integer"}},
                      "required": ["requirement_id"]}},
    {"name": "does_it_count", "description": "Given a course code (e.g. 'AP/HIST 3500'), list every requirement it "
     "would count toward or not, prerequisites, and double-counting warnings.",
     "input_schema": {"type": "object", "properties": {"course": {"type": "string"}}, "required": ["course"]}},
    {"name": "search_calendar", "description": "Search the student's academic calendar year and York rule pages. "
     "Returns text with source and year to quote.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


class LLM(Protocol):
    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Return {'text': str, 'tool_calls': [{'id','name','input'}], 'raw_content': any}."""


@dataclass
class DegreeAgent:
    auditor: Auditor
    tree: object
    attempts: list[Attempt]
    retriever: Retriever
    program: str
    year: str
    max_steps: int = 6
    trace: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.result: Result = self.auditor.run(self.tree, self.attempts)
        self._by_id = {}

        def idx(r):
            if r.node.rule_id:
                self._by_id[r.node.rule_id] = r
            for c in r.children:
                idx(c)
        idx(self.result)

    # ---------------------------------------------------------------- tools
    def tool(self, name: str, args: dict):
        if name == "get_progress":
            opens = open_requirements(self.result)
            return {"report": render(self.result),
                    "open_requirements": [{"requirement_id": r.node.rule_id, "label": r.node.label,
                                           "kind": r.node.kind, "status": r.status, "need_credits": r.need,
                                           "detail": r.message} for r in opens]}
        if name == "find_courses":
            r = self._by_id.get(args.get("requirement_id", ""))
            if r is None:
                return {"error": "unknown requirement_id; call get_progress first"}
            overlays = [x for x in open_requirements(self.result) if x.node.kind == "upper_level"]
            return {"requirement": r.node.label or r.node.rule_id,
                    "courses": [s.to_dict() for s in suggest_for(r, self.auditor, self.attempts, overlays,
                                                                  int(args.get("limit", 12)))],
                    "note": "Course offerings by term are not checked yet; confirm the course runs in the term you want."}
        if name == "does_it_count":
            code = parse_code(args.get("course", ""), known_subjects=self.auditor.catalog.subjects)
            if code is None:
                return {"error": "could not read a course code; use a format like 'AP/HIST 3500'"}
            return where_does_it_count(code, self.result, self.auditor, self.attempts)
        if name == "search_calendar":
            return {"results": self.retriever.search(args.get("query", ""), self.year)}
        return {"error": f"unknown tool {name}"}

    # ---------------------------------------------------------------- loop
    def ask(self, question: str, llm: LLM) -> str:
        system = SYSTEM.format(year=self.year, program=self.program)
        messages = [{"role": "user", "content": question}]
        for _ in range(self.max_steps):
            out = llm.chat(system, messages, TOOLS)
            if not out.get("tool_calls"):
                return out.get("text", "")
            messages.append({"role": "assistant", "content": out.get("raw_content") or out.get("text", ""),
                             "tool_calls": out["tool_calls"]})
            for call in out["tool_calls"]:
                result = self.tool(call["name"], call.get("input") or {})
                self.trace.append({"tool": call["name"], "input": call.get("input"), "output": result})
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                                 "content": json.dumps(result, default=str)[:12000]})
        return ("I could not finish this within the tool-step limit. Please ask a narrower question, "
                "or confirm with your advisor.")


