# MISFILS

MISFILS: The MongoDB-based Implementation for the San Francisco Integrated Library System.

This folder contains the submission files for Assignment 2. The remaining folders contain the files from Assignment 1.

This includes the initialization scripts, the scripts that are used to modify (indexing, moving, updating, data cleaning, ...) the database tables (after the data is loaded into the database), as well as the app program itself (the user interface).

## Files


| File                  | Step | What it does                                                                                                                                                                                          |
| --------------------- | ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `import_csv.py`     | 1    | Reads `src/SFPL_DataSF_library-usage_Jan_2023.csv` and loads every row into table `library_basic` of `library_database` (with minimal column name processing). |
| `init_db.py`  | 2    | Creates and loads every row from `library_basic` into the `library` table, leaving out parameters with `NULL` values.                                                       |
| `main.py` | 3    | Allows the user to query, remove, and update rows in the `library` table. Also allows the user to view and export statistics about the database with or without data filters. |

### `import_csv.py`

- Removes the invisible byte-order mark at the start of the CSV, which would otherwise make the parser fail.
- Cleans the header names: each header cell is two lines (the original name, then a short name). The last non-empty line is used, in lower case and with `_` instead of spaces (`"Patron Type Code\r\npatron_type_code"` becomes `patron_type_code`).
- The `columns` list pairs every column with its BSON type and constraints (required or not required), in CSV order. The script stops with a clear message if the CSV header no longer matches this list. `circulation_active_month`, `circulation_active_year`, `age_range`, and `home_library_code` may be empty (`NULL`) -- `home_library_definition` may be `NULL`, since their codes can be null.
- Values are passed to MongoDB by their respective `Python` type, which is automatically converted to the appropriate `BSON` type. `NULL` parameters are not passed to their respective MongoDB documents.
- All inserts happen in batches of 10,000 rows (via `insert_many(docs, ordered=False)`), which is much faster than saving each row separately. Although using `ordered=False` means MongoDB would attempt to insert the whole batch prior to reporting errors, the table is dropped first, so previous iterations would not affect the current run.

### `init_db.py`

- Stops with a clear message if `library_basic` is empty, then drops and recreates the `library` collection.
- Reads `library_basic` 10,000 documents at a time and inserts each batch with `insert_many(docs, ordered=False)`, so the whole collection is never held in memory.
- `DOC_TEMPLATE` maps every field of the new document to the raw column it comes from. Related columns are grouped into sub-documents (`patron`, `library`, `notification`, `circulation_active`) and given shorter names (`checkout_total` becomes `checkouts`).
- Builds a new document for every row, so the template itself is never changed. Text values are trimmed again as a safeguard, and numbers and booleans are copied as they are, because `import_csv.py` already converted them.
- Leaves out a field whose raw value is missing (`age_range`, `home_library_code`, `circulation_active_month`, `circulation_active_year`), and leaves out `circulation_active` altogether when both of its parts are missing. A missing value in any other column stops the script with the `_id` of the row.
- Keeps the raw `_id`, so every document can be traced back to `library_basic`.
- Checks at the end that `library` holds exactly as many documents as `library_basic`, and prints that number. There is no transaction: if the script fails halfway, running it again drops the partial collection and starts over.

### `main.py`

- Checks that the MongoDB server answers (5 second limit) and exits with a clear message if it does not. If `library` is empty, it runs `import_csv.py` (only when `library_basic` is empty too) and `init_db.py` before starting.
- Loads the possible values of every filter field, the minimum and maximum of every numeric field, and the document count at start-up, so the user never has to guess what is valid.
- Turns the current filters into one MongoDB filter document (`$in`/`$nin` for chosen values, `$gte`/`$lte` for ranges). Anything the user types is only ever used as a value, never as a field name or an operator.
- All searching goes through a wrapper that only has the reading methods of the collection. Only the update and delete commands use the real collection, and each asks for a typed confirmation first.
- `view results` and `export` fetch only the nine displayed fields, sorted on the server. Age ranges are sorted by their position in the list of age ranges, because as text `"10 to 19 years"` would come before `"9"`.
- `view stats` fetches the three numeric fields and computes the statistics in Python. The breakdowns are grouped on the server with `$group`.
- Updates use `update_many`. Changing a patron type, library or notification type sets the code and its definition together, and setting a value to blank removes the field. Unlike a SQLite transaction, an update of many documents that fails halfway is not undone.

### Order matters

`import_csv` must run before `init_db`, because `init_db` reads `library_basic`. `main` needs `library`, and runs both scripts itself if the collection is empty. Each script can be run again safely, but running `init_db` again discards any changes made with the app's update and delete commands.