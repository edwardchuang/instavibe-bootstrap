# spanner_data_fetchers.py

import os
from dotenv import load_dotenv
import traceback
from datetime import datetime, timezone
import json # For example usage printing

from google.cloud import spanner
from google.cloud.spanner_v1 import param_types
from google.api_core import exceptions

load_dotenv()
# --- Spanner Configuration ---
INSTANCE_ID = os.environ.get("SPANNER_INSTANCE_ID", "instavibe-graph-instance")
DATABASE_ID = os.environ.get("SPANNER_DATABASE_ID", "graphdb")
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")

if not PROJECT_ID:
    print("Warning: GOOGLE_CLOUD_PROJECT environment variable not set.")

# --- Spanner Client Initialization ---
db_instance = None
spanner_client = None
try:
    if PROJECT_ID:
        spanner_client = spanner.Client(project=PROJECT_ID)
        instance = spanner_client.instance(INSTANCE_ID)
        database = instance.database(DATABASE_ID)
        print(f"Attempting to connect to Spanner: {instance.name}/databases/{database.name}")

        if not database.exists():
             print(f"Error: Database '{database.name}' does not exist in instance '{instance.name}'.")
             db_instance = None
        else:
            print("Spanner database connection check successful.")
            db_instance = database
    else:
        print("Skipping Spanner client initialization due to missing GOOGLE_CLOUD_PROJECT.")

except exceptions.NotFound:
    print(f"Error: Spanner instance '{INSTANCE_ID}' not found in project '{PROJECT_ID}'.")
    db_instance = None
except Exception as e:
    print(f"An unexpected error occurred during Spanner initialization: {e}")
    db_instance = None

def run_sql_query(sql, params=None, param_types=None, expected_fields=None):
    """
    Executes a standard SQL query against the Spanner database.
    Returns: list[dict] or None on error.
    """
    if not db_instance:
        print("Error: Database connection is not available.")
        return None

    results_list = []
    print(f"--- Executing SQL Query ---")
    # print(f"SQL: {sql}")

    try:
        with db_instance.snapshot() as snapshot:
            results = snapshot.execute_sql(
                sql,
                params=params,
                param_types=param_types
            )

            field_names = expected_fields
            if not field_names:
                 print("Error: expected_fields must be provided to run_sql_query.")
                 return None

            for row in results:
                if len(field_names) != len(row):
                     print(f"Warning: Mismatch between field names ({len(field_names)}) and row values ({len(row)}). Skipping row: {row}")
                     continue
                results_list.append(dict(zip(field_names, row)))

    except (exceptions.NotFound, exceptions.PermissionDenied, exceptions.InvalidArgument) as spanner_err:
        print(f"Spanner SQL Query Error ({type(spanner_err).__name__}): {spanner_err}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during SQL query execution or processing: {e}")
        traceback.print_exc()
        return None

    return results_list


def run_graph_query( graph_sql, params=None, param_types=None, expected_fields=None):
    """
    Executes a Spanner Graph Query (GQL).
    Returns: list[dict] or None on error.
    """
    if not db_instance:
        print("Error: Database connection is not available.")
        return None

    results_list = []
    print(f"--- Executing Graph Query ---")
    # print(f"GQL: {graph_sql}") # Uncomment for verbose query logging

    try:
        with db_instance.snapshot() as snapshot:
            results = snapshot.execute_sql(
                graph_sql,
                params=params,
                param_types=param_types
            )

            field_names = expected_fields
            if not field_names:
                 print("Error: expected_fields must be provided to run_graph_query.")
                 return None

            for row in results:
                if len(field_names) != len(row):
                     print(f"Warning: Mismatch between field names ({len(field_names)}) and row values ({len(row)}). Skipping row: {row}")
                     continue
                results_list.append(dict(zip(field_names, row)))

    except (exceptions.NotFound, exceptions.PermissionDenied, exceptions.InvalidArgument) as spanner_err:
        print(f"Spanner Graph Query Error ({type(spanner_err).__name__}): {spanner_err}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during graph query execution or processing: {e}")
        traceback.print_exc()
        return None

    return results_list

