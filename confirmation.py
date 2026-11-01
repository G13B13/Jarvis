"""Decision logic for the "reply YES to confirm" flow.

Kept free of Discord/network code so it can be unit-tested.
"""

NEGATIVES = {"NO", "N", "CANCEL"}


def resolve(pending, text, now):
    """Decide what to do with the user's next message while a confirmation may be pending.

    pending: dict like {"command": "shutdown", "expires": <loop time>} or None
    text:    the raw message content
    now:     current loop time, same clock as pending["expires"]

    Returns (action, handled):
      action  - "none" | "execute" | "expired" | "cancelled"
      handled - True if the message was fully consumed by the confirmation flow;
                False if it should still be processed as a normal command.

    A pending confirmation is always one-shot: the caller removes it whatever happens,
    so a late "YES" can never run a shutdown and a stray message can't leave it hanging.
    """
    if not pending:
        return "none", False

    answer = (text or "").strip().upper()
    expired = now > pending["expires"]

    if answer == "YES":
        return ("expired", True) if expired else ("execute", True)
    if answer in NEGATIVES:
        return "cancelled", True
    if expired:
        return "none", False          # stale; just handle the new message normally
    return "cancelled", False         # something else came in: cancel, then process it
