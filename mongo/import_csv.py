import csv
import re
from itertools import batched
from pathlib import Path
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

BATCH_SIZE = 10_000

# Resolve the CSV path relative to this file's location (repo root / src / *.csv)
REPO_DIR = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_DIR / "src" / "SFPL_DataSF_library-usage_Jan_2023.csv"

CONNECTION_STRING = "mongodb://localhost:27017/"
DATABASE_NAME = "library_database"

# Validator for the parameters of docs in MongoDB
# (Ensures that each parameter is the right type)
VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Document Validation",
        # All required but circulation_active_month and circulation_active_year
        # PLUS: age_range, home_library_code, home_library_definition (first two are sometimes missing) 
        "required": [
            "patron_type_code",
            "patron_type_definition",
            "checkout_total",
            "renewal_total",
            # "age_range",
            # "home_library_code",
            # "home_library_definition",
            "notification_medium_code",
            "notification_code_definition",
            "provided_email_address",
            "within_san_francisco_county",
            "year_patron_registered",
        ],
        "properties": {
            "patron_type_code": {"bsonType": "int", "minimum": 0},
            "patron_type_definition": {"bsonType": "string"},
            "checkout_total": {"bsonType": "int", "minimum": 0},
            "renewal_total": {"bsonType": "int", "minimum": 0},
            "age_range": {"bsonType": "string"},
            "home_library_code": {"bsonType": "string"},   # "r3", "x", "b2", etc.
            "home_library_definition": {"bsonType": "string"},
            "circulation_active_month": {"bsonType": "string"},
            "circulation_active_year": {"bsonType": "int"},
            "notification_medium_code": {"bsonType": "string", "minLength": 1, "maxLength": 1},
            "notification_code_definition": {"bsonType": "string"},
            "provided_email_address": {"bsonType": "bool"},
            "within_san_francisco_county": {"bsonType": "bool"},
            "year_patron_registered": {"bsonType": "int"},
        },
    }
}

# clean column name: (converter, is-required)
# Insertion order is document order
COLUMN_SPECS: dict[str, tuple[Any, bool]] = {
    "patron_type_code": (int, True),
    "patron_type_definition": (str, True),
    "checkout_total": (int, True),
    "renewal_total": (int, True),
    "age_range": (str, False), # Age range is left empty in some rows in the CSV file
    "home_library_code": (str, False),
    "home_library_definition": (str, True),
    "circulation_active_month": (str, False),
    "circulation_active_year": (int, False),
    "notification_medium_code": (str, True),
    "notification_code_definition": (str, True),
    "provided_email_address": (bool, True),
    "within_san_francisco_county": (bool, True),
    "year_patron_registered": (int, True),
}

COLUMNS = list(COLUMN_SPECS)

# Cleans a header cell: drops the BOM and quote characters, keeps the last
# non-empty line, lowercases it and turns runs of whitespace into one underscore.
def clean_name(name: str) -> str:
    text = name.replace("\uFEFF", "") # Remove UTF-8 BOM
    lines = [line.strip() for line in text.splitlines() if line.strip()] # Split based on newlines
    if not lines: #                                                         v use repr() for debug statements
        raise ValueError(f"A header cell in the CSV is empty (raw value: {name!r})")
    last = lines[-1].strip().strip("\"'").strip() # Strip the last line of outer spaces, quotes, and then spaces
    return re.sub(r"\s+", "_", last.lower()) # Replaces " " with "_" and sets to lowercase

# Converts a string into a boolean, and raises an error if it's not either
# "true"/"True" or "false"/"False".
def parse_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"expected a boolean (True/False), got {raw!r}")


# Maps every raw header cell to its clean column name, after checking the header.
def check_header(raw_header: list[str]) -> dict[str, str]:
    if not raw_header:
        raise ValueError(f"No header row found in {CSV_PATH}")

    rename = {raw: clean_name(raw) for raw in raw_header}
    got = [rename[raw] for raw in raw_header]

    # For debug
    missing = [column for column in COLUMNS if column not in got]
    unexpected = [column for column in got if column not in COLUMNS]
    duplicated = sorted({column for column in got if got.count(column) > 1})

    if missing or unexpected or duplicated:
        raise ValueError(
            "CSV header does not match 'columns' list:\n"
            f"\theader in file : {got}\n"
            f"\texpected       : {COLUMNS}\n"
            f"\tmissing        : {missing}\n"
            f"\tunexpected     : {unexpected}\n"
            f"\tduplicated     : {duplicated}"
        )
    return rename


