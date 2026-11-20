"""Terminal app for querying, updating and deleting documents in the SFPL
library-usage database (MongoDB, library_database.library).

Run it from anywhere with:   python3 mongo/main.py
If the collection does not exist yet, import_csv.py and init_db.py are run
to build it (MongoDB itself has to be running already).

Searching, statistics and export go through a READ-ONLY wrapper around the
collection. Only the update/delete commands change the database (through the
real collection), and each of them asks for a typed confirmation first.
"""

import csv
import difflib
import math
import readline
import statistics
from datetime import datetime
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

# import_csv.py and init_db.py sit next to this file (Python puts the folder
# of the script that was started on its import path)
from import_csv import import_csv_basic
from init_db import (COLLECTION_NAME, CONNECTION_STRING, DATABASE_NAME,
                     RAW_COLLECTION_NAME, init_db)

# ------------------------------------------------------ PATHS & CONSTANTS -------------------------------------------------

# Everything is worked out from where THIS file is, so it does not matter
# which folder you start the app from
APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
RESULTS_DIR = REPO_DIR / "results"

PAGE_SIZE = 20          # rows shown per page in "view results"
UNKNOWN = "(unknown)"   # what we show for a blank age range

# Set in main(). COLL is the READ-ONLY wrapper used for all searching;
# changes go through writable_collection() instead.
CLIENT = None
COLL = None
_REAL_COLLECTION = None

# Filled in at startup with the real values from the database, so the user
# never has to guess what is valid
CHOICES = {}   # field name -> list of possible values (strings)
RANGES = {}    # numeric field name -> (minimum, maximum)
TOTAL_ROWS = 0

# Fields with a list of possible values: choice name -> field path in a document
# ("patron.age_range" means the age_range field inside the patron sub-document)
CATEGORY_FIELDS = {
    "age ranges": "patron.age_range",
    "patron types": "patron.type",
    "library names": "library.name",
    "notification types": "notification.medium",
}

# Yes/no field (stored as a real true/false in the database)
BOOLEAN_FIELD = "within sf"
BOOLEAN_PATH = "within_sf"

# Numeric fields (the user picks a minimum and maximum): name -> field path
NUMERIC_FIELDS = {
    "checkouts": "checkouts",
    "renewals": "renewals",
    "year registered": "year_registered",
}

# Columns shown in "view results" and "export": (heading, field path).
# "TID" is the document's _id (kept under the same heading as the SQLite app).
RESULT_COLUMNS = [
    ("TID", "_id"),
    ("Patron type", "patron.type"),
    ("Age range", "patron.age_range"),
    ("Library", "library.name"),
    ("Notification", "notification.medium"),
    ("Checkouts", "checkouts"),
    ("Renewals", "renewals"),
    ("Within SF", "within_sf"),
    ("Year registered", "year_registered"),
]
NUMBER_HEADINGS = {"TID", "Checkouts", "Renewals", "Year registered"}

# Only the fields in RESULT_COLUMNS are fetched for "view results" and "export"
RESULT_PROJECTION = {path: 1 for _, path in RESULT_COLUMNS}

# Age ranges are text ("10 to 19 years"), which sorts wrongly ("10" < "9"),
# so that one heading is sorted by a computed position instead (see fetch_rows)
AGE_HEADING = "Age range"

# ------------------------------------------------- CURRENT USER SETTINGS --------------------------------------------------

# Filters. Within one field the chosen values are OR'd ("Teen OR Adult");
# different fields are AND'd ("Teen AND Richmond").
CATEGORY_FILTERS = {}   # field name -> {"in": [values], "not": [values]}
RANGE_FILTERS = {}      # numeric field name -> (minimum, maximum)
SORT = {"heading": "TID", "direction": "ASC"}

# ---------------------------------------------------- STARTUP FUNCTIONS ---------------------------------------------------

class ReadOnlyCollection:
    """Gives out only the READING methods of a collection, so the searching
    code cannot change the database by accident. (MongoDB has no read-only
    connection mode like SQLite's mode=ro; a truly read-only connection needs
    a database user with only the 'read' role.)"""

    def __init__(self, collection):
        self.find = collection.find
        self.find_one = collection.find_one
        self.aggregate = collection.aggregate
        self.count_documents = collection.count_documents
        self.distinct = collection.distinct


def writable_collection():
    """The real collection, which CAN be changed. Only update_rows() and
    delete_rows() call this."""
    return _REAL_COLLECTION


