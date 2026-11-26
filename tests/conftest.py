import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ydn.catalog import BucketLibrary, Catalog  # noqa: E402
from ydn.reqparse import parse_requirements  # noqa: E402

FIX = ROOT / "tests/fixtures"

# Synthetic catalog rows shaped like Kuali course details. MATH 2030's description is
# the real 2025-26 text; the others are short invented descriptions.
COURSES = [
    ("SC/MATH1300 Cr=3.00 EN", "Applied Calculus I", ""),
    ("SC/MATH1310 Cr=3.00 EN", "Applied Calculus II", "Prerequisite: SC/MATH 1300 3.00."),
    ("SC/MATH2030 Cr=3.00 EN", "Elementary Probability",
     "Introduction to the theory of probability. Prerequisite: One of SC/MATH 1014 3.00, SC/MATH 1310 3.00, "
     "SC/ISCI 1402 3.00, SC/ISCI 1410 6.00."),
    ("SC/MATH2022 Cr=3.00 EN", "Linear Algebra II", "Prerequisite: SC/MATH 1021 3.00 or SC/MATH 1025 3.00."),
    ("SC/MATH2041 Cr=3.00 EN", "Symbolic Computation Laboratory I", ""),
    ("SC/MATH3241 Cr=3.00 EN", "Numerical Methods I", "Prerequisites: SC/MATH 2022 3.00 and LE/EECS 1012 3.00."),
    ("SC/MATH3090 Cr=3.00 EN", "Computational Mathematics", "Prerequisite: SC/MATH 2022 3.00 with a minimum grade of C."),
    ("LE/EECS2011 Cr=3.00 EN", "Fundamentals of Data Structures", "Prerequisite: LE/EECS 1012 3.00."),
    ("LE/EECS1012 Cr=3.00 EN", "Net-centric Introduction to Computing", ""),
    ("SC/BIOL1000 Cr=3.00 EN", "Biology I", ""),
    ("SC/BIOL1001 Cr=3.00 EN", "Biology II", "Prerequisite: SC/BIOL 1000 3.00."),
    ("SC/CHEM1000 Cr=3.00 EN", "Chemical Structure", ""),
    ("SC/PHYS1411 Cr=3.00 EN", "Physics Fundamentals 1", ""),
    ("HH/PSYC1010 Cr=6.00 EN", "Introduction to Psychology", ""),
    ("SC/PHYS2020 Cr=3.00 EN", "Electricity and Magnetism", "Prerequisite: SC/PHYS 1411 3.00."),
    ("AP/HIST1010 Cr=6.00 EN", "Making History", ""),
    ("AP/HIST3500 Cr=3.00 EN", "History of Canada", "Prerequisite: 24 credits completed."),
    ("AP/PHIL1100 Cr=3.00 EN", "The Meaning of Life", ""),
    ("AP/SOSC1009 Cr=6.00 EN", "Science and Society",
     "Explores science in society. Cross-listed to: SC/STS 1009 6.00."),
    ("AP/MODR1650 Cr=3.00 EN", "Statistical Reasoning", ""),
    ("AP/ECON1000 Cr=3.00 EN", "Introduction to Microeconomics", ""),
    ("AP/ECON1010 Cr=3.00 EN", "Introduction to Macroeconomics", "Prerequisite: AP/ECON 1000 3.00."),
    ("AP/GWST2510 Cr=3.00 EN", "Gender and Science", "Course credit exclusion: SC/NATS 1700 6.00."),
    ("AP/EN1001 Cr=3.00 EN", "Reading Literature", ""),
    ("AP/SOCI1010 Cr=6.00 EN", "Introduction to Sociology", "Course credit exclusion: AP/SOCI 1000 6.00."),
    ("SB/MGMT1000 Cr=3.00 EN", "Introduction to Management", ""),
    ("FA/MUSI1530 Cr=6.00 EN", "Rock and Popular Music", ""),
]


@pytest.fixture
def catalog():
    recs = [{"id": i, "title": t, "description": d} for i, t, d in COURSES]
    return Catalog.from_records("FW25/26", recs)


@pytest.fixture
def buckets():
    return BucketLibrary.load(ROOT / "rules/buckets.yaml")


@pytest.fixture
def tree():
    return parse_requirements((FIX / "program_ds_bsc.html").read_text())
