import json
from pathlib import Path

from ydn.kuali import KualiClient, undergrad_catalogs
from ydn.pipeline import dq_report, sync_catalogs, sync_courses, sync_programs
from ydn.store import connect

FIX = Path(__file__).parent / "fixtures"
CAT = "6634ddfc480d8a001cce25ab"


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        raise RuntimeError(self.status_code)


class FakeSession:
    """Serves canned API responses; can fail the first N calls with 429."""

    def __init__(self, fail_first=0):
        self.headers, self.calls, self.fail_first = {}, [], fail_first
        html = (FIX / "program_ds_bsc.html").read_text()
        self.routes = {
            "/public/catalogs": [
                {"_id": CAT, "title": "York FW25/26 Undergrad Calendar", "startDate": "2025-05-15", "endDate": "2026-05-14"},
                {"_id": "x", "title": "York FW23/24 Undergrad Calendar - Admin Copy"}],
            f"/programs/{CAT}": [{"code": "SC-DASC", "pid": "P1", "title": "Data Science",
                                  "faculty": {"name": "Faculty of Science (SC)"}}],
            f"/program/{CAT}/P1": {"specializations": [
                {"pid": "S1", "title": "Data Science - Bachelor of Science - Honours",
                 "credentialEarned": {"name": "BSc Honours"}, "creditCompletionRequirements": html,
                 "graduationRequirements": "<p>Minimum GPA 5.00 (C+).</p>"}]},
            f"/courses/{CAT}": [
                {"__catalogCourseId": "SC/MATH2030", "pid": "c1", "subjectCode": {"name": "SC/MATH"}},
                {"__catalogCourseId": "SC/MATH2030", "pid": "c1", "subjectCode": {"name": "SC/MATH"}},  # duplicate row
                {"__catalogCourseId": "AP/HIST1010", "pid": "c2", "subjectCode": {"name": "AP/HIST"}},
                {"__catalogCourseId": "AP/ZZZZ1000", "pid": "c3", "subjectCode": {"name": "AP/ZZZZ"}}],
            f"/course/{CAT}/c1": {"__catalogCourseId": "SC/MATH2030 Cr=3.00 EN", "title": "Elementary Probability",
                                  "credits": {"value": "3.00"},
                                  "description": "Prerequisite: One of SC/MATH 1014 3.00, SC/MATH 1310 3.00."},
            f"/course/{CAT}/c2": {"__catalogCourseId": "AP/HIST1010 Cr=6.00 EN", "title": "Making History",
                                  "credits": {"value": "6.00"}, "description": "History."},
        }

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.fail_first > 0:
            self.fail_first -= 1
            return FakeResp(429)
        path = url.split("/api/v1/catalog")[1]
        return FakeResp(200, self.routes[path]) if path in self.routes else FakeResp(404)


def client(tmp_path, **kw):
    return KualiClient(tmp_path / "cache", min_interval=0, session=FakeSession(**kw), sleep=lambda s: None)


def test_admin_copies_skipped():
    cats = undergrad_catalogs(FakeSession().routes["/public/catalogs"])
    assert list(cats) == ["FW25/26"]


def test_retry_on_429_then_cache(tmp_path):
    c = client(tmp_path, fail_first=2)
    body = c.catalogs()
    assert body[0]["_id"] == CAT and c.stats["retries"] == 2
    c.catalogs()
    assert c.stats["cache"] == 1                 # second call served from disk


def test_full_sync_is_idempotent(tmp_path):
    con = connect(tmp_path / "y.db")
    for _ in range(2):                            # run twice: same rows, no duplicates
        c = client(tmp_path)
        ids = sync_catalogs(c, con)
        progs = sync_programs(c, con, "FW25/26", ids["FW25/26"], ["SC-DASC"])
        stats = sync_courses(c, con, "FW25/26", ids["FW25/26"], {"SC/MATH", "AP/HIST"})
    assert {k: stats[k] for k in ("loaded", "listing_rows", "duplicate_ids", "failed")} == \
        {"loaded": 2, "listing_rows": 4, "duplicate_ids": 1, "failed": 0}
    assert con.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 1
    row = con.execute("SELECT course_full, credits, prereq_json FROM courses WHERE course_key='MATH 2030'").fetchone()
    assert row[0] == "SC/MATH 2030" and row[1] == 3.0
    assert json.loads(row[2])["clauses"] == [["SC/MATH 1014", "SC/MATH 1310"]]
    tree = json.loads(progs[0]["rule_tree_json"]) if isinstance(progs[0]["rule_tree_json"], str) else progs[0]["rule_tree_json"]
    assert tree["children"][0]["label"] == "General Education"


def test_dq_report_flags_missing_rule_courses(tmp_path):
    con = connect(tmp_path / "y.db")
    c = client(tmp_path)
    ids = sync_catalogs(c, con)
    sync_programs(c, con, "FW25/26", ids["FW25/26"], ["SC-DASC"])
    sync_courses(c, con, "FW25/26", ids["FW25/26"], {"SC/MATH", "AP/HIST"})
    rep = dq_report(con, "FW25/26")
    assert rep["hard_ok"]
    assert "SC/BIOL 1000" in rep["soft"]["program_rule_courses_not_in_catalog"]


def test_export_then_webapi_end_to_end(tmp_path):
    """Pipeline → SQLite → static JSON → the same functions the web page calls."""
    from ydn import webapi
    from ydn.cli import ROOT
    from ydn.export import export_all
    con = connect(tmp_path / "y.db")
    c = client(tmp_path)
    ids = sync_catalogs(c, con)
    sync_programs(c, con, "FW25/26", ids["FW25/26"], ["all"])
    sync_courses(c, con, "FW25/26", ids["FW25/26"], None)
    idx = export_all(con, tmp_path / "site", ROOT / "rules/buckets.yaml", "2026")
    assert idx["years"][0]["programs"][0]["title"].startswith("Data Science")
    cov = idx["coverage"]["FW25/26"]
    assert cov["rules"] > 0 and 0 < cov["checkable_pct"] <= 100
    d = tmp_path / "site" / "fw25-26"
    webapi.load_year("FW25/26", (d / "courses.json").read_text(), (d / "programs.json").read_text(),
                     (tmp_path / "site/rules.json").read_text(), (d / "chunks.json").read_text())
    out = json.loads(webapi.audit("S1", "SC MATH 2030 3.00 B\nAP HIST 1010 6.00 A\nStudent number: 219999999"))
    assert out["major_subjects"] == ["MATH"] and out["courses_read"] == 2
    assert any(o["requirement_id"].startswith("General Education") for o in out["open"])
    prev = json.loads(webapi.preview("Student number: 219999999\nSC MATH 2030 3.00 B"))
    assert "219999999" not in prev["redacted"]
    assert "MATH 2030" in webapi.advisor_summary() or "Program:" in webapi.advisor_summary()
    spec = json.loads(webapi.agent_spec())
    assert "FW25/26" in spec["system"] and len(spec["tools"]) == 4