def rebuild_database(db):
    """Builds the collection: CSV -> library_basic (import_csv.py), then
    library_basic -> library (init_db.py)."""
    print("Database not found (or empty)! Building it from the CSV file...")
    print("(This can take a minute.)")

    if db[RAW_COLLECTION_NAME].estimated_document_count() == 0:
        import_csv_basic(RAW_COLLECTION_NAME)
    init_db()


def startup():
    """Connects to MongoDB, makes sure the collection is there (building it if
    needed), loads the lists of possible values, and returns the open client."""
    global COLL, _REAL_COLLECTION

    # A MongoClient does not connect until it is first used, so "ping" the
    # server now to find out straight away if MongoDB is not running
    client = MongoClient(CONNECTION_STRING, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        client.close()
        raise SystemExit(f"Cannot reach MongoDB at {CONNECTION_STRING}. "
                         "Start the MongoDB server (mongod) and try again.")

    db = client[DATABASE_NAME]
    collection = db[COLLECTION_NAME]

    if collection.estimated_document_count() == 0:
        rebuild_database(db)
        if collection.estimated_document_count() == 0:
            client.close()
            raise SystemExit("The database still is not usable after rebuilding. Exiting.")

    _REAL_COLLECTION = collection
    COLL = ReadOnlyCollection(collection)

    print("Database found. Connected successfully (searches are read-only).")
    load_choices()
    return client


def age_sort_key(age):
    """'0 to 9 years' < '10 to 19 years' < ... < '(unknown)'"""
    return (1000, age) if not age[:1].isdigit() else (int(age.split()[0]), age)


def load_choices():
    """Fills CHOICES, RANGES and TOTAL_ROWS from the database."""
    global TOTAL_ROWS

    for choice, path in CATEGORY_FIELDS.items():
        values = COLL.distinct(path)

        if choice == "age ranges":
            # A blank age range is stored as a MISSING field, which distinct()
            # does not report, so look for one such document ourselves.
            # ({path: None} matches documents where the field is missing.)
            if COLL.count_documents({path: None}, limit=1):
                values.append(UNKNOWN)
            CHOICES[choice] = sorted(values, key=age_sort_key)
        else:
            CHOICES[choice] = sorted(values)

    CHOICES[BOOLEAN_FIELD] = ["yes", "no"]

    # One pass over the collection for all the minimums and maximums
    group = {"_id": None}
    for field, path in NUMERIC_FIELDS.items():
        group[f"min_{path}"] = {"$min": f"${path}"}
        group[f"max_{path}"] = {"$max": f"${path}"}
    result = next(COLL.aggregate([{"$group": group}]), {})
    for field, path in NUMERIC_FIELDS.items():
        RANGES[field] = (result.get(f"min_{path}"), result.get(f"max_{path}"))

    TOTAL_ROWS = COLL.count_documents({})

# ----------------------------------------------- TAB-COMPLETION & PROMPTING ----------------------------------------------

# What Tab completes right now. ask() changes these for each question.
_options = []
_allow_not = False


def completer(text, state):
    """Called by readline repeatedly (state = 0, 1, 2, ...) and returns the
    state-th completion of `text`, or None when there are no more."""
    typed = text.lower()
    prefix = ""
    # In the value prompts a leading "not " excludes a value, so keep it
    if _allow_not and typed.startswith("not "):
        prefix = "not "
        typed = typed[4:]

    matches = [prefix + option for option in _options
               if option.lower().startswith(typed)]
    return matches[state] if state < len(matches) else None


def setup_readline():
    readline.set_completer(completer)
    # Complete the WHOLE line, not just the last word: many values contain
    # spaces ("25 to 34 years", "Visitacion Valley")
    readline.set_completer_delims("\t\n")

    # Bind the Tab key to invoke completion (macOS uses libedit, not GNU readline)
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def ask(prompt, options=None, allow_not=False):
    """Asks one question. Tab completes from `options` (default: the command
    names; pass [] for no completion). Returns the stripped answer."""
    global _options, _allow_not
    _options = list(COMMANDS) if options is None else options
    _allow_not = allow_not

    try:
        return input(prompt).strip()
    finally:
        _options, _allow_not = [], False


def match_option(text, options):
    """Finds the option the user meant: exact match (ignoring case), else a
    unique beginning. Returns the option, or None."""
    lowered = text.lower()

    for option in options:
        if option.lower() == lowered:
            return option

    starts = [option for option in options if option.lower().startswith(lowered)]
    return starts[0] if len(starts) == 1 and lowered else None


# ------------------------------------------------------ QUERY BUILDING -----------------------------------------------------

def path_for(field):
    """The document field path behind a filter field name."""
    if field in CATEGORY_FIELDS:
        return CATEGORY_FIELDS[field]

    if field == BOOLEAN_FIELD:
        return BOOLEAN_PATH

    return NUMERIC_FIELDS[field]


def stored_value(field, shown):
    """Converts what the user sees ("yes", "(unknown)") to what is stored.
    None stands for "the field is missing" (a blank age range)."""
    if field == BOOLEAN_FIELD:
        return shown == "yes"

    if shown == UNKNOWN:
        return None

    return shown


def show_value(field, value):
    """Converts a stored value to what the user sees."""
    if field == BOOLEAN_FIELD:
        return "yes" if value else "no"

    if value is None:
        return UNKNOWN

    return str(value)


def build_query():
    """Turns the current filters into a MongoDB filter document, e.g.
    {"patron.type": {"$in": ["Teen", "Adult"]}, "checkouts": {"$gte": 0, "$lte": 50}}.
    Several fields in one filter document are combined with AND. The user's
    text only ever ends up as a VALUE here, never as a field name or operator."""
    query = {}

    for field, chosen in CATEGORY_FILTERS.items():
        condition = {}
        # $in / $nin with None in the list also match / exclude a missing field
        for key, operator in (("in", "$in"), ("not", "$nin")):
            values = [stored_value(field, shown) for shown in chosen[key]]
            if values:
                condition[operator] = values
        if condition:
            query[path_for(field)] = condition

    for field, (low, high) in RANGE_FILTERS.items():
        query[path_for(field)] = {"$gte": low, "$lte": high}

    return query


def fetch_rows(query, skip=0, limit=None):
    """The documents matching `query` in the current sort order, as a cursor.
    `skip`/`limit` pick out one page (limit=None means all the rest)."""
    heading = SORT["heading"]
    direction = 1 if SORT["direction"] == "ASC" else -1

    if heading != AGE_HEADING:
        path = dict(RESULT_COLUMNS)[heading]
        # _id at the end keeps the order stable when values are equal
        keys = [(path, direction)] if path == "_id" else [(path, direction), ("_id", 1)]
        cursor = COLL.find(query, RESULT_PROJECTION, allow_disk_use=True).sort(keys).skip(skip)
        return cursor.limit(limit) if limit else cursor

    # Sorting by age range: sort by each value's position in the (already
    # correctly ordered) list of age ranges; a missing age range counts as
    # "(unknown)", which is last in that list
    position = {"$indexOfArray": [CHOICES["age ranges"],
                                  {"$ifNull": ["$patron.age_range", UNKNOWN]}]}
    pipeline = [
        {"$match": query},
        {"$project": RESULT_PROJECTION | {"_position": position}},
        {"$sort": {"_position": direction, "_id": 1}},
        {"$skip": skip},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    return COLL.aggregate(pipeline, allowDiskUse=True)


def count_matches():
    return COLL.count_documents(build_query())

# ------------------------------------------------------ PRINT HELPERS -----------------------------------------------------

def fmt(number, commas=True):
    """12345 -> '12,345'; 3.14159 -> '3.14'; None -> '-'."""
    if number is None:
        return "-"

    spec = "," if commas else ""

    if isinstance(number, float):
        return format(number, spec + ".2f")

    return format(number, spec)


def print_table(headings, rows, right_aligned=()):
    """Prints rows (lists of strings) in aligned columns."""
    widths = [len(h) for h in headings]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells):
        parts = [cell.rjust(widths[i]) if headings[i] in right_aligned
                 else cell.ljust(widths[i]) for i, cell in enumerate(cells)]
        return "  ".join(parts).rstrip()

    print(line(headings))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(line(row))


def print_in_columns(items):
    width = max(len(item) for item in items) + 2
    per_line = max(1, 78 // width)
    for i in range(0, len(items), per_line):
        print("  " + "".join(item.ljust(width) for item in items[i:i + per_line]).rstrip())


def get_path(doc, path):
    """get_path(doc, "patron.type") -> doc["patron"]["type"], or None if any
    part of the path is missing."""
    value = doc
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def format_result_row(doc):
    """Makes one document readable (also used for the CSV export)."""
    cells = []
    for heading, path in RESULT_COLUMNS:
        value = get_path(doc, path)
        if heading == "Within SF":
            cells.append("yes" if value else "no")
        elif heading == AGE_HEADING and value is None:
            cells.append(UNKNOWN)
        else:
            cells.append("" if value is None else str(value))

    return cells


# ------------------------------------------------------ USER COMMANDS -----------------------------------------------------

def describe_filters():
    lines = []
    for field, chosen in CATEGORY_FILTERS.items():
        text = " OR ".join(chosen["in"]) if chosen["in"] else "anything"

        if chosen["not"]:
            text += "  (except: " + ", ".join(chosen["not"]) + ")"

        lines.append(f"  {field}: {text}")

    for field, (low, high) in RANGE_FILTERS.items():
        commas = field != "year registered"
        lines.append(f"  {field}: {fmt(low, commas)} to {fmt(high, commas)}")

    return lines


def show_filters():
    """Prints the current filters, the sort order and how many rows match."""
    lines = describe_filters()
    if lines:
        print("Current filters (different fields are combined with AND):")
        print("\n".join(lines))
    else:
        print("No filters set (all rows are included).")

    print(f"Sorted by: {SORT['heading']} {SORT['direction']}")
    matches = count_matches()
    print(f"Matching rows: {matches:,} of {TOTAL_ROWS:,}")


def set_category_filter(field):
    options = CHOICES[field]
    existing = CATEGORY_FILTERS.get(field, {"in": [], "not": []})
    chosen = {"in": list(existing["in"]), "not": list(existing["not"])}

    print(f"\n{field}: {len(options)} possible values:")
    print_in_columns(options)
    print("Type a value (Tab completes it). Start with 'not ' to exclude a value.")
    print("Type 'clear' to remove this filter, or press Enter when done.")

    while True:
        text = ask(f"  {field} = ", options, allow_not=True)
        if text == "":
            break
        if text.lower() == "clear":
            chosen = {"in": [], "not": []}
            print("  (cleared)")
            continue

        # "not Main" means exclude Main
        key = "in"
        if text.lower().startswith("not "):
            key, text = "not", text[4:].strip()

        value = match_option(text, options)
        if value is None:
            print(f"  '{text}' is not one of the values above. Press Tab to see choices.")
            continue

        # A value cannot be both included and excluded
        other = "not" if key == "in" else "in"
        if value in chosen[other]:
            chosen[other].remove(value)
        if value not in chosen[key]:
            chosen[key].append(value)
        print(f"  ok: {'excluding' if key == 'not' else 'including'} {value}")

    if chosen["in"] or chosen["not"]:
        CATEGORY_FILTERS[field] = chosen
    else:
        CATEGORY_FILTERS.pop(field, None)


def ask_number(prompt, default):
    """Asks for a whole number. Enter = default, 'clear' = None."""
    while True:
        text = ask(prompt, []).replace(",", "")
        if text == "":
            return default
        if text.lower() == "clear":
            return None
        try:
            return int(text)
        except ValueError:
            print("  Please type a whole number.")


def set_range_filter(field):
    lowest, highest = RANGES[field]
    commas = field != "year registered"
    print(f"\n{field}: values in the database go from {fmt(lowest, commas)} "
          f"to {fmt(highest, commas)}.")
    print("Press Enter to keep the value in [brackets], or type 'clear' to remove this filter.")

    low = ask_number(f"  minimum [{lowest}]: ", lowest)
    high = ask_number(f"  maximum [{highest}]: ", highest) if low is not None else None

    if low is None or high is None or (low, high) == (lowest, highest):
        RANGE_FILTERS.pop(field, None)
        print("  (no filter on this field)")
    elif low > high:
        print("  The minimum is larger than the maximum, so nothing was changed.")
    else:
        RANGE_FILTERS[field] = (low, high)


def set_filters():
    """Choose which rows to look at."""
    field_names = list(CHOICES) + list(NUMERIC_FIELDS)
    print("Which field do you want to filter on?  (Tab shows the choices, Enter when done)")
    while True:
        text = ask("filter field> ", field_names)
        if text == "":
            break

        field = match_option(text, field_names)

        if field is None:
            print("  Not a field. Choices:")
            print_in_columns(field_names)
        elif field in NUMERIC_FIELDS:
            set_range_filter(field)
        else:
            set_category_filter(field)
    print()
    show_filters()


def clear_filters():
    CATEGORY_FILTERS.clear()
    RANGE_FILTERS.clear()
    print("Filters cleared.")
    show_filters()


def sort_results():
    """Choose the order of the rows in 'view results' and 'export'."""
    headings = [heading for heading, _ in RESULT_COLUMNS]
    text = ask(f"Sort by (now: {SORT['heading']} {SORT['direction']}; Tab for choices): ", headings)

    if text == "":
        return

    heading = match_option(text, headings)

    if heading is None:
        print("Not a column. Choices:", ", ".join(headings))
        return

    direction = (ask("asc or desc [asc]: ", ["asc", "desc"]) or "asc").lower()

    if direction not in ("asc", "desc"):
        print("Please type asc or desc. Nothing changed.")
        return

    SORT["heading"], SORT["direction"] = heading, direction.upper()
    print(f"Sorted by {heading} {direction.upper()}.")


def view_results():
    """Shows the rows that match the filters, a page at a time."""
    total = count_matches()

    if total == 0:
        print("No rows match the current filters.")
        return

    query = build_query()
    headings = [heading for heading, _ in RESULT_COLUMNS]
    print(f"{total:,} matching rows (sorted by {SORT['heading']} {SORT['direction']}).")

    offset = 0
    while offset < total:
        docs = fetch_rows(query, skip=offset, limit=PAGE_SIZE)
        print()
        print_table(headings, [format_result_row(doc) for doc in docs], NUMBER_HEADINGS)

        offset += PAGE_SIZE
        if offset >= total:
            break

        answer = ask(f"\n-- {offset:,} of {total:,} shown. Enter = next page, q = stop: ", [])

        if answer.lower().startswith("q"):
            break


def percentile(sorted_values, p):
    """Smallest value that at least p% of the values are less than or equal to."""
    index = max(0, math.ceil(p / 100 * len(sorted_values)) - 1)
    return sorted_values[index]


def summarize(values):
    """Statistics for a list of numbers: returns {name: number}."""
    values.sort()
    n = len(values)
    mean = math.fsum(values) / n
    # Sample standard deviation (n - 1); needs at least two values
    stdev = (math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))
             if n > 1 else None)
    return {
        "sum": sum(values),
        "mean": mean,
        "median": statistics.median(values),
        "stdev": stdev,
        "min": values[0],
        "max": values[-1],
        "90th percentile": percentile(values, 90),
        "99th percentile": percentile(values, 99),
    }


