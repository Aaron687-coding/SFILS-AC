# SFILS: San Francisco Integrated Library System.

SFILS is an integrated library system for the San Francisco Public Library.

## Data

This project uses the [library usage data set](https://data.sfgov.org/Culture-and-Recreation/Library-Usage/qzz6-2jup/about_data) to build a complete database of the San Francisco Public Library.

There could be missing data (some of them intentional - to protect the identity of the patrons). However, we do not complement the data with other data sources. The only data set that is used in this project is [SFPL_DataSF_library-usage_Jan 2023.xlsx](<https://data.sfgov.org/api/views/qzz6-2jup/files/ecfd0051-fe40-43c3-a17d-caeff0f1503f?download=true&filename=SFPL_DataSF_library-usage_Jan%202023.xlsx>), which is also linked from the library usage data set page linked above.

## Goal

The goal of this project is to carefully store this Excel sheet in a database with several tables. Eventually, we find interesting insights into the San Francisco public library through this database, and provide an app that allows for updates and edits.

The aim is to understand and demonstrate the optimal use of MySQL through efficient indices and multiple tables, rather than simply dumping the whole excel sheet into a single large table. We create several tables from this single excel sheet in a meaningful manner.

## Current implementation (status)

| Part            | What exists now                                                                                                                                                                                                              |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Database engine | **SQLite** (`library.db`). The assignment asks for MySQL/MariaDB, which I now realize as I fix this README; the schema, keys and SQL are standard, and a MySQL port is the remaining work (see "Known limitations"). |
| Schema          | 4 tables, normalised from the single spreadsheet: `UserTransaction` plus three lookup tables (`PatronType`, `HomeLibType`, `NotificationType`) linked by foreign keys. Details in [`docs/`](docs/README.md).        |
| Loading         | A Gleam pipeline in [`src/`](src/scripts/README.md) reads the CSV, loads it as-is into `library_basic.db`, then builds the normalised `library.db` from it.                                                             |
| App             | A terminal app in [`app/`](app/README.md) to **search** (filters with autocomplete), compute **statistics**, **export** CSV, and **update** or **delete** rows.                               |
| Findings        | Counts, averages and timings are in [`results/`](results/README.md).                                                                                                                                                        |

### Quick start

```sh
python3 app/main.py
```

If `library.db` does not exist yet, the app builds it first by running `gleam run` (about 30 seconds). Requirements and troubleshooting are in [`docs/README.md`](docs/README.md).

### Folders

| Folder / file | Contents                                                                                                                                 |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `app/`      | The terminal application (`main.py`, Python standard library only).                                                                    |
| `src/`      | Gleam source: `run_me.gleam` (runs the whole pipeline) and `scripts/` (create tables, import CSV, fill tables), plus the source CSV. |
| `docs/`     | How to build and run, the schema, and troubleshooting.                                                                                   |
| `results/`  | Findings, performance figures, and CSV files exported by the app.                                                                        |
| `mongo/`    | Reserved for Assignment 2 (not started).                                                                                                 |

### Known limitations

- **Not MySQL yet.** The database is SQLite. Porting means changing `AUTOINCREMENT` to `AUTO_INCREMENT`, dropping the SQLite-only `PRAGMA`/`ATTACH` statements, using `%s` placeholders in the app, and adding indexes (see `results/README.md`).
- **No secondary indexes yet.** Only the primary-key indexes exist.
- **Dropped columns.** `Circulation Active Month/Year` and `Provided Email Address` are kept only in the raw table `library_basic.db`; they are not in `library.db` or the app.
- **Updates and deletes are permanent** (there is no undo); the original data can be rebuilt from the CSV (see `app/README.md`).
