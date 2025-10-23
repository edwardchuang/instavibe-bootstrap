from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response, stream_with_context
import json 
import traceback 
from introvertally import call_agent_for_plan, post_plan_event


# It's good practice to use a Blueprint for organizing routes
ally_bp = Blueprint('ally', __name__, template_folder='templates')

def get_all_people_for_ally_page():
    """
    Fetches all people from the Person table to be listed as friends.
    This function will be called from within a route, ensuring 'app' is loaded.
    """
    try:
        # Import here to avoid circular dependencies at module load time
        # and ensure app.py's db and run_query are initialized.
        from app import db as main_app_db, run_query as main_app_run_query
        # param_types might be needed if run_query is called with params
        # from google.cloud.spanner_v1 import param_types as main_app_param_types

        if not main_app_db:
            print("Error in ally_routes.get_all_people_for_ally_page: main_app_db is not available from app.py.")
            return [] # Return empty list if db connection failed

        sql = """
            SELECT person_id, name
            FROM Person
            ORDER BY name
        """
        fields = ["person_id", "name"]
        # The run_query function in your app.py uses the global 'db' from app.py
        people = main_app_run_query(sql, expected_fields=fields)
        return people
    except ImportError:
        print("ERROR in ally_routes.get_all_people_for_ally_page: Could not import db or run_query from app.py. Check app.py structure and execution.")
        return [] # Fallback to empty list
    except Exception as e:
        print(f"Error fetching people in ally_routes.get_all_people_for_ally_page: {e}")
        import traceback
        traceback.print_exc()
        return []

@ally_bp.route('/introvert-ally', methods=['GET'])
def introvert_ally_page():
    """Renders the Introvert Ally page."""
    print("--- DEBUG: introvert_ally_page route CALLED (ally_routes.py) ---")
    friends_list = get_all_people_for_ally_page()
    print(f"--- DEBUG: Friends data from DB for ally page: {friends_list} ---")
    if friends_list is None: # Should be an empty list on error from get_all_people_for_ally_page
        friends_list = []
        flash("Could not load the list of people from the database.", "warning")
    return render_template('introvert_ally.html', friends=friends_list, title="Introvert Ally Planner")


@ally_bp.route('/api/introvert-ally/submit', methods=['POST'])
def submit_introvert_ally_request():
    """Handles the submission of the Introvert Ally form."""
    if request.method == 'POST':
        date = request.form.get('event_date')
        location_preference = request.form.get('location') # Renamed for clarity
        selected_friend_names_list = request.form.getlist('selected_friends')

        # Basic validation
        if not date or not location_preference or not selected_friend_names_list:
            flash('Please select a date, enter a location, and choose at least one friend.', 'warning')
            return redirect(url_for('ally.introvert_ally_page'))

        # Store parameters in session for the SSE route
        session['ally_request_params'] = {
            "user_name": "Alice", # Hardcoded for now
            "planned_date": date,
            "location_n_perference": location_preference,
            "selected_friend_names_list": selected_friend_names_list
        }
        # Clear any old plan details
        session.pop('ally_plan_details', None)
        session.pop('ally_agent_thoughts', None) # This is now handled by SSE stream

        print(f"Introvert Ally Request Received, redirecting to review page for streaming:")
        print(f"  Date: {date}")
        print(f"  Location: {location_preference}")
        print(f"  Selected Friends: {selected_friend_names_list}")

        return redirect(url_for('ally.introvert_ally_review_page'))

    return redirect(url_for('ally.introvert_ally_page')) # Fallback redirect

