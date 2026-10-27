# Database Scripts

This folder contains all the initialization scripts that helps us (perhaps convert and) load the Excel sheet into the database tables.

The scripts that are used to modify (indexing, moving, updating, data cleaning, ...) the database tables (after the data is loaded into the database) are also included here.

## Files

Gleam only builds modules that sit under `src/`, so the scripts live here (not in a top-level `scripts/` folder). They are run in this order by `src/run_me.gleam`, started with `gleam run` from the repository folder.

| File | Step | What it does |
|---|---|---|
| `init_db.gleam` | 1 | Creates `library.db` and its four tables (`PatronType`, `HomeLibType`, `NotificationType`, `UserTransaction`) with their keys and foreign keys. Does nothing for tables that already exist. |
| `import_csv.gleam` | 2 | Reads `src/SFPL_DataSF_library-usage_Jan_2023.csv` and loads every row, unchanged, into table `UserTransactionRaw` of `library_basic.db`. |
| `fill_tables.gleam` | 3 | Copies the raw data into the four normalised tables of `library.db`, using `INSERT ... SELECT DISTINCT` for the lookup tables. |

### `import_csv.gleam`

- Removes the invisible byte-order mark at the start of the CSV, which would otherwise make the parser fail.
- Cleans the header names: each header cell is two lines (the original name, then a short name). The last non-empty line is used, in lower case, with `_` instead of spaces (`"Patron Type Code\r\npatron_type_code"` becomes `patron_type_code`).
- The `columns` list pairs every column with its SQL type and constraints, in CSV order. The script stops with a clear message if the CSV header no longer matches this list. Only `circulation_active_month` and `circulation_active_year` may be empty (`NULL`).
- Values are passed to SQLite as typed values (`?` placeholders), so no quoting is needed. Blank cells in nullable columns become `NULL`.
- All inserts happen in one transaction, which is much faster than saving each row separately and leaves nothing half-loaded if a row fails. The old rows are deleted first, so it can be run again.

### `fill_tables.gleam`

- Attaches `library_basic.db` as `raw`, so one SQL statement can copy between the two files.
- Fills the lookup tables first, then `UserTransaction` (which points to them). Library names are trimmed, because 15,423 rows have a trailing space (e.g., `"Ortega "`).
- Converts `"True"`/`"False"` to `1`/`0` for `within_sf`, keeps the raw `TID`, and runs everything in one transaction that is rolled back if any statement fails.
- Prints the number of rows in each table at the end.

### Order matters

`import_csv` must run before `fill_tables`, because `fill_tables` reads `library_basic.db`. Each script can be run again safely.
