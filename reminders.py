import sqlite3
import datetime
import re
import os

# Fix: Dynamic DB path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "reminders.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  remind_time TIMESTAMP,
                  message TEXT)''')
    conn.commit()
    conn.close()

def add_reminder(user_id, remind_time, message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, remind_time, message) VALUES (?, ?, ?)",
              (user_id, remind_time, message))
    conn.commit()
    conn.close()

def get_due_reminders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("SELECT id, user_id, message FROM reminders WHERE remind_time <= ?", (now,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_reminder(reminder_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def parse_time(time_str):
    now = datetime.datetime.now()
    time_str = time_str.lower().strip()

    # Fix: Require the unit for minutes so "in 2 hours" doesn't match as 2 minutes
    match = re.match(r'in (\d+)\s*(minute|minutes|min|mins?)\b', time_str)
    if match:
        return now + datetime.timedelta(minutes=int(match.group(1)))

    match = re.match(r'in (\d+)\s*(hour|hours|hr|hrs?)\b', time_str)
    if match:
        return now + datetime.timedelta(hours=int(match.group(1)))

    match = re.match(r'at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_str)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target < now:
            target += datetime.timedelta(days=1)
        return target

    if 'tomorrow' in time_str:
        base = now + datetime.timedelta(days=1)
        time_part = time_str.replace('tomorrow', '').strip()
        if time_part:
            match = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_part)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2) or 0)
                ampm = match.group(3)
                if ampm == 'pm' and hour < 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
                return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return base.replace(hour=9, minute=0, second=0, microsecond=0)

    return None
