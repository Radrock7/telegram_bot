import os
import logging
import sqlite3
import io
import re
from datetime import datetime
from ics import Calendar
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler,
)
from openai import OpenAI
import scheduler2  # For finding free slots
import update_schedule_2  # For updating ICS files

# Load environment variables
load_dotenv()
# Set your OpenAI API key from the .env file

# Initialize SQLite database 
conn = sqlite3.connect('meetings.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS meetings
             (meeting_id TEXT PRIMARY KEY,
              user_a_schedule TEXT,
              user_b_schedule TEXT,
              user_a_id TEXT,
              user_b_id TEXT,
              meeting_duration INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_choices
             (meeting_id TEXT, user_id TEXT, choices TEXT)''')
conn.commit()

# Bot token from .env or fallback
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Helper function to validate meeting IDs (format: xxx xxx xxx xxx)
def valid_meeting_id(meeting_id: str) -> bool:
    pattern = r"^\d{3}( \d{3}){3}$"
    return re.match(pattern, meeting_id) is not None

# /newmeeting command handler: Clears any previous state and asks for a meeting ID.
async def start_new_meeting(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text(
        "Please send your unique Meeting ID in the format: xxx xxx xxx xxx (e.g., '234 242 667 442') to initiate a new meeting."
    )

# This function handles the first text input (meeting ID) for new meeting creation.
async def store_meeting_id(update: Update, context: CallbackContext):
    meeting_id = update.message.text.strip()
    if not valid_meeting_id(meeting_id):
        await update.message.reply_text(
            "Invalid Meeting ID format. Please enter in the format: xxx xxx xxx xxx (e.g., '234 242 667 442')."
        )
        return

    # Check if the meeting ID already exists.
    c.execute("SELECT meeting_id FROM meetings WHERE meeting_id=?", (meeting_id,))
    if c.fetchone() is not None:
        await update.message.reply_text(
            "Meeting ID already exists. Please enter a different meeting ID."
        )
        return

    user_id = update.message.from_user.id
    # Insert the meeting with user_a_id (the meeting creator)
    c.execute("INSERT OR IGNORE INTO meetings (meeting_id, user_a_id) VALUES (?, ?)", (meeting_id, user_id))
    conn.commit()

    # Store the meeting id and signal that we are now waiting for the meeting duration.
    context.user_data['current_meeting'] = meeting_id
    context.user_data['awaiting_duration'] = True

    await update.message.reply_text(
        f"Meeting ID {meeting_id} stored. Please enter the meeting duration in minutes."
    )

# This function processes the meeting duration input.
async def store_meeting_duration(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "Invalid duration. Please enter a positive integer representing the meeting duration in minutes."
        )
        return

    duration = int(text)
    if duration <= 0:
        await update.message.reply_text("Meeting duration must be greater than zero.")
        return

    meeting_id = context.user_data.get("current_meeting")
    if not meeting_id:
        await update.message.reply_text("No meeting ID found. Please start a new meeting.")
        return

    # Update the meeting record with the meeting duration.
    c.execute("UPDATE meetings SET meeting_duration=? WHERE meeting_id=?", (duration, meeting_id))
    conn.commit()
    # Remove the awaiting_duration flag.
    context.user_data.pop("awaiting_duration", None)

    await update.message.reply_text(
        f"Meeting duration set to {duration} minutes. Please upload your .ics file."
    )

# Unified text handler to route between meeting ID and meeting duration input.
async def handle_text(update: Update, context: CallbackContext):
    if context.user_data.get("awaiting_duration"):
        await store_meeting_duration(update, context)
    else:
        await store_meeting_id(update, context)

# Handler for .ics file uploads.
async def handle_ics_upload(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    meeting_id = context.user_data.get('current_meeting')
    if not meeting_id:
        await update.message.reply_text("No active meeting found. Please start a new meeting.")
        return

    # Enforce that meeting duration has been provided.
    if context.user_data.get("awaiting_duration"):
        await update.message.reply_text("Please enter the meeting duration in minutes before uploading your .ics file.")
        return

    ics_file = await update.message.document.get_file()
    ics_content_bytes = await ics_file.download_as_bytearray()
    calendar_content = ics_content_bytes.decode()

    c.execute(
        "SELECT user_a_id, user_b_id, user_a_schedule, user_b_schedule, meeting_duration FROM meetings WHERE meeting_id=?",
        (meeting_id,)
    )
    meeting_data = c.fetchone()
    if not meeting_data:
        await update.message.reply_text("Meeting not found in the database. Please check your meeting ID.")
        return

    user_a_id, user_b_id, user_a_schedule, user_b_schedule, meeting_duration = meeting_data

    if int(user_a_id) == user_id:
        if user_a_schedule is None:
            c.execute("UPDATE meetings SET user_a_schedule=? WHERE meeting_id=?", (calendar_content, meeting_id))
            await update.message.reply_text("Your schedule has been stored. Waiting for the other user.")
        else:
            await update.message.reply_text("Your schedule has already been stored. Waiting for the other user.")
    else:
        if user_b_schedule is None:
            c.execute("UPDATE meetings SET user_b_schedule=? WHERE meeting_id=?", (calendar_content, meeting_id))
            await update.message.reply_text("Your schedule has been stored. Waiting for the other user.")
        else:
            await update.message.reply_text("Your schedule has already been stored. Waiting for the other user.")

    conn.commit()

    c.execute(
        "SELECT user_a_schedule, user_b_schedule, user_a_id, user_b_id, meeting_duration FROM meetings WHERE meeting_id=?",
        (meeting_id,)
    )
    row = c.fetchone()
    if row:
        user_a_schedule, user_b_schedule, user_a_id, user_b_id, meeting_duration = row
        if meeting_duration is None:
            await update.message.reply_text("Meeting duration not set. Please enter the meeting duration first.")
            return
        if user_a_schedule and user_b_schedule:
            await context.bot.send_message(user_a_id, "Both schedules have been uploaded. Calculating free slots...")
            await context.bot.send_message(user_b_id, "Both schedules have been uploaded. Calculating free slots...")
            ics_contents = [user_a_schedule, user_b_schedule]
            common_slots = scheduler2.find_best_meeting_slots(ics_contents, meeting_duration_minutes=meeting_duration)
            if common_slots:
                # Cache the computed common slots for this meeting.
                context.bot_data[meeting_id] = common_slots
                keyboard = [
                    [InlineKeyboardButton(slot, callback_data=f"{meeting_id}|{slot}")]
                    for slot in common_slots
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(user_a_id, "Common free slots:", reply_markup=reply_markup)
                await context.bot.send_message(user_b_id, "Common free slots:", reply_markup=reply_markup)
            else:
                await context.bot.send_message(user_a_id, "No common free slots found.")
                await context.bot.send_message(user_b_id, "No common free slots found.")

# /join command handler for participants joining an existing meeting.
async def join_meeting(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Please provide a meeting ID to join, e.g., /join 234 242 667 442")
        return

    meeting_id = " ".join(context.args).strip()
    if not valid_meeting_id(meeting_id):
        await update.message.reply_text("Invalid Meeting ID format. Please use the format: xxx xxx xxx xxx.")
        return

    user_id = update.message.from_user.id
    c.execute("SELECT meeting_id, user_b_id FROM meetings WHERE meeting_id=?", (meeting_id,))
    meeting_data = c.fetchone()
    if not meeting_data:
        await update.message.reply_text("Invalid Meeting ID. Please check and try again.")
        return

    existing_user_b_id = meeting_data[1]
    if existing_user_b_id:
        await update.message.reply_text("This meeting already has a second participant.")
        return

    c.execute("UPDATE meetings SET user_b_id=? WHERE meeting_id=?", (user_id, meeting_id))
    conn.commit()
    context.user_data['current_meeting'] = meeting_id
    await update.message.reply_text(f"Joined meeting {meeting_id}. Please upload your .ics file.")

# Handler for slot selection via inline buttons with a "Select All" option.
async def slot_selection(update: Update, context: CallbackContext):
    query = update.callback_query
    meeting_id, chosen_slot = query.data.split("|", 1)
    user_id = query.from_user.id

    # Retrieve the cached common slots (or recalc if missing)
    common_slots = context.bot_data.get(meeting_id)
    if not common_slots:
        c.execute("SELECT user_a_schedule, user_b_schedule, meeting_duration FROM meetings WHERE meeting_id=?", (meeting_id,))
        row = c.fetchone()
        if not row:
            await query.answer("Meeting data not found.")
            return
        user_a_schedule, user_b_schedule, meeting_duration = row
        if not meeting_duration:
            meeting_duration = 30
        ics_contents = [user_a_schedule, user_b_schedule]
        common_slots = scheduler2.find_best_meeting_slots(ics_contents, meeting_duration_minutes=meeting_duration)
        context.bot_data[meeting_id] = common_slots

    # Ensure we have a dictionary in context.user_data for this meeting.
    if meeting_id not in context.user_data:
        context.user_data[meeting_id] = {}
    if user_id not in context.user_data[meeting_id]:
        context.user_data[meeting_id][user_id] = set()
    selected_slots = context.user_data[meeting_id][user_id]

    # Handle the "Select All" button.
    if chosen_slot == "select_all":
        if set(common_slots) != selected_slots:
            context.user_data[meeting_id][user_id] = set(common_slots)
        else:
            context.user_data[meeting_id][user_id].clear()
    else:
        # Toggle individual slot selection.
        if chosen_slot in selected_slots:
            selected_slots.remove(chosen_slot)
        else:
            selected_slots.add(chosen_slot)

    # Rebuild the keyboard for each free slot.
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if slot in context.user_data[meeting_id][user_id] else ''}{slot}",
                                callback_data=f"{meeting_id}|{slot}")]
        for slot in common_slots
    ]
    # Add a "Select All"/"Deselect All" button.
    if set(common_slots) == context.user_data[meeting_id][user_id]:
        select_all_text = "Deselect All"
    else:
        select_all_text = "Select All"
    keyboard.append([InlineKeyboardButton(select_all_text, callback_data=f"{meeting_id}|select_all")])
    # Add a "Submit" button if any slot is selected.
    if context.user_data[meeting_id][user_id]:
        keyboard.append([InlineKeyboardButton("Submit", callback_data=f"submit|{meeting_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_reply_markup(reply_markup=reply_markup)
    await query.answer()

# Handler for submission of slot selections.
async def submit_selection(update: Update, context: CallbackContext):
    query = update.callback_query
    action, meeting_id = query.data.split("|", 1)
    user_id = query.from_user.id
    selected_slots = context.user_data.get(meeting_id, {}).get(user_id, set())
    if not selected_slots:
        await query.answer("No slots selected. Please select at least one slot.")
        return

    c.execute(
        "INSERT OR REPLACE INTO user_choices (meeting_id, user_id, choices) VALUES (?, ?, ?)",
        (meeting_id, user_id, ", ".join(selected_slots))
    )
    conn.commit()
    await query.edit_message_text("Your selections have been submitted. Please wait for the other user.")
    await query.answer("Your selections have been submitted.")

    c.execute("SELECT user_id, choices FROM user_choices WHERE meeting_id=?", (meeting_id,))
    choices = c.fetchall()
    if len(choices) == 2:
        user_a_choices = choices[0][1].split(", ") if choices[0][1] else []
        user_b_choices = choices[1][1].split(", ") if choices[1][1] else []
        common_slots_set = set(user_a_choices).intersection(user_b_choices)
        if common_slots_set:
            common_slot = common_slots_set.pop()
            user_id_a = choices[0][0]
            user_id_b = choices[1][0]
            await context.bot.send_message(user_id_a, f"Common slot found: {common_slot}. Adding it to your calendar.")
            await context.bot.send_message(user_id_b, f"Common slot found: {common_slot}. Adding it to your calendar.")
            start_time, end_time = update_schedule_2.parse_selected_time(common_slot)
            c.execute(
                "SELECT user_a_schedule, user_b_schedule, user_a_id, user_b_id FROM meetings WHERE meeting_id=?",
                (meeting_id,)
            )
            row = c.fetchone()
            if row:
                user_a_schedule, user_b_schedule, user_a_id, user_b_id = row
                updated_ics_contents = update_schedule_2.add_event_to_ics_contents(
                    [user_a_schedule, user_b_schedule],
                    start_time,
                    end_time,
                    meeting_id
                )
                for uid, ics_content in zip([user_a_id, user_b_id], updated_ics_contents):
                    file_obj = io.BytesIO(ics_content.encode())
                    file_obj.name = "updated_schedule.ics"
                    await context.bot.send_document(uid, document=file_obj, caption="Here is your updated schedule.")
            else:
                await context.bot.send_message(user_id_a, "Error retrieving meeting schedules.")
                await context.bot.send_message(user_id_b, "Error retrieving meeting schedules.")
        else:
            user_id_a = choices[0][0]
            user_id_b = choices[1][0]
            await context.bot.send_message(user_id_a, "No common slots found. Please choose again.")
            await context.bot.send_message(user_id_b, "No common slots found. Please choose again.")

    context.user_data.pop('current_meeting', None)

# /summarise command handler.
async def summarise_text(update: Update, context: CallbackContext):
    input_text = " ".join(context.args).strip()
    if not input_text:
        await update.message.reply_text("Please provide the text you would like to summarise after the command.")
        return

    await update.message.reply_text("Summarising your text, please wait...")
    try:


        client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),  
        )

        response = client.chat.completions.create(
            messages=[
                {"role": "system","content": "You are a medical assistant in charge of summarising medical wound operative notes. Output one of the following classifications: clean/clean-contaminated/contaminated/dirty",},
                {"role":"user", "content": f"Please summarise the following note:\n\n{input_text} "}
            ],
            model="gpt-4o",
            temperature=0
        )

        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(f"Summary:\n{summary}")
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        await update.message.reply_text("An error occurred while trying to summarise the text.")

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("newmeeting", start_new_meeting))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.FileExtension("ics"), handle_ics_upload))
    application.add_handler(CommandHandler("join", join_meeting))
    application.add_handler(CallbackQueryHandler(slot_selection, pattern=r"^\d{3}( \d{3}){3}\|.*$"))
    application.add_handler(CallbackQueryHandler(submit_selection, pattern=r"^submit\|.*$"))
    application.add_handler(CommandHandler("summarise", summarise_text))

    application.run_polling()

if __name__ == "__main__":
    main()



