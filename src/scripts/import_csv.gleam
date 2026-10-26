import gleam/dict.{type Dict}
import gleam/dynamic/decode
import gleam/int
import gleam/io
import gleam/list
import gleam/string
import gsv
import simplifile
import sqlight

// CONST VARS
// Relative to where you run `gleam run` (the project root), NOT to this file
const path = "src/SFPL_DataSF_library-usage_Jan_2023.csv"

const db_path = "library_basic.db"

// Every attribute of the CSV, IN CSV COLUMN ORDER, as #(name, type + constraints)

// Circulation Active Month/Year are blank in many rows, so they are the
// only two columns that allow NULL.
const columns = [
  #("patron_type_code", "INTEGER NOT NULL"),
  #("patron_type_definition", "VARCHAR(25) NOT NULL"),
  #("checkout_total", "INTEGER NOT NULL"),
  #("renewal_total", "INTEGER NOT NULL"),
  #("age_range", "VARCHAR(25) NOT NULL"),
  #("home_library_code", "VARCHAR(5) NOT NULL"),
  #("home_library_definition", "VARCHAR(25) NOT NULL"),
  // Play it safe with varchar
  #("circulation_active_month", "VARCHAR(10)"),
  #("circulation_active_year", "INTEGER"),
  #("notification_medium_code", "CHAR(1) NOT NULL"),
  #("notification_code_definition", "VARCHAR(25) NOT NULL"),
  #("provided_email_address", "VARCHAR(5) NOT NULL"),
  #("within_san_francisco_county", "VARCHAR(5) NOT NULL"),
  #("year_patron_registered", "INTEGER NOT NULL"),
]

// The same information as a Dict: attribute name -> type and constraints.
// (A Dict does not remember order, so the table is built from `columns`.)
pub fn column_definitions() -> Dict(String, String) {
  dict.from_list(columns)
}

// Turns a raw header cell from the CSV into a clean column name.
//   "Patron Type Code\r\npatron_type_code" -> "patron_type_code"  (name past newline is selected)
//   "Patron Type Definition\r\n"           -> "patron_type_definition"
//   "Within San Francisco County"          -> "within_san_francisco_county"
pub fn clean_name(header_cell: String) -> String {
  let lines =
    header_cell
    |> string.replace("\r", "")
    |> string.split("\n")
    |> list.map(string.trim)
    |> list.filter(fn(line) { line != "" })

  // The last non-empty line is either the name past the newline or the name
  let name = case list.last(lines) {
    Ok(line) -> line
    Error(Nil) -> panic as "A header cell in the CSV is empty"
  }

  // Make lowercase and replace spaces with underscores
  name
  |> string.lowercase
  |> string.replace(" ", "_")
}

// Builds: CREATE TABLE ... (TID ..., name type constraints, name type constraints, ...)
fn create_table_sql() -> String {
  // Append names and commands on subsequent columns first
  let column_sql =
    columns
    |> list.map(fn(column) {
      let #(name, definition) = column
      name <> " " <> definition
    })
    |> string.join(", ")

  // Return the command to create the table
  "CREATE TABLE IF NOT EXISTS UserTransactionRaw ("
  <> "TID INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
  <> column_sql
  <> ");"
}

// INSERT INTO UserTransactionRaw (name, name, ...) VALUES (?, ?, ...)
// Use placeholder `?`s, which get handled by insert_row() using to_value()
fn insert_sql() -> String {
  // No TID so that AUTOINCREMENT can fill in the value
  let names = list.map(columns, fn(column) { column.0 })
  let placeholders = list.map(columns, fn(_) { "?" })

  "INSERT INTO UserTransactionRaw ("
  <> string.join(names, ", ")
  <> ") VALUES ("
  <> string.join(placeholders, ", ")
  <> ");"
}

// Turns one CSV cell into the SQLite value that matches its column's type,
// Where type comes from the `columns` list:
//   INTEGER ...   -> sqlight.int      VARCHAR / CHAR ... -> sqlight.text
//   blank cell in a column that allows NULL -> sqlight.null
fn to_value(definition: String, cell: String) -> sqlight.Value {
  // Bools based on given definition
  let allows_null = !string.contains(definition, "NOT NULL")
  let is_integer = string.starts_with(definition, "INTEGER")

  // (The month column holds a single space when it is blank, hence trim)
  case allows_null && string.trim(cell) == "" {
    True -> sqlight.null()
    False ->
      // Check if integer
      case is_integer {
        True ->
          case int.parse(cell) {
            Ok(number) -> sqlight.int(number)
            Error(Nil) ->
              panic as {
                "Expected a whole number but found: \"" <> cell <> "\""
              }
          }
        False -> sqlight.text(cell)
      }
  }
}

// Inserts one CSV row (a list of cells, in column order) into the table
fn insert_row(conn: sqlight.Connection, sql: String, row: List(String)) {
  assert list.length(row) == list.length(columns)
    as "A CSV row does not have the same number of cells as there are columns"

  let values =
    list.zip(columns, row)
    |> list.map(fn(pair) {
      let #(column, cell) = pair
      to_value(column.1, cell)
    })

  // An INSERT returns no rows, so the decoder is never actually run
  let assert Ok(_) =
    sqlight.query(sql, on: conn, with: values, expecting: decode.success(Nil))
}

// Creates the raw table in SQLite and returns the cleaned attribute names
pub fn import_csv_basic() {
  let assert Ok(raw) = simplifile.read(path)

  // The file starts with an invisible UTF-8 byte-order mark (BOM) before the
  // first quote, which makes the parser fail (Enoent) -> strip it first
  let csv_content = string.replace(raw, "\u{FEFF}", "")

  // Parse it into a list of rows (each row is a list of strings)
  let assert Ok(rows) = gsv.to_lists(csv_content, ",")
  let header = case list.first(rows) {
    // The first row is the header row, i.e. the attribute names
    Ok(header) -> header
    Error(Nil) -> panic as "No entries found in CSV"
  }
  let attributes = list.map(header, clean_name)

  // Confirm that the CSV header matches the `columns` list
  let expected = list.map(columns, fn(column) { column.0 })
  assert attributes == expected
    as "CSV header does not match the `columns` list"

  // Create (OR SELECT) a table with Transaction ID & attributes
  use conn <- sqlight.with_connection(db_path)
  let assert Ok(Nil) = sqlight.exec(create_table_sql(), conn)

  // Insert the data into the table (in one transaction)
  let assert Ok(Nil) = sqlight.exec("BEGIN;", conn)
  // Clear table first (since sometimes we're reusing a db)
  let assert Ok(Nil) = sqlight.exec("DELETE FROM UserTransactionRaw;", conn)
  // Reset AUTOINCREMENT counters (ditto above)
  let assert Ok(Nil) =
    sqlight.exec(
      "DELETE FROM sqlite_sequence WHERE name = 'UserTransactionRaw';",
      conn,
    )

  // Everything after the header row is data
  let data_rows = list.drop(rows, 1)
  let sql = insert_sql()
  list.each(data_rows, fn(row) { insert_row(conn, sql, row) })

  let assert Ok(Nil) = sqlight.exec("COMMIT;", conn)
  io.println("Inserted " <> int.to_string(list.length(data_rows)) <> " rows")

  attributes
}
