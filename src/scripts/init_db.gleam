// import gleam/io
import gleam/list

// Used to map rows back to Gleam types
import sqlight

// Table schemas:
// PatronType:        (Patron_Type_Code (int), Age Range ("0 to 9 years")) -> Patron Type Definition ("Juvenile")
// HomeLibType:       Home Library Code  (string, "r3")  ->  Home Library Definition  ("Richmond")
// NotificationType:  notification_medium_code (char, 'z') -> Notification Code Definition ("Email")

// Non-related values(all determined by Transaction ID (TID)):
// checkout_total, renewal_total, Within San Francisco (bool), Year Patron Registered (string),
// Included (but determinant): Patron Type Code (int), Home Library Code, notification_medium_code (char)

// Not included:  Circulation Active Month (empty?), Circulation Active Year (empty?)

// Create the tables of the database first
pub fn create_db() {
  use conn <- sqlight.with_connection("library.db")
  let table_ops = [
    // SQLite does NOT enforce foreign keys unless this is switched on
    // (it has to be set on every connection).
    sqlight.exec("PRAGMA foreign_keys = ON;", conn),

    // Create the lookup tables first, since UserTransaction refers to them.
    // Create the PatronType table
    // The key is (code, age range) because "Digital Access Card" & "Teacher Card"
    // (and in fact most patron types) appear with multiple age ranges.
    // Some rows in the data have an empty age range, so '' is a legal value.
    sqlight.exec(
      "CREATE TABLE IF NOT EXISTS PatronType ("
        <> "patron_type_code INTEGER NOT NULL, "
        <> "age_range VARCHAR(25) NOT NULL, "
        <> "patron_type_definition VARCHAR(25) NOT NULL, "
        <> "PRIMARY KEY (patron_type_code, age_range) "
        <> ");",
      conn,
    ),

    // Create the HomeLibType table
    sqlight.exec(
      "CREATE TABLE IF NOT EXISTS HomeLibType ("
        <> "home_library_code VARCHAR(5) NOT NULL, "
        <> "home_library_name VARCHAR(25) NOT NULL, "
        <> "PRIMARY KEY (home_library_code) "
        <> ");",
      conn,
    ),

    // Create the NotificationType table
    sqlight.exec(
      "CREATE TABLE IF NOT EXISTS NotificationType ("
        <> "notification_medium_code CHAR(1) NOT NULL, "
        <> "notification_medium_definition VARCHAR(25) NOT NULL, "
        <> "PRIMARY KEY (notification_medium_code) "
        <> ");",
      conn,
    ),

    // Create the UserTransaction table (one row per row of the spreadsheet)
    sqlight.exec(
      "CREATE TABLE IF NOT EXISTS UserTransaction ("
        <> "TID INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
        // Lower two seem like zero but we'll query and check
        <> "checkout_total INTEGER NOT NULL, "
        <> "renewal_total INTEGER NOT NULL, "
        // SQLite has no real boolean type: 0 = False, 1 = True
        <> "within_sf INTEGER NOT NULL CHECK (within_sf IN (0, 1)), "
        <> "year_registered INTEGER NOT NULL, "
        <> "patron_type_code INTEGER NOT NULL, "
        <> "age_range VARCHAR(25) NOT NULL, "
        <> "home_library_code VARCHAR(5) NOT NULL, "
        <> "notification_medium_code CHAR(1) NOT NULL, "
        // Relationships to the lookup tables
        <> "FOREIGN KEY (patron_type_code, age_range) "
        <> "REFERENCES PatronType (patron_type_code, age_range), "
        <> "FOREIGN KEY (home_library_code) "
        <> "REFERENCES HomeLibType (home_library_code), "
        <> "FOREIGN KEY (notification_medium_code) "
        <> "REFERENCES NotificationType (notification_medium_code) "
        <> ");",
      conn,
    ),
  ]

  // Stops at the first failing statement and crashes with its error
  let assert Ok(Nil) = list.try_each(table_ops, fn(res) { res })

  Ok(Nil)
}
// pub fn main() {
//   create_db()
//   io.println("Database ready to receive text commands!")
// }
