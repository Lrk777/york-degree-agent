"""SQLite store for the cleaned data. Every load is an upsert keyed on
(catalog, id), so running the pipeline twice never creates duplicates."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalogs (
  year TEXT PRIMARY KEY, catalog_id TEXT NOT NULL, start_date TEXT, end_date TEXT);
CREATE TABLE IF NOT EXISTS courses (
  year TEXT NOT NULL, course_full TEXT NOT NULL, course_key TEXT NOT NULL,
  faculty TEXT, subject TEXT NOT NULL, number TEXT NOT NULL, level INTEGER NOT NULL,
  credits REAL, title TEXT, description TEXT, prereq_raw TEXT,
  prereq_json TEXT, exclusions_json TEXT, crosslisted_json TEXT, gen_ed_flag INTEGER,
  kuali_pid TEXT, kuali_id TEXT,
  PRIMARY KEY (year, course_full));
CREATE INDEX IF NOT EXISTS courses_key ON courses(year, course_key);
CREATE TABLE IF NOT EXISTS subjects (
  year TEXT NOT NULL, subject TEXT NOT NULL, description TEXT, PRIMARY KEY (year, subject));
CREATE TABLE IF NOT EXISTS offerings (
  ay TEXT NOT NULL, course_full TEXT NOT NULL, terms TEXT NOT NULL, sections INTEGER, fetched TEXT,
  PRIMARY KEY (ay, course_full));
CREATE TABLE IF NOT EXISTS programs (
  year TEXT NOT NULL, pid TEXT NOT NULL, code TEXT, title TEXT, faculty TEXT,
  credential TEXT, requirements_html TEXT, rule_tree_json TEXT,
  PRIMARY KEY (year, pid));
CREATE TABLE IF NOT EXISTS calendar_text (
  year TEXT NOT NULL, source TEXT NOT NULL, section TEXT NOT NULL, chunk_no INTEGER NOT NULL,
  text TEXT NOT NULL, url TEXT,
  PRIMARY KEY (year, source, section, chunk_no));
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, table: str, row: dict):
    cols = ", ".join(row)
    qs = ", ".join("?" for _ in row)
    con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})",
                [json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()])