def print_breakdown(field, query, total):
    """Count and averages for each value of one field (MongoDB's $group is
    its GROUP BY)."""
    path = path_for(field)
    order = {"_id": 1} if field == "year registered" else {"rows": -1, "_id": 1}

    groups = COLL.aggregate([
        {"$match": query},
        {"$group": {
            "_id": f"${path}",          # a missing age range groups under None
            "rows": {"$sum": 1},
            "avg_checkouts": {"$avg": "$checkouts"},
            "avg_renewals": {"$avg": "$renewals"},
            "sum_checkouts": {"$sum": "$checkouts"},
        }},
        {"$sort": order},
    ])

    table = [[show_value(field, group["_id"]), fmt(group["rows"]),
              f"{group['rows'] / total * 100:.1f}%", fmt(group["avg_checkouts"]),
              fmt(group["avg_renewals"]), fmt(group["sum_checkouts"])]
             for group in groups]
    right = {"rows", "share", "avg checkouts", "avg renewals", "total checkouts"}
    print()
    print_table([field, "rows", "share", "avg checkouts", "avg renewals", "total checkouts"],
                table, right)


def view_stats():
    """Statistics about the rows that match the current filters."""
    total = count_matches()
    if total == 0:
        print("No rows match the current filters.")
        return

    query = build_query()
    print(f"Rows matching: {total:,} of {TOTAL_ROWS:,} ({total / TOTAL_ROWS * 100:.1f}% of the table)")
    lines = describe_filters()

    if lines:
        print("Filters:")
        print("\n".join(lines))
    else:
        print("Filters: none")

    # One list of numbers per numeric field. Only those fields are fetched,
    # and the statistics are worked out here in Python (as in the SQLite app).
    projection = {"_id": 0} | {path: 1 for path in NUMERIC_FIELDS.values()}
    values = {field: [] for field in NUMERIC_FIELDS}
    for doc in COLL.find(query, projection):
        for field, path in NUMERIC_FIELDS.items():
            values[field].append(doc[path])
    stats = {field: summarize(values[field]) for field in NUMERIC_FIELDS}

    stat_names = ["sum", "mean", "median", "stdev", "min", "max",
                  "90th percentile", "99th percentile"]
    table = []
    for name in stat_names:
        row = [name]
        for field in NUMERIC_FIELDS:
            # Adding up registration years would mean nothing
            if name == "sum" and field == "year registered":
                row.append("-")
            else:
                row.append(fmt(stats[field][name], field != "year registered"))
        table.append(row)

    print()
    print_table(["statistic"] + list(NUMERIC_FIELDS), table, set(NUMERIC_FIELDS))

    if stats["checkouts"]["sum"] > 0:
        ratio = stats["renewals"]["sum"] / stats["checkouts"]["sum"]
        print(f"\nRenewals per checkout (total renewals / total checkouts): {ratio:.3f}")
    print("Note: a few very heavy users pull the mean far above the median.")

    # Optional breakdowns
    group_fields = list(CHOICES) + ["year registered"]
    print("\nBreak these rows down by a field (Tab shows choices, Enter to finish):")
    while True:
        text = ask("breakdown by> ", group_fields)
        if text == "":
            break
        field = match_option(text, group_fields)
        if field is None:
            print("  Not a field. Choices:", ", ".join(group_fields))
        else:
            print_breakdown(field, query, total)


