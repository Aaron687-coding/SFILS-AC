from pymongo import MongoClient
from itertools import batched


# Define the clean columns for the CSV file
columns = ["patron_type_code", "patron_type_definition", "checkout_total", 
           "renewal_total", "age_range", "home_library_code", 
           "home_library_definition", "circulation_active_month", "circulation_active_year",
           "notification_medium_code", "notification_code_definition", "provided_email_address", 
           "within_san_francisco_county", "year_patron_registered"]

# Init-import database function
def init_db():
    # 1. Connect to MongoDB
    # For local instance: "mongodb://localhost:27017/"
    CONNECTION_STRING = "mongodb://localhost:27017/"
    client = MongoClient(CONNECTION_STRING)

    # Access/Create Database and Collection
    db = client["library_database"]
    collection = db["library_basic"]

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