# Builds one document from one CSV record, using COLUMN_SPECS for types.
def construct_doc(row: dict[str, str], _id: int, line_no: int) -> dict[str, Any]:
    doc: dict[str, Any] = {"_id": _id}
    for column, (converter, required) in COLUMN_SPECS.items():
        raw = (row.get(column) or "").strip()

        if not raw:
            if required:
                raise ValueError(f"line {line_no}, column {column!r}: value is empty but required")
            continue  # optional + blank -> omit the field, the validator allows that

        try: # Parse the value; if not bool, it gets the type (e.g., int("123"))
            doc[column] = parse_bool(raw) if converter is bool else converter(raw)
        except ValueError as exc:
            raise ValueError(
                f"line {line_no}, column {column!r}: cannot convert {raw!r} ({exc})"
            ) from exc
    return doc

def verify_import(collection: Collection) -> None:
    # Verify collection statistics
    total_count = collection.count_documents({})

    # Checkout and renewal statistics
    checkout_pipeline = [
        {
            "$group": {
                "_id": None,
                "total_checkouts": {"$sum": "$checkout_total"},
                "median_checkouts": {"$median": {"input": "$checkout_total", "method": "approximate"}},
                "total_renewals": {"$sum": "$renewal_total"},
                "median_renewals": {"$median": {"input": "$renewal_total", "method": "approximate"}},
            }
        }
    ]
    cr_stats = list(collection.aggregate(checkout_pipeline))[0] # cr = checkout-renewal

    # Age range statistics (count for each age range)
    age_pipeline = [{"$group": {"_id": "$age_range", "age_range_count": {"$sum": 1}}}]
    ar_stats = {doc["_id"]: doc["age_range_count"] for doc in collection.aggregate(age_pipeline)}

    # STATISTICS HARD-CODED FROM results/README.md -- CHANGE IF NECESSARY
    if total_count != 437_115 or cr_stats["total_checkouts"] != 70_412_694 or cr_stats["median_checkouts"] != 7:
        raise ValueError(
            "Checkout statistics are incorrect "
            f"(count={total_count}, checkouts={cr_stats['total_checkouts']}, "
            f"median={cr_stats['median_checkouts']})"
        )
    if cr_stats["total_renewals"] != 33_101_214 or cr_stats["median_renewals"] != 3:
        raise ValueError(
            "Renewal statistics are incorrect "
            f"(renewals={cr_stats['total_renewals']}, median={cr_stats['median_renewals']})"
        )

    known_ar_stats = {
        "0 to 9 years": 34_642,
        "10 to 19 years": 67_320,
        "20 to 24 years": 24_484,
        "25 to 34 years": 92_790,
        "35 to 44 years": 80_477,
        "45 to 54 years": 48_752,
        "55 to 59 years": 19_281,
        "60 to 64 years": 17_664,
        "65 to 74 years": 31_906,
        "75 years and over": 18_872,
        None: 927, # <- for empty age ranges
    }

    problems = []
    for age_range, expected in known_ar_stats.items():
        actual = ar_stats.get(age_range)
        if actual != expected:
            problems.append(f"  {age_range}: expected {expected}, got {actual}")
    unexpected_ranges = sorted(set(ar_stats) - set(known_ar_stats))
    if unexpected_ranges:
        problems.append(f"\tunexpected age ranges in data: {unexpected_ranges}")
    if problems:
        raise ValueError("Age range counts are incorrect:\n" + "\n".join(problems))


# Bulk inserts rows into the database without structural changes to the data
def import_csv_basic(collection_str: str = "library_basic") -> None:
    if not CSV_PATH.is_file():
        raise FileNotFoundError(f"CSV not found at {CSV_PATH}")

    client = MongoClient(CONNECTION_STRING)
    try:
        db = client[DATABASE_NAME]
        db.drop_collection(collection_str)  # Clear collection first
        collection = db.create_collection(
            collection_str,
            validator=VALIDATOR,
            validationLevel="strict",       # "strict" (default) or "moderate"
            validationAction="error",
        )

        offset = 0
        # newline="" and utf-8-sig; csv module needs the first, BOM the second
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            rename = check_header([name for name in (reader.fieldnames or []) if name is not None])

            for batch in batched(reader, BATCH_SIZE):
                docs = [
                    construct_doc(
                        {rename[key]: value for key, value in row.items() if key is not None},
                        _id=offset + index,
                        line_no=offset + index + 2,  # +1 header row, +1 for 1-based lines
                    )
                    for index, row in enumerate(batch)
                ]
                try:
                    collection.insert_many(docs, ordered=False)
                except BulkWriteError as exc:
                    first_errors = exc.details.get("writeErrors", [])[:3]
                    raise RuntimeError(
                        f"insert_many failed for CSV lines {offset + 2}-{offset + len(docs) + 1}: "
                        f"{first_errors}"
                    ) from exc
                offset += len(docs)  # Update offset for next batch

        verify_import(collection)
    finally:
        client.close()  # always close

if __name__ == "__main__":
    import_csv_basic("library_basic")