# ------------------------------------------------- UPDATING & DELETING ------------------------------------------------

# The update/delete commands are the ONLY code that changes the database. They
# use writable_collection(), so that everything else in this program stays
# read-only.

MIN_YEAR = 1900              # earliest "year registered" that update accepts
MAX_COUNT = 2_147_483_647    # largest checkout/renewal total that update accepts


def edit_fields():
    """The fields update can change (same names as the filters use)."""
    return list(NUMERIC_FIELDS) + list(CATEGORY_FIELDS) + [BOOLEAN_FIELD]


def refresh_after_change():
    """Reloads the row count, choices and min/max values, which a change can alter."""
    load_choices()


def scope_row():
    """Asks for a TID and shows that row. Returns a 'scope' (which rows to
    change) or None if cancelled."""
    first_doc = COLL.find_one({}, {"_id": 1}, sort=[("_id", 1)])
    last_doc = COLL.find_one({}, {"_id": 1}, sort=[("_id", -1)])

    if first_doc is None:
        print("The table is empty.")
        return None
    first, last = first_doc["_id"], last_doc["_id"]
    text = ask(f"TID of the row (from {first:,} to {last:,}; 'view results' shows "
               f"TIDs; Enter cancels): ", []).replace(",", "")

    if text == "":
        return None

    if not text.isdigit():
        print("A TID is a whole number.")
        return None

    doc = COLL.find_one({"_id": int(text)}, RESULT_PROJECTION)

    if doc is None:
        print(f"No row has TID {int(text):,} (it may have been deleted).")
        return None

    print()
    print_table([heading for heading, _ in RESULT_COLUMNS],
                [format_result_row(doc)], NUMBER_HEADINGS)
    print()

    return {"query": {"_id": int(text)}, "count": 1,
            "text": f"row {int(text):,}", "everything": False}


