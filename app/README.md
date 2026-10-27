# The App to Query, Retrieve, and Update Database

This folder contains the source code of the (minimal) application that allows user interaction with the database.

It is a single-file terminal application, `main.py`, written with the Python standard library only (no `pip install` needed).

## Running it

```sh
python3 app/main.py
```

It can be started from any folder. If `library.db` is missing or empty, the app runs `gleam run` in the repository folder to build it (about 30 seconds; needs Gleam and Erlang, see [`docs/README.md`](../docs/README.md)).

Type a command, or just its beginning (`view r` is enough for `view results`). Press **Tab** to autocomplete commands, field names and values; pressing Tab twice lists every match. `Ctrl-C` cancels the current question, and `Ctrl-D` leaves the app.

## Commands

| Command | What it does |
|---|---|
| `set filters` | Choose which rows to look at. Pick a field, then values. Text fields show every possible value and autocomplete them; numeric fields show the minimum and maximum in the database. Enter keeps a default, `clear` removes the filter. |
| `show filters` | Show the current filters, the sort order, and how many rows match. |
| `clear filters` | Remove every filter. |
| `sort` | Choose the column and `asc`/`desc` for the results and the export. Age ranges sort numerically (0 to 9, 10 to 19, ...). |
| `view results` | Show the matching rows 20 at a time (Enter = next page, `q` = stop). |
| `view stats` | Statistics for the matching rows: sum, mean, median, standard deviation, min, max, 90th and 99th percentile of checkouts, renewals and year registered; renewals per checkout; and an optional breakdown by any field. |
| `export` | Save the matching rows to a CSV file in `results/` (asks before overwriting). |
| `update row` | Change one field of a single row, found by its `TID` (shown by `view results`). |
| `update filtered` | Change one field of **all** rows that match the current filters. |
| `delete row` | Delete a single row, found by its `TID`. |
| `delete filtered` | Delete **all** rows that match the current filters. |
| `help`, `exit`, `quit` | Show the command list, or leave. |

### Filters

- **Fields:** age ranges, patron types, library names, notification types, within sf (yes/no), checkouts, renewals, year registered.
- Several values in **one** field are combined with OR (Teen or Adult). **Different** fields are combined with AND (Teen and Richmond).
- Start a value with `not ` to exclude it (`not Main`).
- Library filters use the 30 library **names**, not the 68 library codes, because several codes share one name (`r3`, `r3aaa` and `r3j` are all Richmond).
- Rows with no age range are shown as `(unknown)`.
- Filters stay set until you clear them, so set them once and then use `view results`, `view stats`, `export`, `update filtered` or `delete filtered`.

### Updating and deleting

Searching, statistics and export use a **read-only** connection, so they can never change the data. Only the four update/delete commands change the database, through a separate connection.

- **Preview first.** `update row` and `delete row` show the row before asking anything. The `filtered` versions show the filters and the number of rows.
- **Typed confirmation.** Nothing changes until you type the confirmation word: `yes` for one row, the exact **number of rows** for several (for example `2,097`), and `ALL` if no filters are set. Anything else cancels.
- **All or nothing.** A change is saved in one transaction: either every chosen row is changed, or none.
- **Validated values.** Numbers must be whole numbers in a sensible range (a year registered from 1900 to the current year; counts of 0 or more). Text fields only accept values that exist in the database, completed with Tab.
- **Relationships are enforced.** Foreign keys are switched on for the writing connection, so a patron type and age range must be a combination that already exists. For example, `Teen` with `75 years and over` is refused, and nothing is changed.
- **Library names.** Choosing a library name stores one of its codes; if the name has several codes, the app asks which one.
- **What can be changed.** One field per update, of: checkouts, renewals, year registered, age range, patron type, library name, notification type, within sf. The lookup tables themselves (`PatronType`, `HomeLibType`, `NotificationType`) can not be edited from the app.
- **There is no undo.** To restore the original data, close the app, delete `library.db`, and start the app again: it rebuilds the database from the CSV file.

### Exported files

`export` writes `results/export_<date>_<time>.csv` by default (or the name you type). The file name is always kept inside `results/`. Columns: TID, Patron type, Age range, Library, Notification, Checkouts, Renewals, Within SF, Year registered. A full export is about 25 MB.

## Notes

- All user input goes into the SQL through `?` placeholders. The only text built into the SQL is column and table names written in `main.py`.
- Searches, statistics and exports run on the whole table (437,115 rows) in well under a second on a laptop.
- The app works with **SQLite** (`library.db`); see the project [README](../README.md) for the MySQL status.