@ally_bp.route('/introvert-ally/stream-plan')
def stream_introvert_ally_plan():
    ally_params = session.get('ally_request_params')
    if not ally_params:
        def error_stream():
            yield f"event: error\ndata: {json.dumps({'message': 'Missing plan parameters in session.'})}\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    def generate_stream():
        print(f"--- PY_SSE: generate_stream called for {ally_params.get('user_name', 'Unknown User')} ---")
        try:
            for event_data in call_agent_for_plan(
                user_name=ally_params['user_name'],
                planned_date=ally_params['planned_date'],
                location_n_perference=ally_params['location_n_perference'],
                selected_friend_names_list=ally_params['selected_friend_names_list']
            ):
                event_type = event_data.get("type", "thought") 
                # Ensure data is JSON serializable, especially for complex objects or None
                data_to_send = event_data.get("data")
                try:
                    data_payload = json.dumps(data_to_send)
                except TypeError as te:
                    print(f"!!! PY_SSE: TypeError serializing data for event '{event_type}': {te}. Data: {data_to_send} !!!")
                    # Fallback or skip this event if it's not critical, or send an error event
                    data_payload = json.dumps({"error": "Data serialization issue", "original_type": str(type(data_to_send))})
                    event_type = "thought_error" # Custom event type for this specific issue
                message_to_send = f"event: {event_type}\ndata: {data_payload}\n\n" # Moved outside the try-except for json.dumps
                print(f"--- PY_SSE: Yielding to client: event='{event_type}', data_preview='{data_payload[:100]}...' ---")
                yield message_to_send

                if event_type == "plan_complete" or event_type == "error": # Note: 'error' here is a custom event from call_agent_for_plan
                    session['ally_plan_details'] = data_to_send # Store original data, not json string
                    session.modified = True # Explicitly mark session as modified
                    print(f"--- PY_SSE: Plan generation finished with type: {event_type}. Stored in session. ---")
            
            print(f"--- PY_SSE: call_agent_for_plan loop finished normally. Yielding stream_end. ---")
            yield f"event: stream_end\ndata: {json.dumps({})}\n\n" # Ensure valid JSON for stream_end

        except Exception as e:
            print(f"!!! PY_SSE: EXCEPTION during generate_stream or from call_agent_for_plan: {str(e)} !!!")
            traceback.print_exc() # Print full traceback to server console
            error_payload_data = {
                "message": f"Server error during plan generation: {str(e)}",
                "raw_output": "Check server console logs for full traceback."
            }
            error_payload_json = json.dumps(error_payload_data)
            yield f"event: error\ndata: {error_payload_json}\n\n" # This is the SSE 'error' event type
            session['ally_plan_details'] = error_payload_data # Store error for potential page reload
            session.modified = True # Explicitly mark session as modified
        finally:
            print(f"--- PY_SSE: generate_stream function is ending. ---")       
    return Response(stream_with_context(generate_stream()), mimetype='text/event-stream')

@ally_bp.route('/introvert-ally/review', methods=['GET'])
def introvert_ally_review_page():
    plan_details = session.get('ally_plan_details')
    agent_thoughts = session.get('ally_agent_thoughts', [])

    # The page will initially load without plan_details if it's a new request.
    # JS will populate it. If plan_details exists, it means the stream finished
    # or the page was reloaded after completion.

    is_error_plan = isinstance(plan_details, dict) and "error" in plan_details

    return render_template('introvert_ally_review.html',
                           plan=plan_details,
                           thoughts=agent_thoughts, # This will be empty on initial load, populated by JS
                           is_error_plan=is_error_plan,
                           title="Review Introvert Ally Plan")