def scope_filtered():
    """The rows matching the current filters (shown first). Returns a scope or None."""
    total = count_matches()

    if total == 0:
        print("No rows match the current filters.")
        return None

    query = build_query()

    if query:
        print("Rows matching the current filters:")
        print("\n".join(describe_filters()))
    else:
        print("WARNING: no filters are set, so this would affect EVERY row.")

    # The same filter document the searches use, so every filter works
    return {"query": query, "count": total, "text": f"{total:,} rows",
            "everything": not query}


def confirmation_word(scope):
    """What the user must type to confirm; more rows = harder to type by accident."""

    if scope["everything"]:
        return "ALL"
    return "yes" if scope["count"] == 1 else f"{scope['count']:,}"


def confirm(scope):
    word = confirmation_word(scope)
    answer = ask(f"Type {word} to confirm (anything else cancels): ", []).replace(",", "")

    if answer.lower() != word.replace(",", "").lower():
        print("Cancelled. Nothing was changed.")
        return False

    return True


def ask_whole_number(prompt, low, high):
    """Asks until the user types a whole number from low to high (Enter = None)."""
    while True:
        text = ask(prompt, []).replace(",", "")

        if text == "":
            return None

        if text.isdigit() and low <= int(text) <= high:
            return int(text)

        print(f"  Please type a whole number from {low:,} to {high:,}.")


