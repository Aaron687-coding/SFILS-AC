"""Terminal app for querying, updating and deleting rows in the SFPL
library-usage database (SQLite, library.db).

Run it from anywhere with:   python3 mongo/main.py
If library.db does not exist yet, the Gleam project is run to build it.

Searching, statistics and export use a READ-ONLY connection. Only the
update/delete commands change the database (through a separate connection),
and each of them asks for a typed confirmation first.
"""

import csv
import difflib
import math
import readline
import shutil
import pymongo
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------ PATHS & CONSTANTS -------------------------------------------------

# Everything is worked out from where THIS file is, so it does not matter
# which folder you start the app from
APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
DB_PATH = REPO_DIR / "library.db"
RESULTS_DIR = REPO_DIR / "results"

PAGE_SIZE = 20          # rows shown per page in "view results"
UNKNOWN = "(unknown)"   # what we show for a blank age range

# The open READ-ONLY connection used for all searching, set in main().
# (Changes go through open_writable() instead.)
CONN = None

# Filled in at startup with the real values from the database, so the user
# never has to guess what is valid
CHOICES = {}   # field name -> list of possible values (strings)
RANGES = {}    # numeric field name -> (minimum, maximum)
TOTAL_ROWS = 0

# Fields with a list of possible values: choice name: [column name, table name]
SQLITE_COMMANDS = {
    "age ranges": ["age_range", "PatronType"],
    "patron types": ["patron_type_definition", "PatronType"],
    "library names": ["home_library_name", "HomeLibType"],
    "notification types": ["notification_medium_definition", "NotificationType"],
}

# Table name -> the short name (alias) it gets in the big query below
TABLE_ALIASES = {
    "PatronType": "p",
    "HomeLibType": "h",
    "NotificationType": "n",
}

# Yes/no field (stored as 1/0 in the database)
BOOLEAN_FIELD = "within sf"

# Numeric fields (the user picks a minimum and maximum): name -> column
NUMERIC_FIELDS = {
    "checkouts": "u.checkout_total",
    "renewals": "u.renewal_total",
    "year registered": "u.year_registered",
}

# Every query reads from the transaction table joined with the three lookup
# tables, so definitions (like "Juvenile" or "Richmond") are available to
# filter on and to display. Only the column names and table names we wrote
# here are ever put into SQL text; anything the user types goes through `?`.
FROM_SQL = (
    "FROM UserTransaction u "
    "JOIN PatronType p ON p.patron_type_code = u.patron_type_code "
    "AND p.age_range = u.age_range "
    "JOIN HomeLibType h ON h.home_library_code = u.home_library_code "
    "JOIN NotificationType n "
    "ON n.notification_medium_code = u.notification_medium_code "
)


# Columns shown in "view results" and "export": (heading, SQL column)
RESULT_COLUMNS = [
    ("TID", "u.TID"),
    ("Patron type", "p.patron_type_definition"),
    ("Age range", "u.age_range"),
    ("Library", "h.home_library_name"),
    ("Notification", "n.notification_medium_definition"),
    ("Checkouts", "u.checkout_total"),
    ("Renewals", "u.renewal_total"),
    ("Within SF", "u.within_sf"),
    ("Year registered", "u.year_registered"),
]
NUMBER_HEADINGS = {"TID", "Checkouts", "Renewals", "Year registered"}

# Age ranges are text ("10 to 19 years"), which sorts wrongly ("10" < "9"),
# so sort them by their first number instead (blank ones last)
SORT_EXPRESSIONS = {
    "Age range": "CASE WHEN u.age_range = '' THEN 1000 "
                 "ELSE CAST(u.age_range AS INTEGER) END",
}

# ------------------------------------------------- CURRENT USER SETTINGS --------------------------------------------------

# Filters. Within one field the chosen values are OR'd ("Teen OR Adult");
# different fields are AND'd ("Teen AND Richmond").
CATEGORY_FILTERS = {}   # field name -> {"in": [values], "not": [values]}
RANGE_FILTERS = {}      # numeric field name -> (minimum, maximum)
SORT = {"heading": "TID", "direction": "ASC"}

# ---------------------------------------------------- STARTUP FUNCTIONS ---------------------------------------------------

def open_database():
    """Opens library.db READ-ONLY: SQLite itself refuses any change, no matter
    what the rest of this program does. (mode=ro also never creates a file.)"""
    return sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)


