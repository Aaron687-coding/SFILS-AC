from pymongo import MongoClient
from csv import DictReader
from itertools import batched
from typing import Any, Dict, List

BATCH_SIZE = 10_000
PATH = "../src/SFPL_DataSF_library-usage_Jan_2023.csv"
CONNECTION_STRING = "mongodb://localhost:27017/"
DATABASE_NAME = "library_database"

# Define the clean columns for the CSV file
columns = ["patron_type_code", "patron_type_definition", "checkout_total", 
           "renewal_total", "age_range", "home_library_code", 
           "home_library_definition", "circulation_active_month", "circulation_active_year",
           "notification_medium_code", "notification_code_definition", "provided_email_address", 
           "within_san_francisco_county", "year_patron_registered"]

header_types = [int, str, int, int ,str, str, str, str, str, str, str, bool, bool, int]

# Cleans the name by removing leading/trailing whitespace, replacing whitespace with underscores, and
# converting to lowercase.
def clean_name(name) -> str:
    lines = name.replace("\r", "").strip().split("\n")
    try:
        cleaned_lines = [line.strip().lower() for line in lines if line.strip()]
    except:
        raise ValueError("A header cell in the CSV is empty")

    return cleaned_lines[0].replace(" ",  "_")

# Constructs a dictionary from a row of values by matching them to the clean columns above
def construct_doc(row: List) -> Dict:
    doc = {}
    # Process each row value according to their type
    for i, column in enumerate(columns):
        # Int or string (convert without any special actions)
        if header_types[i] in [int, str]:
            doc[column] = header_types[i](row[i])
        # Boolean (convert to bool by comparing to "true" string)
        # (Don't allow for anything other than True or False)
        elif header_types[i] == bool and row[i].lower() in ["true", "false"]:
            doc[column[i]] = row[i].lower() == "true"
        else:
            raise ValueError("Unrecognized type in row \"" + column[i] + "\": " + header_types[i])
    return doc

# Bulk inserts rows into the database without structural changes to the data
def import_csv_basic(collection_str: str = "library_basic"):
    # Read the file into a string
    with open(PATH, "r", encoding="utf-8") as csv_content:
        csv_string = csv_content.read().replace("\uFEFF", "")

    # Split the string into rows
    rows = csv_string.splitlines()
    try:
        header = rows[0].split(",")
    except:
        raise ValueError("No entries found in CSV")

    # Confirm that the CSV header matches the 'columns' list
    if [clean_name(header[i])!= column for i, column in enumerate(columns)]:
        raise ValueError("CSV header does not match 'columns' list")

    # Connect to mongodb client
    client = MongoClient(CONNECTION_STRING)

    # Select the database and collection
    db = client[DATABASE_NAME]
    db.drop_collection(collection_str) # Clear collection first
    collection = db[collection_str]

    for batch in batched(rows, BATCH_SIZE):
        # Only 10,000 dicts are held in memory at a time
        # process(batch)
        # First make dicts out of each row
        row_dicts = [construct_doc(row.split(",")) for row in batch]
        # Insert batch of dicts into collection
        collection.insert_many(row_dicts, ordered=False) 

    # Close the client
    client.close()
    
    return None