def choose_library_code(name):
    """Several library codes can share one name (r3, r3aaa, r3j are all
    Richmond), so ask which code to store. Enter = the first (shortest) one.
    Returns the code, or None for "no code" (a blank code is a missing field)."""
    codes = sorted(COLL.distinct("library.code", {"library.name": name}),
                   key=lambda code: (len(code), code))
    if COLL.count_documents({"library.name": name, "library.code": None}, limit=1):
        codes.insert(0, None)

    if len(codes) == 1:
        return codes[0]

    shown = [code or "(blank)" for code in codes]
    print(f"  {name} has {len(codes)} library codes: {', '.join(shown)}")

    while True:
        text = ask(f"  Which code? [{shown[0]}]: ", shown)

        if text == "":
            return codes[0]
        match = match_option(text, shown)
        if match is not None:
            return codes[shown.index(match)]
        print("  Not one of the codes above.")


def export_csv():
    """Writes the matching rows (in the current sort order) to results/."""
    total = count_matches()

    if total == 0:
        print("No rows match the current filters, so there is nothing to export.")
        return

    default = f"export_{datetime.now():%Y%m%d_%H%M%S}.csv"
    name = ask(f"File name (saved in results/) [{default}]: ", []) or default
    # Path(...).name drops any folders, so the file always stays inside results/
    name = Path(name).name

    if not name.lower().endswith(".csv"):
        name += ".csv"

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / name

    if path.exists() and ask(f"{name} already exists. Overwrite? [y/N]: ", []).lower() != "y":
        print("Export cancelled.")
        return

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([heading for heading, _ in RESULT_COLUMNS])

        for doc in fetch_rows(build_query()):
            writer.writerow(format_result_row(doc))

    print(f"Wrote {total:,} rows to {path}")


