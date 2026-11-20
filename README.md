# SFILS: San Francisco Integrated Library System.

SFILS is an integrated library system for the San Francisco Public Library.

## Data

This project uses the [library usage data set](https://data.sfgov.org/Culture-and-Recreation/Library-Usage/qzz6-2jup/about_data) to build a complete database of the San Francisco Public Library.

There could be missing data (some of them intentional - to protect the identity of the patrons). However, we do not complement the data with other data sources. The only data set that is used in this project is [SFPL_DataSF_library-usage_Jan 2023.xlsx](<https://data.sfgov.org/api/views/qzz6-2jup/files/ecfd0051-fe40-43c3-a17d-caeff0f1503f?download=true&filename=SFPL_DataSF_library-usage_Jan%202023.xlsx>), which is also linked from the library usage data set page linked above.

## Goal

The goal of this project is to carefully store this Excel sheet in a database with several tables. Eventually, we find interesting insights into the San Francisco public library through this database, and provide an app that allows for updates and edits.

The aim is to understand and demonstrate the optimal use of MySQL through efficient indices and multiple tables, rather than simply dumping the whole excel sheet into a single large table. We create several tables from this single excel sheet in a meaningful manner.
 
Assignment 2 stores the same data in MongoDB (MISFILS, in [`mongo/`](mongo/README.md)), as nested documents instead of related tables, with the same app on top, so that the two implementations can be compared.
 
## Current implementation (status)
 
There are two implementations of the same system. Both load the same CSV file, and both apps have the same commands.
 
| Part            | Assignment 1: SQLite                                                                                                                                                                                                         | Assignment 2: MongoDB (MISFILS)                                                                                                                                                              |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Database engine | **SQLite** (`library.db`). The assignment asks for MySQL/MariaDB, which I now realize as I fix this README; the schema, keys and SQL are standard, and a MySQL port is the remaining work (see "Known limitations"). | **MongoDB** (`library_database` on a local server at `mongodb://localhost:27017/`), reached from Python with PyMongo.                                                                    |
| Schema          | 4 tables, normalised from the single spreadsheet: `UserTransaction` plus three lookup tables (`PatronType`, `HomeLibType`, `NotificationType`) linked by foreign keys. Details in [`docs/`](docs/README.md).        | 1 collection (`library`) of nested documents: each document carries its codes and their definitions in sub-documents, so there are no lookup collections. Details in [`docs/`](docs/README.md). |
| Loading         | A Gleam pipeline in [`src/`](src/scripts/README.md) reads the CSV, loads it as-is into `library_basic.db`, then builds the normalised `library.db` from it.                                                             | Two Python scripts in [`mongo/`](mongo/README.md): `import_csv.py` loads the CSV as-is into the `library_basic` collection, then `init_db.py` builds the nested `library` collection from it. |
| App             | A terminal app in [`app/`](app/README.md) to **search** (filters with autocomplete), compute **statistics**, **export** CSV, and **update** or **delete** rows.                               | The same terminal app, converted to MongoDB queries: [`mongo/main.py`](mongo/README.md).                                                                                                    |
| Findings        | Counts, averages and timings are in [`results/`](results/README.md).                                                                                                                                                        | Storage, query time, build time and app speed are collected with `mongo/metrics.py`.                                                                                                        |
 
### Quick start
 
SQLite:
 
```sh
python3 app/main.py
```
 
If `library.db` does not exist yet, the app builds it first by running `gleam run` (about 30 seconds).
 
MongoDB:
 
```sh
pip install pymongo
python3 mongo/main.py
```
 
The MongoDB server has to be running already. If the `library` collection is empty, the app builds it first by running `import_csv.py` and `init_db.py`.
 
Requirements and troubleshooting for both are in [`docs/README.md`](docs/README.md).
 
### Folders

| Folder / file | Contents                                                                                                                                 |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `app/`      | The SQLite terminal application (`main.py`, Python standard library only).                                                             |
| `src/`      | Gleam source: `run_me.gleam` (runs the whole pipeline) and `scripts/` (create tables, import CSV, fill tables), plus the source CSV. |
| `docs/`     | How to build and run, the schema and document structure, and troubleshooting, for both implementations.                                 |
| `results/`  | Findings, performance figures, and CSV files exported by either app.                                                                    |
| `mongo/`    | Assignment 2 (MISFILS): `import_csv.py`, `init_db.py`, the MongoDB terminal application (`main.py`) and `metrics.py`.             |
 
### Known limitations
 
SQLite:
 
- **Not MySQL yet.** The database is SQLite. Porting means changing `AUTOINCREMENT` to `AUTO_INCREMENT`, dropping the SQLite-only `PRAGMA`/`ATTACH` statements, using `%s` placeholders in the app, and adding indexes (see `results/README.md`).
- **No secondary indexes yet.** Only the primary-key indexes exist.
- **Dropped columns.** `Circulation Active Month/Year` and `Provided Email Address` are kept only in the raw table `library_basic.db`; they are not in `library.db` or the app.
- **Updates and deletes are permanent** (there is no undo); the original data can be rebuilt from the CSV (see `app/README.md`).
MongoDB:
 
- **No secondary indexes yet.** Only the automatic index on `_id` exists, so every filtered query reads the whole collection.
- **Repeated definitions.** Patron type definitions, library names and notification definitions are stored in every document instead of in lookup collections, and nothing in the database ties a code to its definition; the app sets both together.
- **No schema validator on `library`.** Only `library_basic` has one.
- **Unused fields.** `circulation_active` and `email_provided` are kept in `library`, but the app does not show or filter on them.
- **Updates of many documents are not all-or-nothing.** If the server stops halfway, the documents already changed stay changed (SQLite undoes the whole update).
- **Read-only is enforced by the app, not the database.** Searches go through a wrapper that only has reading methods; MongoDB has no read-only connection mode like SQLite's.
- **Updates and deletes are permanent** (there is no undo); the original data can be rebuilt by running `mongo/init_db.py`.
 