def database_is_ready(conn):
    """True if the tables exist and hold data."""
    try:
        return conn.execute("SELECT count(*) FROM UserTransaction").fetchone()[0] > 0
    except sqlite3.DatabaseError:
        # e.g. "no such table", or the file is not a database at all
        return False


def rebuild_database():
    """Runs the Gleam project (src/run_me.gleam), which creates library.db."""
    if shutil.which("gleam") is None:
        raise SystemExit("Cannot rebuild the database: the 'gleam' command was not "
                         "found. Install Gleam (and Erlang), or run 'gleam run' yourself.")

    print("Database not found (or empty)! Running the Gleam program to build it...")
    print("(This takes about 15 to 30 seconds.)")

    # `gleam run` has to be started in the folder that contains gleam.toml
    # (the repo folder), not given a path to a .gleam file
    result = subprocess.run(
        ["gleam", "run"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit("The Gleam program failed (see above). If it says that "
                         "'escript' was not found, Erlang is not on your PATH.")

    print("Gleam program output:")
    print(result.stdout.strip())


def startup():
    """Makes sure the database is there (building it if needed), loads the
    lists of possible values, and returns the open connection."""
    conn = None
    
    # Tries to open the database twice
    for attempt in (1, 2):
        conn = open_database() if DB_PATH.is_file() else None
        if conn is not None and database_is_ready(conn):
            break
        if conn is not None:
            conn.close()
        if attempt == 2:
            raise SystemExit("The database still is not usable after rebuilding. Exiting.")
        rebuild_database() # Rebuild if failed to open

    print("Database found. Connected successfully (searches are read-only).")
    load_choices(conn)
    return conn


def age_sort_key(age):
    """'0 to 9 years' < '10 to 19 years' < ... < '(unknown)'"""
    return (1000, age) if not age[:1].isdigit() else (int(age.split()[0]), age)


def load_choices(conn):
    """Fills CHOICES, RANGES and TOTAL_ROWS from the database."""
    global TOTAL_ROWS

    for choice, (column, table) in SQLITE_COMMANDS.items():
        # Get distinct age ranges from the database (and replace empty values with UNKNOWN)
        rows = conn.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()
        values = [row[0] for row in rows]

        if choice == "age ranges":
            values = [UNKNOWN if value == "" else value for value in values]
            CHOICES[choice] = sorted(values, key=age_sort_key)
        else:
            CHOICES[choice] = sorted(values)

    CHOICES[BOOLEAN_FIELD] = ["yes", "no"]

    for field, column in NUMERIC_FIELDS.items():
        RANGES[field] = conn.execute(
            f"SELECT min({column}), max({column}) FROM UserTransaction u"
        ).fetchone()

    TOTAL_ROWS = conn.execute("SELECT count(*) FROM UserTransaction").fetchone()[0]

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


# ------------------------------------------------------- SQL BUILDING ------------------------------------------------------

def column_for(field):
    """The SQL column (with table alias) behind a filter field name."""
    if field in SQLITE_COMMANDS:
        column, table = SQLITE_COMMANDS[field]
        return f"{TABLE_ALIASES[table]}.{column}"
    
    if field == BOOLEAN_FIELD:
        return "u.within_sf"
    
    return NUMERIC_FIELDS[field]


def sql_value(field, shown):
    """Converts what the user sees ("yes", "(unknown)") to what is stored."""
    if field == BOOLEAN_FIELD:
        return 1 if shown == "yes" else 0
    
    if shown == UNKNOWN:
        return ""
    
    return shown


def show_value(field, value):
    """Converts a stored value to what the user sees."""
    if field == BOOLEAN_FIELD:
        return "yes" if value else "no"
    
    if value == "" and field == "age ranges":
        return UNKNOWN
    
    return str(value)


def build_where():
    """Turns the current filters into (" WHERE ...", [values for the ?s])."""
    clauses, params = [], []

    for field, chosen in CATEGORY_FILTERS.items():
        column = column_for(field)
        for key, keyword in (("in", "IN"), ("not", "NOT IN")):
            values = [sql_value(field, shown) for shown in chosen[key]]
            if values:
                marks = ", ".join(["?"] * len(values))
                clauses.append(f"{column} {keyword} ({marks})")
                params.extend(values)

    for field, (low, high) in RANGE_FILTERS.items():
        clauses.append(f"{column_for(field)} BETWEEN ? AND ?")
        params.extend([low, high])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def order_by():
    heading = SORT["heading"]
    column = dict(RESULT_COLUMNS)[heading]
    expression = SORT_EXPRESSIONS.get(heading, column)
    # u.TID at the end keeps the order stable when values are equal
    return f" ORDER BY {expression} {SORT['direction']}, u.TID"


def count_matches():
    where, params = build_where()
    return CONN.execute(f"SELECT count(*) {FROM_SQL}{where}", params).fetchone()[0]

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


def format_result_row(row):
    """Makes one database row readable (also used for the CSV export)."""
    cells = []
    for (heading, _), value in zip(RESULT_COLUMNS, row):
        if heading == "Within SF":
            cells.append("yes" if value else "no")
        elif heading == "Age range" and value == "":
            cells.append(UNKNOWN)
        else:
            cells.append(str(value))

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

    where, params = build_where()
    headings = [heading for heading, _ in RESULT_COLUMNS]
    columns = ", ".join(column for _, column in RESULT_COLUMNS)
    print(f"{total:,} matching rows (sorted by {SORT['heading']} {SORT['direction']}).")

    offset = 0
    while offset < total:
        rows = CONN.execute(
            f"SELECT {columns} {FROM_SQL}{where}{order_by()} LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, offset],
        ).fetchall()
        print()
        print_table(headings, [format_result_row(row) for row in rows], NUMBER_HEADINGS)

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


def print_breakdown(field, where, params, total):
    """Count and averages for each value of one field (like GROUP BY)."""
    column = column_for(field)
    order = column if field == "year registered" else "count(*) DESC"

    rows = CONN.execute(
        f"SELECT {column}, count(*), avg(u.checkout_total), avg(u.renewal_total), "
        f"sum(u.checkout_total) {FROM_SQL}{where} GROUP BY {column} ORDER BY {order}",
        params,
    ).fetchall()

    table = [[show_value(field, value), fmt(count), f"{count / total * 100:.1f}%",
              fmt(avg_co), fmt(avg_re), fmt(sum_co)]
             for value, count, avg_co, avg_re, sum_co in rows]
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

    where, params = build_where()
    print(f"Rows matching: {total:,} of {TOTAL_ROWS:,} ({total / TOTAL_ROWS * 100:.1f}% of the table)")
    lines = describe_filters()

    if lines:
        print("Filters:")
        print("\n".join(lines))
    else:
        print("Filters: none")

    # One list of numbers per numeric field
    columns = ", ".join(NUMERIC_FIELDS.values())
    rows = CONN.execute(f"SELECT {columns} {FROM_SQL}{where}", params).fetchall()
    stats = {field: summarize([row[i] for row in rows])
             for i, field in enumerate(NUMERIC_FIELDS)}

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
            print_breakdown(field, where, params, total)


# ------------------------------------------------- UPDATING & DELETING ------------------------------------------------

# The update/delete commands are the ONLY code that changes the database. They
# use a second, short-lived connection (open_writable) so that everything else
# in this program stays read-only.

MIN_YEAR = 1900              # earliest "year registered" that update accepts
MAX_COUNT = 2_147_483_647    # largest checkout/renewal total that update accepts


def open_writable():
    """Opens a connection that CAN change library.db (mode=rw never creates a
    new file). SQLite leaves foreign keys off unless asked, on every connection,
    so turn them on: that is what stops an invalid patron type / age range."""
    conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=rw", uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def plain_column(column):
    """'u.checkout_total' -> 'checkout_total' (UPDATE needs the bare name)."""
    return column.split(".")[-1]


def edit_fields():
    """The fields update can change (same names as the filters use)."""
    return list(NUMERIC_FIELDS) + list(SQLITE_COMMANDS) + [BOOLEAN_FIELD]


def refresh_after_change():
    """Reloads the row count and min/max values, which a change can alter."""
    load_choices(CONN)


def scope_row():
    """Asks for a TID and shows that row. Returns a 'scope' (which rows to
    change) or None if cancelled."""
    first, last = CONN.execute("SELECT min(TID), max(TID) FROM UserTransaction").fetchone()

    if first is None:
        print("The table is empty.")
        return None
    text = ask(f"TID of the row (from {first:,} to {last:,}; 'view results' shows "
               f"TIDs; Enter cancels): ", []).replace(",", "")
    
    if text == "":
        return None
    
    if not text.isdigit():
        print("A TID is a whole number.")
        return None

    columns = ", ".join(column for _, column in RESULT_COLUMNS)
    row = CONN.execute(f"SELECT {columns} {FROM_SQL}WHERE u.TID = ?", [int(text)]).fetchone()

    if row is None:
        print(f"No row has TID {int(text):,} (it may have been deleted).")
        return None

    print()
    print_table([heading for heading, _ in RESULT_COLUMNS],
                [format_result_row(row)], NUMBER_HEADINGS)
    print()

    return {"where": "TID = ?", "params": [int(text)], "count": 1,
            "text": f"row {int(text):,}", "everything": False}


def scope_filtered():
    """The rows matching the current filters (shown first). Returns a scope or None."""
    total = count_matches()

    if total == 0:
        print("No rows match the current filters.")
        return None

    where, params = build_where()

    if where:
        print("Rows matching the current filters:")
        print("\n".join(describe_filters()))
    else:
        print("WARNING: no filters are set, so this would affect EVERY row.")

    # The subquery reuses the same joins as the searches, so every filter works
    return {"where": f"TID IN (SELECT u.TID {FROM_SQL}{where})", "params": params,
            "count": total, "text": f"{total:,} rows", "everything": not where}


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
    Richmond), so ask which code to store. Enter = the first (shortest) one."""
    codes = [row[0] for row in CONN.execute(
        "SELECT home_library_code FROM HomeLibType WHERE home_library_name = ? "
        "ORDER BY length(home_library_code), home_library_code", [name])]
    
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

    where, params = build_where()
    columns = ", ".join(column for _, column in RESULT_COLUMNS)
    cursor = CONN.execute(f"SELECT {columns} {FROM_SQL}{where}{order_by()}", params)

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([heading for heading, _ in RESULT_COLUMNS])

        for row in cursor:
            writer.writerow(format_result_row(row))
    
    print(f"Wrote {total:,} rows to {path}")


def ask_new_value(field):
    """Asks for the new value of one field. Returns (column, value, text to
    show) or None if cancelled. `column` is always one of our own column names."""
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
        return plain_column(NUMERIC_FIELDS[field]), number, text

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

    # Turn what the user picked into the code that is stored in the table
    if field == BOOLEAN_FIELD:
        return "within_sf", 1 if shown == "yes" else 0, shown
    if field == "age ranges":
        return "age_range", sql_value(field, shown), shown
    if field == "patron types":
        code = CONN.execute("SELECT DISTINCT patron_type_code FROM PatronType "
                            "WHERE patron_type_definition = ?", [shown]).fetchone()[0]
        return "patron_type_code", code, shown
    if field == "library names":
        code = choose_library_code(shown)
        return "home_library_code", code, f"{shown} (code {code or '(blank)'})"
    code = CONN.execute("SELECT notification_medium_code FROM NotificationType "
                        "WHERE notification_medium_definition = ?", [shown]).fetchone()[0]
    return "notification_medium_code", code, shown


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
    column, value, shown = new

    print(f"\nThis will set {field} to '{shown}' for {scope['text']}.")
    if not confirm(scope):
        return

    writer = open_writable()
    try:
        # `with` saves (commits) if the UPDATE works and undoes it (rolls back)
        # if anything goes wrong, so a change is applied to all rows or to none
        with writer:
            changed = writer.execute(
                f"UPDATE UserTransaction SET {column} = ? WHERE {scope['where']}",
                [value] + scope["params"],
            ).rowcount
    except sqlite3.IntegrityError:
        print("Nothing was changed: that value is not allowed for at least one of "
              "these rows.\n(The patron type and age range together have to be a "
              "combination that already exists in the database.)")
        return
    finally:
        writer.close()

    print(f"Updated {changed:,} row(s).")
    refresh_after_change()


def delete_rows(scope):
    """Permanently deletes the chosen rows, after a typed confirmation."""
    if scope is None:
        return

    print(f"This will permanently DELETE {scope['text']} from library.db.")
    print("(To get the original data back, close the app, delete library.db and "
          "start the app again: it rebuilds the database from the CSV file.)")
    
    if not confirm(scope):
        return

    writer = open_writable()
    try:
        with writer:
            deleted = writer.execute(
                f"DELETE FROM UserTransaction WHERE {scope['where']}", scope["params"]
            ).rowcount
    finally:
        writer.close()

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
    global CONN
    setup_readline()
    CONN = startup()

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
        except sqlite3.Error as error:
            print(f"Database error: {error}")

    CONN.close()


if __name__ == "__main__":
    main()
