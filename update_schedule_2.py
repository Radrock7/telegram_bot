from ics import Calendar, Event
from datetime import datetime, timezone, timedelta
import pytz  # or use zoneinfo in Python 3.9+

# Define Singapore timezone (UTC+8)
SGT = pytz.timezone("Asia/Singapore")  # or zoneinfo.ZoneInfo("Asia/Singapore") in Python 3.9+


def parse_selected_time(selected_time: str) -> tuple[datetime, datetime]:
    """Parse the selected time string into a start and end datetime in Singapore time."""
    date_str, time_range = selected_time.split(": ")
    start_time_str, end_time_str = time_range.split("-")
    
    # Parse the datetime and set it to Singapore timezone
    start_time = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=SGT)
    end_time = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=SGT)
    
    return start_time, end_time


def add_event_to_ics_contents(ics_contents, start_time, end_time, meeting_id):
    """Add the common slot as an event to the ICS contents."""
    updated_contents = []
    for ics_content in ics_contents:
        calendar = Calendar(ics_content)
        
        # Create a new event
        event = Event()
        event.name = "Scheduled Meeting"
        event.description = f"Teams Meeting ID: {meeting_id}"
        event.begin = start_time
        event.end = end_time
        
        # Add the event to the calendar
        calendar.events.add(event)
        
        # Save the updated calendar as a string
        updated_contents.append(str(calendar))
    
    return updated_contents
