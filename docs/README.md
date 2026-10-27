# Project Documentation

Here, we maintain the documentation of the project.

This includes how to build and run the software, along with any screenshots that can be of help.

## What you need

| Tool | Used for | Notes |
|---|---|---|
| Python 3 | the app (`app/main.py`) | Standard library only. Developed with 3.14. |
| [Gleam](https://gleam.run) | building the database from the CSV | Developed with 1.18. |
| Erlang/OTP | running Gleam | `brew install erlang`. Gleam needs `escript` on your `PATH`. |

The app only needs Python once `library.db` exists. Gleam and Erlang are only needed to (re)build it.

## Build and run

```sh
python3 app/main.py        # builds the database first if it is missing, then starts the app
                           # (run it from any folder; the app finds the repository itself)
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

## The database

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