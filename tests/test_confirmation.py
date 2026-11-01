import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from confirmation import resolve

P = {"command": "shutdown", "expires": 100.0}

def test_no_pending():
    assert resolve(None, "YES", 50) == ("none", False)

def test_yes_in_time_executes():
    assert resolve(P, "yes", 50) == ("execute", True)
    assert resolve(P, "  YES ", 100.0) == ("execute", True)

def test_late_yes_never_executes():
    assert resolve(P, "YES", 100.1) == ("expired", True)

def test_explicit_no_cancels_and_consumes():
    for word in ("no", "N", "cancel"):
        assert resolve(P, word, 50) == ("cancelled", True)

def test_other_message_cancels_but_is_still_processed():
    assert resolve(P, "j volume up", 50) == ("cancelled", False)

def test_stale_other_message_is_just_processed():
    assert resolve(P, "j volume up", 500) == ("none", False)