def set_or_unset(path, value):
    """The update document that stores `value` at `path`, or removes the field
    when value is None (blank values are left out of documents, not stored)."""
    return {"$unset": {path: ""}} if value is None else {"$set": {path: value}}


def ask_new_value(field):
    """Asks for the new value of one field. Returns (update document, text to
    show) or None if cancelled. The field paths in the update document are
    always our own names; the user's input is only ever a value."""
    if field in NUMERIC_FIELDS:

        low, high = RANGES[field]
        if field == "year registered":
            allowed = (MIN_YEAR, datetime.now().year)
        else:
            allowed = (0, MAX_COUNT)
        number = ask_whole_number(f"New {field} (the database now has {low} to "
                                  f"{high}; Enter cancels): ", *allowed)

        if number is None:
            return None

        text = str(number) if field == "year registered" else f"{number:,}"
        return {"$set": {NUMERIC_FIELDS[field]: number}}, text

    options = CHOICES[field]
    print_in_columns(options)
    while True:
        text = ask(f"New value for {field} (Tab completes; Enter cancels): ", options)

        if text == "":
            return None

        shown = match_option(text, options)
        if shown is not None:
            break

        print("  Not one of the values above.")

    if field == BOOLEAN_FIELD:
        return {"$set": {BOOLEAN_PATH: shown == "yes"}}, shown
    if field == "age ranges":
        return set_or_unset("patron.age_range", stored_value(field, shown)), shown

    # The other fields are a code + its definition stored side by side in a
    # sub-document, so look up the code and change BOTH together
    if field == "patron types":
        code = COLL.find_one({"patron.type": shown}, {"patron.code": 1})["patron"]["code"]
        return {"$set": {"patron.code": code, "patron.type": shown}}, shown
    if field == "library names":
        code = choose_library_code(shown)
        update = set_or_unset("library.code", code)
        update.setdefault("$set", {})["library.name"] = shown
        return update, f"{shown} (code {code or '(blank)'})"
    code = COLL.find_one({"notification.medium": shown},
                         {"notification.code": 1})["notification"]["code"]
    return {"$set": {"notification.code": code, "notification.medium": shown}}, shown


