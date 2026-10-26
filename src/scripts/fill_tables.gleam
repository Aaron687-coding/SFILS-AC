import gleam/dynamic/decode
import gleam/int
import gleam/io
import gleam/list
import gleam/result
import scripts/init_db
import simplifile
import sqlight

// Fills the tables made by init_db using the raw database created by import_csv
//! CALL THIS AFTER import_csv_basic() 
// (init_db() is technically run here but it's more readable to call it separately)

// CONST VARS
const raw_db_path = "library_basic.db"

const db_path = "library.db"

// SQL statements to run (in order); start with the smaller tables first
// `raw.` = table in library_basic.db (i.e., created by import_csv_basic())
const statements = [
  // Clear old data so re-running does not duplicate or conflict
  // Children (UserTransaction) go before the tables they point to
  "DELETE FROM UserTransaction;",
  "DELETE FROM PatronType;",
  "DELETE FROM HomeLibType;",
  "DELETE FROM NotificationType;",
  // AUTOINCREMENT remembers its highest number, so reset it as well
  "DELETE FROM sqlite_sequence WHERE name = 'UserTransaction';",
  // Lookup tables: one row for each distinct combination in the raw data
  // (code + age range) -> definition
  "INSERT INTO PatronType (patron_type_code, age_range, patron_type_definition) "
    <> "SELECT DISTINCT patron_type_code, age_range, patron_type_definition "
    <> "FROM raw.UserTransactionRaw;",
  // code -> name (trim names to avoid trailing space)
  "INSERT INTO HomeLibType (home_library_code, home_library_name) "
    <> "SELECT DISTINCT home_library_code, trim(home_library_definition) "
    <> "FROM raw.UserTransactionRaw;",
  // code -> definition
  "INSERT INTO NotificationType (notification_medium_code, notification_medium_definition) "
    <> "SELECT DISTINCT notification_medium_code, notification_code_definition "
    <> "FROM raw.UserTransactionRaw;",
  // UserTransaction: one row per raw row, with the same TID as raw
  // ("True"/"False" becomes 1/0)
  "INSERT INTO UserTransaction (TID, checkout_total, renewal_total, within_sf, "
    <> "year_registered, patron_type_code, age_range, home_library_code, "
    <> "notification_medium_code) "
    <> "SELECT TID, checkout_total, renewal_total, "
    <> "CASE within_san_francisco_county WHEN 'True' THEN 1 WHEN 'False' THEN 0 END, "
    <> "year_patron_registered, patron_type_code, age_range, home_library_code, "
    <> "notification_medium_code "
    <> "FROM raw.UserTransactionRaw ORDER BY TID;",
]

// Tables to report row counts for afterwards
const tables = [
  "PatronType", "HomeLibType", "NotificationType", "UserTransaction",
]

// Runs every statement inside one transaction: either all of them succeed and
// are saved, or an error occurs and nobody passes
fn run_statements(conn: sqlight.Connection) -> Result(Nil, sqlight.Error) {
  use _ <- result.try(sqlight.exec("BEGIN;", conn))

  case list.try_each(statements, fn(sql) { sqlight.exec(sql, conn) }) {
    Ok(Nil) -> sqlight.exec("COMMIT;", conn)
    Error(error) -> {
      // Undo everything done since BEGIN, then pass the error on
      let _ = sqlight.exec("ROLLBACK;", conn)
      Error(error)
    }
  }
}

// How many rows a table has
fn count_rows(conn: sqlight.Connection, table: String) -> Int {
  let decoder = {
    use count <- decode.field(0, decode.int)
    decode.success(count)
  }
  let assert Ok([count]) =
    sqlight.query(
      "SELECT count(*) FROM " <> table <> ";",
      on: conn,
      with: [],
      expecting: decoder,
    )
  count
}

// Creates the tables if needed (init_db), then fills them from the raw data
pub fn fill_tables() -> Result(Nil, sqlight.Error) {
  // Make sure the tables exist (does nothing if they already do)
  let assert Ok(Nil) = init_db.create_db()

  // ATTACH would create an empty file if it is missing, so check first
  let assert Ok(True) = simplifile.is_file(raw_db_path)
    as "library_basic.db not found -- run import_csv_basic() first"

  use conn <- sqlight.with_connection(db_path)

  // These two go first because they cannot be run inside a transaction
  // Turn on foreign keys (they are off by default)
  use _ <- result.try(sqlight.exec("PRAGMA foreign_keys = ON;", conn))
  // Lets us read library_basic.db as `raw` from this connection
  use _ <- result.try(sqlight.exec(
    "ATTACH DATABASE '" <> raw_db_path <> "' AS raw;",
    conn,
  ))

  use _ <- result.try(run_statements(conn))

  list.each(tables, fn(table) {
    io.println(
      table <> ": " <> int.to_string(count_rows(conn, table)) <> " rows",
    )
  })

  Ok(Nil)
}
