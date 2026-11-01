"""Tiny JSON persistence for conversation history and the daily summary.

Written atomically (temp file + rename) with 0600 permissions, and tolerant of a
missing or corrupt file so a bad write can never stop the bot from starting.
"""
import json
import os
import tempfile

MAX_TURNS = 10  # messages kept per user (same as the bot's in-memory limit)


def _clean_history(raw):
    history = {}
    if not isinstance(raw, dict):
        return history
    for uid, msgs in raw.items():
        try:
            key = int(uid)
        except (TypeError, ValueError):
            continue
        if not isinstance(msgs, list):
            continue
        good = [
            {"role": m["role"], "content": m["content"]}
            for m in msgs
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        if good:
            history[key] = good[-MAX_TURNS:]
    return history


def load(path):
    """Return {"history": {int: [...]}, "daily_memory": str, "summarized_at": float}."""
    empty = {"history": {}, "daily_memory": "", "summarized_at": 0.0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    daily = data.get("daily_memory", "")
    stamp = data.get("summarized_at", 0.0)
    return {
        "history": _clean_history(data.get("history")),
        "daily_memory": daily if isinstance(daily, str) else "",
        "summarized_at": float(stamp) if isinstance(stamp, (int, float)) else 0.0,
    }


def save(path, history, daily_memory, summarized_at):
    """Atomically write the memory file (mode 0600)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "history": {str(k): v[-MAX_TURNS:] for k, v in history.items()},
                    "daily_memory": daily_memory,
                    "summarized_at": summarized_at,
                },
                f,
            )
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
