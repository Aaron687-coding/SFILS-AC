import gleam/list

import scripts/fill_tables
import scripts/import_csv
import scripts/init_db

pub fn main() {
  // Create database
  init_db.create_db()
  // Import data from CSV
  // (Create one-table in the database first)
  import_csv.import_csv_basic()
  // Fill the database with the imported data
  fill_tables.fill_tables()
}
