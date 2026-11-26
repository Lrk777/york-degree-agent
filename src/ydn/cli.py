"""Command line.

  python -m ydn.cli sync --years FW25/26 --programs all --db data/york.db
  python -m ydn.cli offerings --year FW26/27 --ay 2026 --shard 0 --shards 3 --db data/york.db
  python -m ydn.cli dq --year FW25/26 --db data/york.db
  python -m ydn.cli export --db data/york.db --out site/data --ay 2026
  python -m ydn.cli merge --into data/york.db data/part-*.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import yaml

from .export import export_all
from .kuali import KualiClient
from .offerings import OfferingsClient, sync_offerings
from .pipeline import dq_report, referenced_subjects, sync_catalogs, sync_courses, sync_programs
from .store import connect

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ydn")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--years", default="FW25/26")
    s.add_argument("--programs", default="all", help="'all' or comma-separated Kuali codes, e.g. SC-DASC")
    s.add_argument("--db", default="data/york.db")
    s.add_argument("--referenced-only", action="store_true", help="only subjects the programs mention")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--interval", type=float, default=1.0)
    o = sub.add_parser("offerings")
    o.add_argument("--year", default="FW26/27")
    o.add_argument("--ay", type=int, default=2026)
    o.add_argument("--shard", type=int, default=0)
    o.add_argument("--shards", type=int, default=1)
    o.add_argument("--db", default="data/york.db")
    o.add_argument("--interval", type=float, default=1.0)
    o.add_argument("--limit", type=int, default=None)
    d = sub.add_parser("dq")
    d.add_argument("--year", default="FW25/26")
    d.add_argument("--db", default="data/york.db")
    e = sub.add_parser("export")
    e.add_argument("--db", default="data/york.db")
    e.add_argument("--out", default="site/data")
    e.add_argument("--ay", default="2026")
    m = sub.add_parser("merge")
    m.add_argument("--into", required=True)
    m.add_argument("parts", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "merge":
        con = connect(a.into)
        for part in a.parts:
            con.execute("ATTACH DATABASE ? AS p", (part,))
            for (t,) in con.execute("SELECT name FROM p.sqlite_master WHERE type='table'").fetchall():
                con.execute(f"INSERT OR REPLACE INTO main.{t} SELECT * FROM p.{t}")
            con.commit()
            con.execute("DETACH DATABASE p")
        print(json.dumps({t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("catalogs", "courses", "programs", "subjects", "offerings", "calendar_text")}))
        return

    con = connect(a.db)
    if a.cmd == "sync":
        client = KualiClient(cache_dir=ROOT / "data/raw/kuali", min_interval=a.interval)
        ids = sync_catalogs(client, con)
        buckets = yaml.safe_load((ROOT / "rules/buckets.yaml").read_text())["buckets"]
        extra = [s for b in buckets for s in b.get("include", {}).get("subjects", [])]
        for year in a.years.split(","):
            if year not in ids:
                sys.exit(f"unknown year {year}; available: {sorted(ids)}")
            progs = sync_programs(client, con, year, ids[year], a.programs.split(","))
            subjects = referenced_subjects(progs, extra) if a.referenced_only else None
            stats = sync_courses(client, con, year, ids[year], subjects, a.limit)
            print(json.dumps({"year": year, "programs": len(progs), **stats, "http": client.stats}))
    elif a.cmd == "offerings":
        client = OfferingsClient(cache_dir=ROOT / "data/raw/w2prod", min_interval=a.interval)
        print(json.dumps(sync_offerings(con, a.year, a.ay, a.shard, a.shards, client, a.limit)))
    elif a.cmd == "export":
        idx = export_all(con, Path(a.out), ROOT / "rules/buckets.yaml", a.ay)
        print(json.dumps({"years": [{k: v for k, v in y.items() if k != "programs"} for y in idx["years"]],
                          "coverage": {y: {k: c[k] for k in ("rules", "checkable_pct")} for y, c in idx["coverage"].items()}}))
    else:
        rep = dq_report(con, a.year)
        print(json.dumps(rep, indent=2))
        sys.exit(0 if rep["hard_ok"] else 1)


if __name__ == "__main__":
    main()
