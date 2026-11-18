from itertools import batched

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

CONNECTION_STRING = "mongodb://localhost:27017/"

# Define the clean columns for the CSV file
COLUMNS = [
    "patron_type_code", 
    "patron_type_definition", 
    "checkout_total", 
    "renewal_total", 
    "age_range", 
    "home_library_code", 
    "home_library_definition", 
    "circulation_active_month", 
    "circulation_active_year",
    "notification_medium_code", 
    "notification_code_definition", 
    "provided_email_address", 
    "within_san_francisco_county", 
    "year_patron_registered"
]

# Doc template that maps each param to the raw param they take from
DOC_TEMPLATE = {
    "_id": "_id",
    "patron": {"code": "patron_type_code", "type": "patron_type_definition", "age_range": "age_range"},
    "library": {"code": "home_library_code", "name": "home_library_definition"},   # name trimmed
    "notification": {"code": "notification_medium_code", "medium": "notification_code_definition"},
    "checkouts": "checkout_total",
    "renewals": "renewal_total",
    "within_sf": "within_san_francisco_county",                               # real booleans, not "True"
    "year_registered": "year_patron_registered",
    "circulation_active": {"month": "circulation_active_month", "year": "circulation_active_year"},  # leave out when blank
    "email_provided": "provided_email_address"
}

def insert_docs(to_c: Collection, from_c: Collection):
    pass

# Init-import database function
def init_db():
    # 1. Connect to MongoDB
    # For local instance: "mongodb://localhost:27017/"

    client = MongoClient(CONNECTION_STRING)

    # Access/Create Database and Collection
    db = client["library_database"]
    collection = db["library"]

    insert_docs()

    # CREATE: Insert documents (represented as Python dictionaries)
    
    # insert_result = collection.insert_one(user_profile)
    # print(f"Inserted document ID: {insert_result.inserted_id}")

    # 4. READ: Find a single document
    #query = {"name": "Alice Smith"}
    #user = collection.find_one(query)
    #print("Found user:", user)

    # 5. UPDATE: Modify fields in a document
    #collection.update_one(
    #    {"name": "Alice Smith"},
    #    {"$set": {"age": 31}}
    #)

    # 6. DELETE: Remove a document
    # collection.delete_one({"name": "Alice Smith"})

    # Always close the client connection when finished
    client.close()
