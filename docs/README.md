# Project Documentation

Here, we maintain the documentation of the project.

This includes how to build and run the software, along with any screenshots that can be of help.

## What you need

### SQLite

| Tool | Used for | Notes |
|---|---|---|
| Python 3 | the app (`app/main.py`) | Standard library only. Developed with 3.14. |
| [Gleam](https://gleam.run) | building the database from the CSV | Developed with 1.18. |
| Erlang/OTP | running Gleam | `brew install erlang`. Gleam needs `escript` on your `PATH`. |

The app only needs Python once `library.db` exists. Gleam and Erlang are only needed to (re)build it.

### MongoDB

| Tool | Used for | Notes |
|---|---|---|
| Python 3 | all programs (`mongo/*.py`) | 3.12 or newer, because `itertools.batched` is used. Developed with 3.14. |
| [PyMongo](https://pymongo.readthedocs.io) | talking to MongoDB from Python | `pip install pymongo`. The only package outside the standard library. |
| MongoDB server | storing the data | Must already be running at `mongodb://localhost:27017/`. 7.0 or newer, because the import check uses `$median`. |
 
All of the MongoDB implementation is written in Python. Unlike SQLite, the database is on the MongoDB server, so the server has to be started before any of the programs are run.

## Build and run

### SQLite

```sh
python3 app/main.py        # builds the database first if it is missing, then starts the app
```

To build the database by hand (from the repository folder, because the paths are relative to where you run it):

```sh
gleam run
```

`gleam run` runs `src/run_me.gleam`, which does three steps. It takes about 13 seconds, or about 25 seconds the first time because the dependencies are compiled then:

1. `init_db.create_db()`: creates the four tables in `library.db`.
2. `import_csv.import_csv_basic()`: reads `src/SFPL_DataSF_library-usage_Jan_2023.csv` and loads all 437,115 rows, as they are, into `library_basic.db` (table `UserTransactionRaw`).
3. `fill_tables.fill_tables()`: copies the data from `library_basic.db` into the four normalised tables of `library.db`.

All three steps are safe to repeat: the old rows are cleared first, so nothing is duplicated.

### MongoDB

```sh
python3 mongo/main.py # builds the database first if it is missing, then starts the app
```

To build the database by hand (from the repository folder, because the paths are relative to where you run it):

```sh
python3 mongo/import_csv.py && python3 mongo/init_db.py
```

These programs import entries from the CSV document into `library_database` on a MongoDB localhost, and then converts them into briefer nested documents for use by `main.py`:

1. `import_csv.import_csv_basic()`: reads `src/SFPL_DataSF_library-usage_Jan_2023.csv` and loads all 437,115 rows, as they are, into `library_database` (table `library_basic`).
2. `init_db.insert_docs()`: inserts documents from one given MongoDB collection to another in batches, using a nested document template.
3. `init_db.init_db()`:  drops and recreates a `library` collection, inserting documents from `library_basic` to `library` using `insert_docs()`.

Both steps are safe to repeat: each one drops its collection first, so nothing is duplicated.
 
To collect the performance figures (storage, query time, app speed, and build time with `--build`):
 
```sh
python3 mongo/metrics.py            # add --build to also time a full rebuild (this drops both collections)
```

## The database

### SQLite

`library.db` has one fact table and three lookup tables:

| Table | Rows | Key | Holds |
|---|---|---|---|
| `PatronType` | 134 | `(patron_type_code, age_range)` | patron type definition (Juvenile, Adult, ...) |
| `HomeLibType` | 68 | `home_library_code` | library name (68 codes map to 30 names) |
| `NotificationType` | 4 | `notification_medium_code` | Email, Phone, Print, None |
| `UserTransaction` | 437,115 | `TID` | one row per row of the spreadsheet |

Design notes:

- `PatronType` is keyed by code **and** age range because the same patron type (for example `Teacher Card`) appears with several age ranges.
- `UserTransaction` has three foreign keys, one for each lookup table. SQLite only enforces them when `PRAGMA foreign_keys = ON` is set on the connection, which `fill_tables.gleam` and the app's update/delete commands both do.
- `TID` is the same number as in the raw table, so every row can be traced back to `library_basic.db`.
- `Circulation Active Month/Year` (blank in 40,592 rows) and `Provided Email Address` are only in the raw table.
- `Within San Francisco County` is stored as `1`/`0` (`within_sf`).

`library.db`, `library_basic.db` and `.env` are in `.gitignore` and are not committed.

### MongoDB

`library_database` has two collections, each with one document per row of the spreadsheet:
 
| Collection | Documents | Shape | Used for |
|---|---|---|---|
| `library_basic` | 437,115 | flat, 14 spreadsheet columns as 14 fields | importing the CSV with minimal processing |
| `library` | 437,115 | nested, related fields grouped into sub-documents | everything `mongo/main.py` reads, updates and deletes |
 
One document from `library` looks like this:
 
```js
{
  _id: 0, // row number, the same in both collections
  patron:       { code: 0, type: "Adult", age_range: "25 to 34 years" },
  library:      { code: "r3", name: "Richmond" },
  notification: { code: "z", medium: "Email" },
  checkouts: 53,
  renewals: 15,
  within_sf: true,
  year_registered: 2010,
  circulation_active: { month: "Jan", year: 2023 },
  email_provided: true
}
```

Design notes:

- Neither table contains a lookup table, so each document carries both the code and its definition (`patron.code` and `patron.type`, and so on). While reading a document never requires a join, definitions are repeated in every document.
- Blank values (such as in `patron.age_range`, `library.code`, and `circulation_active`) are left out of the document, instead of being represented as empty strings. The app shows a missing age range as `(unknown)`.
- `library_basic` has a schema validator (`$jsonSchema`, strict) that rejects a document with a missing required field or a value of the wrong type. `library` has no validator.
- `circulation_active` and `email_provided` are kept in `library`, unlike the SQLite version, but the app does not show or filter on them.
- Neither collection has an index other than the automatic one on `_id`.

The data is stored by the MongoDB server in its own data folder, so there are no database files in this repository to commit or ignore.

## Using the app

Both apps (`app/main.py` and `mongo/main.py`) have the same commands. Type a command or just its beginning, and press Tab to complete it.
 
|| Command | What it does |
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
 
Filters stay set until they are cleared. Within one field the chosen values are combined with OR, and different fields are combined with AND. Starting a value with `not ` excludes it.
 
Searching never changes the database. Only the update and delete commands do, and each asks for a typed confirmation first: `yes` for one row, the number of rows for several, and `ALL` when no filters are set.
 
To undo changes made with update or delete, rebuild the database: for SQLite, delete `library.db` and start the app again; for MongoDB, run `python3 mongo/init_db.py`.
