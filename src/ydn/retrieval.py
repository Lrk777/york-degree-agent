"""Version-aware search over calendar text, rule sources and course descriptions.

Pure Python (no scikit-learn) so it runs in the browser too. Every chunk carries the
calendar year it belongs to; a student who entered in FW23/24 is answered from
FW23/24 text, and other years are never mixed in. Picking the wrong year's rules is
the most common way to get a degree question wrong.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_STOP = set("""a an and are as at be by can do does for from has have how i if in is it its my of on or so that the
their them then there these they this to was what when where which who will with you your me can't cannot""".split())


def _tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]      # unigrams + bigrams


@dataclass
class Chunk:
    text: str
    year: str            # "FW25/26", or "any" for rules that are not versioned
    source: str
    section: str
    url: str = ""


class Retriever:
    """TF-IDF with sublinear term frequency and cosine similarity."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        docs = [Counter(_tokens(c.text)) for c in chunks]
        df = Counter(t for d in docs for t in d)
        n = max(1, len(docs))
        self.idf = {t: math.log((1 + n) / (1 + v)) + 1 for t, v in df.items()}
        self.vecs = []
        for d in docs:
            v = {t: (1 + math.log(c)) * self.idf[t] for t, c in d.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            self.vecs.append({t: x / norm for t, x in v.items()})

    def search(self, query: str, year: str, k: int = 4) -> list[dict]:
        q = Counter(_tokens(query))
        qv = {t: (1 + math.log(c)) * self.idf.get(t, 0) for t, c in q.items()}
        qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
        scored = []
        for i, v in enumerate(self.vecs):
            c = self.chunks[i]
            if c.year not in (year, "any"):
                continue
            s = sum(w * v.get(t, 0.0) for t, w in qv.items()) / qn
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        out = []
        for s, i in scored[:k]:
            c = self.chunks[i]
            out.append({"text": c.text[:900], "year": c.year, "source": c.source, "section": c.section,
                        "url": c.url, "score": round(s, 3)})
        return out


def chunks_from_db(con) -> list[Chunk]:
    rows = con.execute("SELECT year, source, section, text, url FROM calendar_text").fetchall()
    return [Chunk(t, y, s, sec, u or "") for y, s, sec, t, u in rows]


def chunks_from_rules(path_or_dict) -> list[Chunk]:
    if isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        import yaml
        data = yaml.safe_load(Path(path_or_dict).read_text())
    out = []
    for b in data["buckets"]:
        text = f"{b['name']}. " + " ".join(b.get("notes", []))
        inc = b.get("include", {})
        if inc.get("subjects"):
            text += " Approved subject areas: " + ", ".join(inc["subjects"]) + "."
        exc = b.get("exclude", {})
        if exc.get("crosslisted_with_faculties"):
            text += " Courses cross-listed with " + ", ".join(exc["crosslisted_with_faculties"]) + " do not count."
        if exc.get("pass_fail"):
            text += " Courses taken pass/fail do not count."
        out.append(Chunk(text, "any", b["name"], "rules", (b.get("sources") or [""])[0]))
    return out
