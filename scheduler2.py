#!/usr/bin/env python3
from ics import Calendar
from datetime import datetime, date, time, timedelta

def load_calendar_from_content(ics_content):
    """Load a calendar from ICS content (string) and return its events."""
    calendar = Calendar(ics_content)
    return list(calendar.events)

def events_for_day(events, day, tzinfo):
    """Return event intervals for a given day (clipped to working hours)."""
    work_start = datetime.combine(day, time(9, 0, tzinfo=tzinfo))
    work_end = datetime.combine(day, time(17, 0, tzinfo=tzinfo))
    day_events = []
    for event in events:
        # Convert event times to the common timezone.
        event_start = event.begin.astimezone(tzinfo)
        event_end   = event.end.astimezone(tzinfo)
        if event_end > work_start and event_start < work_end:
            start = max(event_start, work_start)
            end = min(event_end, work_end)
            day_events.append((start, end))
    # Merge overlapping events.
    day_events.sort(key=lambda x: x[0])
    merged = []
    for interval in day_events:
        if not merged:
            merged.append(interval)
        else:
            last_start, last_end = merged[-1]
            current_start, current_end = interval
            if current_start <= last_end:
                merged[-1] = (last_start, max(last_end, current_end))
            else:
                merged.append(interval)
    return merged

def free_intervals_for_day(events, day, tzinfo, current_dt=None):
    """Return free intervals (as tuples of datetime) for a given day."""
    work_start = datetime.combine(day, time(9, 0, tzinfo=tzinfo))
    work_end = datetime.combine(day, time(17, 0, tzinfo=tzinfo))
    busy = events_for_day(events, day, tzinfo)
    
    free = []
    if not busy:
        free.append((work_start, work_end))
    else:
        if work_start < busy[0][0]:
            free.append((work_start, busy[0][0]))
        for i in range(len(busy)-1):
            if busy[i][1] < busy[i+1][0]:
                free.append((busy[i][1], busy[i+1][0]))
        if busy[-1][1] < work_end:
            free.append((busy[-1][1], work_end))
    
    if current_dt and day == current_dt.date():
        new_free = []
        for start, end in free:
            if current_dt > start:
                start = max(start, current_dt)
            if start < end:
                new_free.append((start, end))
        free = new_free

    return free

def candidate_from_interval(interval, meeting_td):
    """Return a centrally placed meeting slot within the free interval."""
    start, end = interval
    interval_td = end - start
    if interval_td < meeting_td:
        return None
    slack = interval_td - meeting_td
    candidate_start = start + slack / 2
    candidate_end = candidate_start + meeting_td

    # Round to nearest 5 minutes
    rounded_minute = (candidate_start.minute // 5) * 5
    if candidate_start.minute % 5 >= 2.5:
        rounded_minute += 5
    if rounded_minute >= 60:
        rounded_minute = 0
        candidate_start += timedelta(hours=1)
    candidate_start = candidate_start.replace(minute=rounded_minute, second=0, microsecond=0)

    candidate_end = candidate_start + meeting_td

    if candidate_start < start or candidate_end > end:
        return None

    score = slack.total_seconds() / 2
    return (score, candidate_start, candidate_end)

def infer_working_week(all_events):
    """Infer the working week (Monday to Friday) from events."""
    if all_events:
        min_event_date = min(event.begin.date() for event in all_events)
        monday = min_event_date - timedelta(days=min_event_date.weekday())
    else:
        current_dt = datetime.now().astimezone()
        monday = current_dt.date() - timedelta(days=current_dt.weekday())
    working_week_start = monday
    working_week_end = monday + timedelta(days=4)
    return working_week_start, working_week_end

def get_scheduling_days(all_events, current_dt):
    """Return a list of scheduling days based on the working week."""
    working_week_start, working_week_end = infer_working_week(all_events)
    if working_week_start <= current_dt.date() <= working_week_end:
        days_count = (working_week_end - current_dt.date()).days + 1
        scheduling_days = [current_dt.date() + timedelta(days=i) for i in range(days_count)]
    else:
        scheduling_days = [working_week_start + timedelta(days=i) for i in range(5)]
    return scheduling_days

def find_best_meeting_slots(ics_contents, meeting_duration_minutes, max_slots=10):
    """Find the best meeting slots by loading events from multiple ICS contents."""
    all_events = []
    for ics_content in ics_contents:
        events = load_calendar_from_content(ics_content)
        all_events.extend(events)

    meeting_td = timedelta(minutes=meeting_duration_minutes)
    current_dt = datetime.now().astimezone()
    tzinfo = current_dt.tzinfo

    scheduling_days = get_scheduling_days(all_events, current_dt)

    candidate_slots = []
    for day in scheduling_days:
        free_intervals = free_intervals_for_day(all_events, day, tzinfo, current_dt if day == current_dt.date() else None)
        for interval in free_intervals:
            candidate = candidate_from_interval(interval, meeting_td)
            if candidate:
                score, cand_start, cand_end = candidate
                candidate_slots.append((score, cand_start, cand_end))

    candidate_slots.sort(key=lambda x: x[0], reverse=True)
    best_slots = candidate_slots[:max_slots]

    formatted_slots = []
    for score, start, end in best_slots:
        slot_str = f"{start.strftime('%Y-%m-%d')}: {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
        formatted_slots.append(slot_str)
    return formatted_slots

#Usage: 
# cal_files = ["sample1.ics", "sample2.ics", "sample3.ics"]
# meeting_duration_minutes = 30
# best_slots = find_best_meeting_slots(cal_files, meeting_duration_minutes)
# for slot in best_slots:
#     print(slot)
