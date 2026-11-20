from itertools import batched
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

# Copy and paste
CONNECTION_STRING = "mongodb://localhost:27017/"
DATABASE_NAME = "library_database"
COLLECTION_NAME = "library"
RAW_COLLECTION_NAME = "library_basic"
BATCH_SIZE = 10_000

# Doc template that maps each param to the raw param they take from
DOC_TEMPLATE = {
    "_id": "_id",
    "patron": {"code": "patron_type_code", "type": "patron_type_definition", "age_range": "age_range"},
    "library": {"code": "home_library_code", "name": "home_library_definition"}, # name trimmed
    "notification": {"code": "notification_medium_code", "medium": "notification_code_definition"},
    "checkouts": "checkout_total",
    "renewals": "renewal_total",
    "within_sf": "within_san_francisco_county", # real booleans, not "True"
    "year_registered": "year_patron_registered",
    "circulation_active": {"month": "circulation_active_month", "year": "circulation_active_year"}, # leave out when blank
    "email_provided": "provided_email_address"
}
# Raw columns that are allowed to be missing (same ones import_csv.py treats as optional)
OPTIONAL_RAW_COLUMNS = {
    "age_range",
    "home_library_code",
    "circulation_active_month",
    "circulation_active_year",
}

# Fills one level of the template with values from the raw doc.
# Missing optional values are left out; an empty sub-document is left out too.
def fill_template(template: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    filled: dict[str, Any] = {}

    for key, source in template.items():
        if isinstance(source, dict):
            sub_doc = fill_template(source, raw) # recursive call to sub dict
            if sub_doc:  # e.g. circulation_active with neither month nor year -> omit
                filled[key] = sub_doc
            continue

        value = raw.get(source)
        if isinstance(value, str):
            value = value.strip()  # trims library name (and any other stray whitespace)
        if value is None or value == "":
            if source not in OPTIONAL_RAW_COLUMNS:
                raise ValueError(f"_id {raw.get('_id')!r}: required raw column {source!r} is missing")
            continue
        filled[key] = value
    return filled


# Builds one clean (nested) document from one raw (flat) document
# (Builds a new dict every time -> DOC_TEMPLATE itself is never modified)
def construct_clean_doc(raw: dict[str, Any]) -> dict[str, Any]:
    return fill_template(DOC_TEMPLATE, raw)

# Reads every doc from from_c, restructures it, and bulk inserts into to_c
# Returns the number of docs inserted
def insert_docs(to_c: Collection, from_c: Collection) -> int:
    inserted = 0
    for batch in batched(from_c.find(), BATCH_SIZE):
        docs = [construct_clean_doc(raw) for raw in batch]
        try:
            to_c.insert_many(docs, ordered=False)
        except BulkWriteError as exc:
            first_errors = exc.details.get("writeErrors", [])[:3]
            raise RuntimeError(
                f"insert_many failed for batch starting at _id {docs[0]['_id']!r}: {first_errors}"
            ) from exc
        inserted += len(docs)
    return inserted

# Init-import database function
def init_db() -> None:
    # Connect to mongodb using client
    client = MongoClient(CONNECTION_STRING)
    try:
        db = client[DATABASE_NAME]
        raw_collection = db[RAW_COLLECTION_NAME]
        raw_count = raw_collection.count_documents({})
        if raw_count == 0:
            raise RuntimeError(f"{RAW_COLLECTION_NAME!r} is empty or missing; run import_csv_basic() first")

        # Clear + create the clean collection
        db.drop_collection(COLLECTION_NAME)
        collection = db.create_collection(COLLECTION_NAME)

        inserted = insert_docs(to_c=collection, from_c=raw_collection)

        # Every raw doc should have produced exactly one clean doc
        clean_count = collection.count_documents({})
        if not (inserted == clean_count == raw_count):
            raise RuntimeError(
                f"doc counts differ (raw={raw_count}, inserted={inserted}, clean={clean_count})"
            )
        print(f"Imported {clean_count} docs into {DATABASE_NAME}.{COLLECTION_NAME}")
    # Always close the client connection when finished
    finally:
        client.close()


if __name__ == "__main__":
    init_db()