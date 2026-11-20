# Findings

This folder contains our findings from this database project.

The findings include details about the library patrons. For example, how many from the age range 0 to 9 years used the library, how many of them were repeat patrons (can be found from total checkouts), how many renewed (can be found from total renewals).

# Performance Metrics

This folder also contains the performance values.

Did we store the data in our database appropriately?

This is meant to be a more manageable database with multiple tables. We are not simply dumping the whole Excel sheet into one giant MySQL table.

All figures below were measured on the unmodified `library.db` (SQLite, 437,115 rows) and the unmodified `library_database` (MongoDB, 437,115 rows). Every number can be reproduced with the app (`view stats`, with breakdowns) or with `sqlite3 library.db`/`mongosh library_database`.

## Findings

### Overview

| Measure | Value |
|---|---|
| Patron records | 437,115 |
| Total checkouts | 70,412,694 |
| Total renewals | 33,101,214 |
| Mean checkouts per patron | 161.1 (the median is only 7, because a few very heavy users pull the mean up; the maximum is 45,380) |
| Median renewals per patron | 3 (mean 75.7) |
| Patrons with no checkouts at all | 139,222 (31.9%) |
| Registered within San Francisco county | 84.5% |

### By age range

"Active" means at least one checkout; "renewed" means at least one renewal.

| Age range | Patrons | Avg checkouts | Avg renewals | Active | Renewed |
|---|---:|---:|---:|---:|---:|
| 0 to 9 years | 34,642 | 68.5 | 46.7 | 58.2% | 49.8% |
| 10 to 19 years | 67,320 | 206.7 | 70.5 | 75.1% | 58.8% |
| 20 to 24 years | 24,484 | 163.6 | 47.3 | 68.0% | 56.0% |
| 25 to 34 years | 92,790 | 47.9 | 26.8 | 60.7% | 49.5% |
| 35 to 44 years | 80,477 | 76.5 | 54.9 | 67.6% | 57.0% |
| 45 to 54 years | 48,752 | 184.8 | 111.1 | 72.9% | 62.7% |
| 55 to 59 years | 19,281 | 262.2 | 132.0 | 71.8% | 61.5% |
| 60 to 64 years | 17,664 | 297.2 | 142.5 | 70.9% | 61.1% |
| 65 to 74 years | 31,906 | 361.9 | 157.8 | 71.6% | 62.3% |
| 75 years and over | 18,872 | 422.3 | 141.8 | 77.0% | 67.9% |
| (unknown age range) | 927 | 742.8 | 519.8 | 60.8% | 57.8% |

Observations:

- Average checkouts rise steadily from the age of 45, and are highest for patrons aged 75 and over (422.3, about 6 times the 0 to 9 group).
- Ages 10 to 19 are a second peak (206.7), well above the 20 to 44 groups.
- The largest group, ages 25 to 34 (92,790 patrons), borrows the least (47.9 on average).
- Renewals follow the same pattern as checkouts, and the share of patrons who renewed at least once is higher in the older groups (about 62% to 68%) than in the 0 to 9 and 25 to 34 groups (about 50%).

### Libraries and notifications

| Home library | Patrons |
|---|---:|
| Main | 143,471 |
| Richmond | 20,736 |
| Mission | 19,212 |
| Mission Bay | 15,878 |
| Ortega | 15,423 |

| Notification preference | Patrons |
|---|---:|
| Email | 391,614 |
| Phone | 35,559 |
| Print | 7,242 |
| None | 2,700 |

## Performance Metrics

### Storage

| File | Size | Contents |
|---|---:|---|
| `library_basic.db` | 33.7 MB | the spreadsheet loaded as-is into one table (14 columns) |
| `library.db`       | 16.1 MB | the same rows in four related tables                     |

Splitting the repeated text (patron type definitions, library names, notification definitions) into lookup tables makes the database about **52%** smaller than the single-table version (16.1 MB vs 33.7 MB), while every row can still be joined back to its definitions. The lookup tables hold only 134, 68, and 4 rows.

