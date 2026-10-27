// import gleam/list

import scripts/fill_tables
import scripts/import_csv
import scripts/init_db

pub fn main() {
  // Create database
  // create_db() returns a list of successful/failed SQLite commands, and
  // Stops when a command fails
  let _throwaway = init_db.create_db()

  // Import data from CSV
  // (Also creates a one-table database - library_basic.db)
  import_csv.import_csv_basic()

  // Fill the database with the imported data
  fill_tables.fill_tables()
}