def get_person_attended_events(person_id: str)-> list[dict]:
    """
    Fetches events attended by a specific person using Graph Query.
    Args:
       person_id (str): The ID of the person whose posts to fetch.
    Returns: list[dict] or None.
    """
    if not db_instance: return None

    graph_sql = """
        Graph SocialGraph
        MATCH (p:Person)-[att:Attended]->(e:Event)
        WHERE p.person_id = @person_id
        RETURN e.event_id, e.name, e.event_date, att.attendance_time
        ORDER BY e.event_date DESC
    """
    params = {"person_id": person_id}
    param_types_map = {"person_id": param_types.STRING}
    fields = ["event_id", "name", "event_date", "attendance_time"]

    results = run_graph_query( graph_sql, params=params, param_types=param_types_map, expected_fields=fields)

    if results is None: return None

    for event in results:
        if isinstance(event.get('event_date'), datetime):
            event['event_date'] = event['event_date'].isoformat()
        if isinstance(event.get('attendance_time'), datetime):
            event['attendance_time'] = event['attendance_time'].isoformat()
    return results

def get_person_id_by_name( name: str) -> str:
    """
    Fetches the person_id for a given name using SQL.

    Args:
       name (str): The name of the person to search for.

    Returns:
        str or None: The person_id if found, otherwise None.
                     Returns the ID of the *first* match if names are duplicated.
    """
    if not db_instance: return None

    sql = """
        SELECT person_id
        FROM Person
        WHERE name = @name
        LIMIT 1 -- Return only the first match in case of duplicate names
    """
    params = {"name": name}
    param_types_map = {"name": param_types.STRING}
    fields = ["person_id"]

    # Use the standard SQL query helper
    results = run_sql_query( sql, params=params, param_types=param_types_map, expected_fields=fields)

    if results: # Check if the list is not empty
        return results[0].get('person_id') # Return the ID from the first dictionary
    else:
        return None # Name not found


def get_person_posts( person_id: str)-> list[dict]:
    """
    Fetches posts written by a specific person using Graph Query.

    Args:
        person_id (str): The ID of the person whose posts to fetch.


    Returns:
        list[dict] or None: List of post dictionaries with ISO date strings,
                           or None if an error occurs.
    """
    if not db_instance: return None

    # Graph Query: Find the specific Person node, follow 'Wrote' edge to Post nodes
    graph_sql = """
        Graph SocialGraph
        MATCH (author:Person)-[w:Wrote]->(post:Post)
        WHERE author.person_id = @person_id
        RETURN post.post_id, post.author_id, post.text, post.sentiment, post.post_timestamp, author.name AS author_name
        ORDER BY post.post_timestamp DESC
    """
    # Parameters now include person_id and limit
    params = {
        "person_id": person_id
    }
    param_types_map = {
        "person_id": param_types.STRING
    }
    # Fields returned remain the same
    fields = ["post_id", "author_id", "text", "sentiment", "post_timestamp", "author_name"]

    results = run_graph_query(graph_sql, params=params, param_types=param_types_map, expected_fields=fields)

    if results is None:
        return None

    # Convert datetime objects to ISO format strings
    for post in results:
        if isinstance(post.get('post_timestamp'), datetime):
            post['post_timestamp'] = post['post_timestamp'].isoformat()

    return results


def get_person_friends( person_id: str)-> list[dict]:
    """
    Fetches friends for a specific person using Graph Query.
    Args:
        person_id (str): The ID of the person whose posts to fetch.
    Returns: list[dict] or None.
    """
    if not db_instance: return None

    graph_sql = """
        Graph SocialGraph
        MATCH (p:Person {person_id: @person_id})-[f:Friendship]-(friend:Person)
        RETURN DISTINCT friend.person_id, friend.name
        ORDER BY friend.name
    """
    params = {"person_id": person_id}
    param_types_map = {"person_id": param_types.STRING}
    fields = ["person_id", "name"]

    results = run_graph_query( graph_sql, params=params, param_types=param_types_map, expected_fields=fields)

    return results