| File              | BSON Size | Compressed Size | Contents                                                 |
| ----------------- | --------: | --------------: | -------------------------------------------------------- |
| `library_basic` |  182.6 MB |         30.4 MB | the spreadsheet loaded as-is into one table (14 columns) |
| `library`       |  133.3 MB |         24.2 MB | documents with shorter field names and nested fields     |

MongoDB stores the field names inside every document, so shortening them (e.g., `within_san_francisco_county` -> `within_sf`) and grouping related fields into sub-documents (e.g., `patron_type_code`, `patron_type_definition` -> `patron`: {`code`, `type`}) makes the documents about **27%** smaller before compression (133.3 MB vs 182.6 MB). On disk the saving is about **20%** (24.2 MB vs 30.4 MB), because MongoDB compresses its data files and repeated field names compress well in either shape.

Compared with SQLite, `library` takes about 50% more space on disk than `library.db` (24.2 MB vs 16.1 MB) even after compression, because every document still carries its definitions. The collection could be made smaller by moving the definitions into lookup collections, similar to the SQLite implementation, at the cost of an additional `$lookup` or an in-memory dictionary.

### Query time

SQLite: the query "average checkouts of Richmond patrons aged 25 to 34" (3,599 rows) joins `UserTransaction` to `HomeLibType`. It took **0.03 s** in each of three runs (`sqlite3`, whole process including start-up), over the full 437,115-row table. The plan scans `UserTransaction` once and looks up each library code through the primary-key index of `HomeLibType`.

MongoDB: the same query on the `library` collection took **0.07 s** on average over three runs (timed from Python, including the network round trip to the server but not start-up), over all 437,115 documents. No join is needed, because the library name is stored in every document. The plan scans the whole collection once, keeps the documents for Richmond patrons aged 25 to 34, and averages their checkouts.

The two times were not measured in the same way (a whole `sqlite3` process against one call from a Python program that is already running), so they show that both are fast at this size, more than which one is faster.

### Building the database

SQLite: `gleam run` (create the tables, load all 437,115 CSV rows, fill the normalised tables) took **13 s** when the dependencies were already compiled, and around **25 s** on a first run from a clean copy, which includes about 10 s of compiling. The inserts are fast because they all share one transaction.

MongoDB: `import_csv.py` (create the raw collection, load all 437,115 CSV rows, verify them) took 5.1 s and `init_db.py` (create the `library` collection and fill it from the raw collection) took 4.5 s, for a total of **9.5 s**. The inserts are fast because they are sent in batches of 10,000 documents.

### App speed

In SQLite, on the full table (437,115 rows, no filters), `view stats` takes **0.63 s** (including starting the app and reading all three numeric columns to compute medians and percentiles), and showing the first page of `view results` takes **0.21 s**.

In MongoDB, on the full collection (437,115 documents, no filters), `view stats` takes **1.65 s** (including starting the app and reading all three numeric fields to compute medians and percentiles), and showing the first page of `view results` takes **1.14 s**.

In the case of MongoDB, most of the time is spent on start-up; before its first command, the app scans the collection several times to load the possible values of each filter field, the minimum and maximum of each numeric field, and the document count, and with no indexes each of these reads all 437,115 documents.

### Not done yet

- **No secondary indexes (SQLite).** Only the primary-key indexes exist (`PatronType`, `HomeLibType`, `NotificationType`). Queries are fast enough at this size, but indexes on the `UserTransaction` foreign-key columns (and on the columns the app filters on) have not been added or measured, so there is no before/after comparison yet.
- **No secondary indexes (MongoDB).** Only the automatic index on `_id` exists, so every filter, sort and start-up query reads the whole collection. Indexes on the fields the app filters on have not been added or measured.
- **Not MySQL.** The Assignment 1 figures are for SQLite. Re-measuring on MySQL/MariaDB (with and without indexes, using `EXPLAIN`) is the main remaining work for that assignment.

## Exported results

Files written by either app's `export` command are saved in this folder as `export_<date>_<time>.csv` or by the name of your choice.