def update_rows(scope):
    """Changes ONE field of the chosen rows, after a typed confirmation."""
    if scope is None:
        return

    fields = edit_fields()
    while True:
        text = ask("Which field do you want to change? (Tab shows the choices, "
                   "Enter cancels): ", fields)
        if text == "":
            print("Cancelled. Nothing was changed.")
            return
        field = match_option(text, fields)
        if field is not None:
            break
        print("  Not a field. Choices:")
        print_in_columns(fields)

    new = ask_new_value(field)
    if new is None:
        print("Cancelled. Nothing was changed.")
        return
    update, shown = new

    print(f"\nThis will set {field} to '{shown}' for {scope['text']}.")
    if not confirm(scope):
        return

    # Each document is changed completely or not at all, but (unlike the SQLite
    # app's transaction) update_many is NOT all-or-nothing across documents: if
    # the server stops halfway, the documents already changed stay changed.
    # matched_count = documents found by the filter (what SQLite's rowcount gives)
    changed = writable_collection().update_many(scope["query"], update).matched_count

    print(f"Updated {changed:,} row(s).")
    refresh_after_change()


def delete_rows(scope):
    """Permanently deletes the chosen rows, after a typed confirmation."""
    if scope is None:
        return

    print(f"This will permanently DELETE {scope['text']} from {DATABASE_NAME}.{COLLECTION_NAME}.")
    print("(To get the original data back, close the app and run init_db.py: it "
          f"rebuilds this collection from {RAW_COLLECTION_NAME}.)")

    if not confirm(scope):
        return

    deleted = writable_collection().delete_many(scope["query"]).deleted_count

    print(f"Deleted {deleted:,} row(s).")
    refresh_after_change()


def update_row():
    update_rows(scope_row())


def update_filtered():
    update_rows(scope_filtered())


def delete_row():
    delete_rows(scope_row())


def delete_filtered():
    delete_rows(scope_filtered())


def show_help():
    print("Commands (type one, or just its beginning; Tab completes):")
    width = max(len(name) for name in COMMANDS)

    for name, (_, description) in COMMANDS.items():
        print(f"  {name.ljust(width)}  {description}")
    print("\nTip: filters are kept until you clear them, so set them once and then "
          "use view results, view stats, export, update filtered or delete filtered.")
    print("Searching never changes the database; only the update and delete "
          "commands do, and each asks you to confirm first.")


def quit_app():
    print("Goodbye!")
    raise SystemExit(0)


# Every command the user can type: name -> (function, description)
COMMANDS = {
    "set filters": (set_filters, "choose which rows to look at (age, patron type, library, ...)"),
    "show filters": (show_filters, "show the current filters and how many rows match"),
    "clear filters": (clear_filters, "remove all filters"),
    "sort": (sort_results, "choose the order of the rows"),
    "view results": (view_results, "show the matching rows, a page at a time"),
    "view stats": (view_stats, "statistics (and breakdowns) for the matching rows"),
    "export": (export_csv, "save the matching rows to a CSV file in results/"),
    "update row": (update_row, "change one field of a single row (found by its TID)"),
    "update filtered": (update_filtered, "change one field of ALL rows matching the filters"),
    "delete row": (delete_row, "delete a single row (found by its TID)"),
    "delete filtered": (delete_filtered, "delete ALL rows matching the filters"),
    "help": (show_help, "show this list"),
    "exit": (quit_app, "leave the app"),
    "quit": (quit_app, "leave the app"),
}


def resolve_command(text):
    """'view results' -> 'view results'; 'view r' -> 'view results'; else None."""
    text = " ".join(text.lower().split())
    if text in COMMANDS:
        return text
    starts = [name for name in COMMANDS if name.startswith(text)]
    return starts[0] if len(starts) == 1 else None

# ------------------------------------------------------ MAIN LOOP ---------------------------------------------------------

def main():
    global CLIENT
    setup_readline()
    CLIENT = startup()

    try:
        print(f"{TOTAL_ROWS:,} rows loaded. Type 'help' for the commands, and press [Tab] to autocomplete.")
        while True:
            try:
                line = ask("> ")
            except EOFError:            # Ctrl-D
                print()
                break
            except KeyboardInterrupt:   # Ctrl-C
                print("\n(Type 'exit' to leave.)")
                continue
            if line == "":
                continue

            command = resolve_command(line)
            if command is None:
                guesses = difflib.get_close_matches(line.lower(), COMMANDS, n=3, cutoff=0.5)
                print(f"Unknown command: '{line}'." +
                      (f" Did you mean: {', '.join(guesses)}?" if guesses else " Type 'help'."))
                continue

            try:
                COMMANDS[command][0]()
            except (KeyboardInterrupt, EOFError):
                print("\n(cancelled)")
            except PyMongoError as error:
                print(f"Database error: {error}")
    finally:
        CLIENT.close()   # also runs when 'exit' raises SystemExit


if __name__ == "__main__":
    main()