@ally_bp.route('/api/introvert-ally/confirm-plan', methods=['POST'])
def confirm_introvert_ally_plan():
    # Get plan from the hidden form field first
    confirmed_plan_json_str = request.form.get('confirmed_plan_json')
    edited_invite_message = request.form.get('edited_invite_message')
    
    print(f"--- [DEBUG] Confirming Plan ---")
    print(f"--- [DEBUG] confirmed_plan_json from form: {confirmed_plan_json_str[:200] if confirmed_plan_json_str else 'None'}...") # Log preview
    if confirmed_plan_json_str:
        print(f"--- [DEBUG] Raw 'confirmed_plan_json' from form (first 200 chars): {confirmed_plan_json_str[:200]}...")
    else:
        print(f"--- [DEBUG] Raw 'confirmed_plan_json' from form: None or Empty")
    print(f"--- [DEBUG] Edited invite message from form: {edited_invite_message}")

    confirmed_plan = None
    if confirmed_plan_json_str:
        try:
            confirmed_plan = json.loads(confirmed_plan_json_str)
            print(f"--- [DEBUG] Successfully parsed 'confirmed_plan_json' from form.")
        except json.JSONDecodeError as e:
            print(f"--- [DEBUG] JSONDecodeError when parsing 'confirmed_plan_json' from form: {e}")
            confirmed_plan = None # Ensure it's None if parsing fails

    if not confirmed_plan or (isinstance(confirmed_plan, dict) and "error" in confirmed_plan):
        print(f"--- [DEBUG] Plan is invalid or missing. Plan content: {confirmed_plan}")
        flash("Cannot confirm an invalid or missing plan.", "danger")
        return redirect(url_for('ally.introvert_ally_page'))

    if edited_invite_message and isinstance(confirmed_plan, dict):
        confirmed_plan['post_to_go_out'] = edited_invite_message
    
    # Prepare parameters for the post_plan_event function
    # Assuming 'Alice' is still the hardcoded user for now
    # In a real app, you'd get the logged-in user's name/ID
    # ally_request_params might be cleared if there was an error during plan gen and user reloaded review page
    # Let's try to get user_name from ally_request_params if it's still there, otherwise default.
    # The plan itself doesn't store the user_name.
    ally_req_params_session = session.get('ally_request_params', {})
    user_name_for_posting = ally_req_params_session.get('user_name', 'Alice') # Default to Alice

    session['ally_post_params'] = {
        "user_name": user_name_for_posting,
        "confirmed_plan": confirmed_plan,
        "edited_invite_message": edited_invite_message,
        "agent_session_user_id": str(user_name_for_posting) # Or a new UUID for this agent interaction
    }
    print(f"--- [DEBUG] Stored ally_post_params: {session['ally_post_params']}")

    # Clear the session variables related to plan generation
    session.pop('ally_plan_details', None) # This was for SSE state, less critical now for confirm
    session.pop('ally_agent_thoughts', None) # Also for SSE display
    # session.pop('ally_request_params', None) # Keep this for now, as user_name_for_posting uses it. Clear after post_status.
    session.modified = True
    print(f"--- [DEBUG] Cleared plan generation session variables. Redirecting to post_status_page. ---")
    # flash(f"Plan '{confirmed_plan.get('event_name', 'Unnamed Plan')}' confirmed. Now proceeding to post...", "info")
    return redirect(url_for('ally.introvert_ally_post_status_page'))

@ally_bp.route('/introvert-ally/post-status', methods=['GET'])
def introvert_ally_post_status_page():
    """Renders the page that will show the live status of event/post creation."""
    print(f"--- [DEBUG] Entered introvert_ally_post_status_page ---")
    print(f"--- [DEBUG] ally_post_params from session at post_status_page: {session.get('ally_post_params')}")
    # Parameters for post_plan_event are expected to be in session['ally_post_params']
    if not session.get('ally_post_params'):
        flash("No posting parameters found. Please confirm a plan first.", "warning")
        return redirect(url_for('ally.introvert_ally_page'))
    
    plan_name = session['ally_post_params'].get('confirmed_plan', {}).get('event_name', 'Your Plan')
    return render_template('introvert_ally_post_status.html', title=f"Posting Status for: {plan_name}")

@ally_bp.route('/introvert-ally/stream-post-status')
def stream_post_status():
    post_params = session.get('ally_post_params')
    if not post_params:
        def error_stream():
            yield f"event: error\ndata: {json.dumps({'message': 'Missing posting parameters in session.'})}\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    def generate_post_stream():
        print(f"--- PY_SSE (Post Status): Starting event/post creation for {post_params['user_name']} ---")
        for event_data in post_plan_event( # Calling with positional arguments
            post_params['user_name'],
            post_params['confirmed_plan'],
            post_params['edited_invite_message'],
            post_params['agent_session_user_id']
        ):
            event_type = event_data.get("type", "thought")
            data_payload = json.dumps(event_data.get("data"))
            yield f"event: {event_type}\ndata: {data_payload}\n\n"
        
        # After the generator finishes
        print(f"--- PY_SSE (Post Status): post_plan_event finished. Setting flash message. ---")
        flash(f"Event '{post_params.get('confirmed_plan',{}).get('event_name','Unknown Event')}' and post creation process finished!", "success")
        session.pop('ally_post_params', None) # Clean up session
        yield f"event: stream_end\ndata: {json.dumps({})}\n\n"

    return Response(stream_with_context(generate_post_stream()), mimetype='text/event-stream')
    
