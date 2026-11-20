"""Collects the MongoDB performance metrics (the same ones measured for SQLite).

Run it with:   python3 mongo/metrics.py            (storage, query time, app speed)
               python3 mongo/metrics.py --build    (also times a full rebuild first)

--build DROPS and re-creates both collections, so any changes made with the
app's update/delete commands are lost.
"""

import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient

from import_csv import import_csv_basic
from init_db import (COLLECTION_NAME, CONNECTION_STRING, DATABASE_NAME,
                     RAW_COLLECTION_NAME, init_db)

APP_PATH = Path(__file__).resolve().parent / "main.py"
RUNS = 3

# "Average checkouts of Richmond patrons aged 25 to 34"
QUERY_PIPELINE = [
    {"$match": {"library.name": "Richmond", "patron.age_range": "25 to 34 years"}},
    {"$group": {"_id": None, "rows": {"$sum": 1}, "avg_checkouts": {"$avg": "$checkouts"}}},
]


def timed(function):
    """Runs function() and returns (seconds it took, its result)."""
    start = time.perf_counter()
    result = function()
    return time.perf_counter() - start, result


def megabytes(size_in_bytes):
    return f"{size_in_bytes / 1_000_000:.1f} MB"


def find_all(obj, key):
    """Every value stored under `key` anywhere inside nested dicts/lists.
    (The shape of explain() output differs between MongoDB versions.)"""
    found = []
    if isinstance(obj, dict):
        for k, value in obj.items():
            if k == key:
                found.append(value)
            found.extend(find_all(value, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_all(item, key))
    return found


def measure_build():
    print("### Building the database")
    csv_seconds, _ = timed(lambda: import_csv_basic(RAW_COLLECTION_NAME))
    init_seconds, _ = timed(init_db)
    print(f"CSV -> {RAW_COLLECTION_NAME} (includes verify_import): {csv_seconds:.1f} s")
    print(f"{RAW_COLLECTION_NAME} -> {COLLECTION_NAME}: {init_seconds:.1f} s")
    print(f"Total: {csv_seconds + init_seconds:.1f} s\n")


def measure_storage(client):
    print("### Storage")
    # MongoDB writes data files at "checkpoints" (about once a minute); fsync
    # forces one now so the on-disk sizes below are up to date
    client.admin.command("fsync")
    db = client[DATABASE_NAME]

    print("collection      documents  BSON size  on disk (compressed)  indexes  total on disk")
    for name in (RAW_COLLECTION_NAME, COLLECTION_NAME):
        stats = next(db[name].aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
        # Pad out the numbers to look prettier
        print(f"{name:<14}  {stats['count']:>9,}  {megabytes(stats['size']):>9}  "
              f"{megabytes(stats['storageSize']):>20}  {megabytes(stats['totalIndexSize']):>7}  "
              f"{megabytes(stats['totalSize']):>13}")
    print()


def measure_query(client):
    print("### Query time")
    collection = client[DATABASE_NAME][COLLECTION_NAME]

    for run in range(1, RUNS + 1):
        # Pass the list as a function to timed
        seconds, result = timed(lambda: list(collection.aggregate(QUERY_PIPELINE)))
        print(f"run {run}: {seconds:.3f} s  (measured in Python, includes the network round trip)")
    print(f"result: {result}")

    # Ask the server how it ran the query and how long it took on its side
    plan = client[DATABASE_NAME].command(
        "explain",
        {"aggregate": COLLECTION_NAME, "pipeline": QUERY_PIPELINE, "cursor": {}},
        verbosity="executionStats",
    )
    print(f"server-side time: {find_all(plan, 'executionTimeMillis')} ms")
    print(f"documents examined: {find_all(plan, 'totalDocsExamined')}")
    print(f"plan stages: {find_all(plan, 'stage')}  (COLLSCAN = whole collection read, IXSCAN = index used)\n")


def measure_app():
    """Starts the real app, feeds it typed commands, and times the whole process."""
    print("### App speed (whole process, including start-up)")
    sessions = {
        "start-up only": "exit\n",
        "view stats": "view stats\n\nexit\n",              # blank line skips the breakdown prompt
        "view results (first page)": "view results\nq\nexit\n",
    }
    for label, typed in sessions.items():
        times = []
        for _ in range(RUNS):
            seconds, process = timed(lambda: subprocess.run(
                [sys.executable, str(APP_PATH)], input=typed, text=True, capture_output=True))
            if process.returncode != 0:
                raise RuntimeError(f"app failed during {label!r}:\n{process.stderr}")
            times.append(f"{seconds:.2f} s")
        print(f"{label}: {', '.join(times)}")
    print()


def main():
    if "--build" in sys.argv[1:]:
        measure_build()

    client = MongoClient(CONNECTION_STRING, serverSelectionTimeoutMS=5000)
    try:
        measure_storage(client)
        measure_query(client)
    finally:
        client.close()
    measure_app()


if __name__ == "__main__":
    main()