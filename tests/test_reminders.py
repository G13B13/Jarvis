import datetime, importlib, sys, os
import conftest  # noqa
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import reminders

def delta(s):
    return round((reminders.parse_time(s) - datetime.datetime.now()).total_seconds() / 60)

def test_units():
    assert delta("in 10 minutes") == 10
    assert delta("in 5 min") == 5
    assert delta("in 2 hours") == 120
    assert delta("in 1 hr") == 60

def test_unparseable():
    assert reminders.parse_time("whenever") is None

def test_clock_times():
    assert reminders.parse_time("at 3pm").hour == 15
    assert reminders.parse_time("at 12am").hour == 0
    assert reminders.parse_time("tomorrow 9am").hour == 9

def test_db_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "DB_PATH", str(tmp_path / "r.db"))
    reminders.init_db()
    reminders.add_reminder(1, "2000-01-01 00:00:00", "Call Mom")
    rows = reminders.get_due_reminders()
    assert rows[0][2] == "Call Mom"
    reminders.delete_reminder(rows[0][0])
    assert reminders.get_due_reminders() == []
