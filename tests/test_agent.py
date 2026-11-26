import json
from pathlib import Path

from ydn.agent import TOOLS, DegreeAgent
from ydn.audit import Auditor
from ydn.catalog import Catalog
from ydn.reqparse import parse_requirements
from ydn.retrieval import Chunk, Retriever, chunks_from_rules
from ydn.transcript import parse_transcript_text

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures"

from test_real_calendar import SYNTH  # noqa: E402


class ScriptedLLM:
    """Plays back tool calls, then answers using what the tools returned."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def chat(self, system, messages, tools):
        self.seen.append(messages[-1])
        step = self.script.pop(0)
        if callable(step):
            return step(messages)
        return step


def make_agent(catalog, buckets):
    tree = parse_requirements((FIX / "real_ds_bsc_honours_fw25_26.html").read_text())
    recs = [{"id": c.code.full.replace(" ", "") + f" Cr={c.credits:.2f} EN", "title": c.title,
             "description": c.desc.text} for c in catalog.courses]
    for n in tree.walk():
        for c in n.courses:
            recs.append({"id": f"{c.fac}/{c.subj}{c.num} Cr={(c.credits or 3.0):.2f} EN", "title": "", "description": ""})
    cat = Catalog.from_records("FW25/26", recs)
    chunks = chunks_from_rules(ROOT / "rules/buckets.yaml") + [
        Chunk("Courses taken on a pass/fail basis cannot be used toward the non-science requirement.",
              "FW25/26", "Faculty of Science rules", "Pass/Fail Grading Option"),
        Chunk("OLD RULE: pass/fail courses count toward general education.", "FW19/20", "old calendar", "Pass/Fail")]
    return DegreeAgent(Auditor(cat, buckets, "SC", {"MATH"}), tree, parse_transcript_text(SYNTH),
                       Retriever(chunks), "Data Science BSc Honours", "FW25/26")


def test_tool_schemas_are_valid():
    for t in TOOLS:
        assert t["input_schema"]["type"] == "object" and t["description"]


def test_agent_loop_uses_tools_and_returns_answer(catalog, buckets):
    agent = make_agent(catalog, buckets)

    def final(messages):
        tool_out = json.loads(messages[-1]["content"])
        reqs = [h["requirement"] for h in tool_out["counts_toward"]]
        return {"text": f"HIST 3500 counts toward: {', '.join(reqs)}. To confirm with your advisor: ...",
                "tool_calls": []}
    llm = ScriptedLLM([
        {"text": "", "tool_calls": [{"id": "t1", "name": "does_it_count", "input": {"course": "AP/HIST 3500"}}]},
        final])
    answer = agent.ask("Will HIST 3500 count for anything?", llm)
    assert "Non-Science" in answer and "Upper-level" in answer
    assert agent.trace[0]["tool"] == "does_it_count"


def test_progress_tool_lists_open_requirements(catalog, buckets):
    agent = make_agent(catalog, buckets)
    out = agent.tool("get_progress", {})
    ids = [r["requirement_id"] for r in out["open_requirements"]]
    assert "General Education:A.1.1" in ids
    found = agent.tool("find_courses", {"requirement_id": "General Education:A.1.1", "limit": 5})
    assert found["courses"] and all("counts_because" in c for c in found["courses"])


def test_search_is_scoped_to_student_calendar_year(catalog, buckets):
    agent = make_agent(catalog, buckets)
    res = agent.tool("search_calendar", {"query": "pass/fail general education"})["results"]
    years = {r["year"] for r in res}
    assert "FW19/20" not in years and res


def test_bad_inputs_are_handled(catalog, buckets):
    agent = make_agent(catalog, buckets)
    assert "error" in agent.tool("does_it_count", {"course": "the history one"})
    assert "error" in agent.tool("find_courses", {"requirement_id": "nope"})
    assert agent.tool("does_it_count", {"course": "AP/ZZZZ 9999"}).get("error") or \
        agent.tool("does_it_count", {"course": "AP/HIST 9000"})["found"] is False
