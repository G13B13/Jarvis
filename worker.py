"""Jarvis worker: small authenticated Flask API that runs on the laptop.

Security model:
  * Every route requires the X-API-Key header (constant-time compare).
  * /execute only runs commands from a fixed allowlist. No arbitrary programs, no shell.
  * File reads are limited to WORKER_ALLOWED_DIRS (symlinks resolved, dotfiles blocked).
  * Bind to your Tailscale IP via WORKER_BIND, never 0.0.0.0.
"""
import base64
import binascii
import hmac
import os
import subprocess
import tempfile
from functools import wraps
from urllib.parse import quote_plus

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file

load_dotenv()

API_SECRET = os.environ.get("WORKER_SECRET")
if not API_SECRET:
    raise SystemExit("WORKER_SECRET is not set. Put it in .env (same value as the brain).")

MAX_FILE_BYTES = 8 * 1024 * 1024
DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
ALLOWED_DIRS = [
    os.path.realpath(os.path.expanduser(p.strip()))
    for p in os.environ.get("WORKER_ALLOWED_DIRS", "~/Documents,~/Downloads").split(",")
    if p.strip()
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # base64 of 8 MB is ~11 MB


# ---------- auth ----------

def require_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied.encode(), API_SECRET.encode()):
            return jsonify({"error": "Unauthorized"}), 403
        return fn(*args, **kwargs)
    return wrapper


# ---------- command allowlist (argv lists, never shell strings) ----------

def _percent(query, lo=0, hi=100):
    value = int(str(query).strip().rstrip("%"))  # ValueError -> 400
    return max(lo, min(hi, value))

STATIC_COMMANDS = {
    "open_firefox": ["firefox"],
    "open_vscode": ["code"],
    "open_terminal": ["konsole"],
    "lock_screen": ["qdbus", "org.kde.ksmserver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
    "take_screenshot": ["scrot", "/tmp/screenshot.png"],
    "open_spotify": ["spotify"],
    "open_discord": ["discord"],
    "open_dolphin": ["dolphin"],
    "open_settings": ["systemsettings"],
    "open_calculator": ["kcalc"],
    "open_notepad": ["kate"],
    "open_libreoffice": ["libreoffice"],
    "open_youtube": ["firefox", "https://youtube.com"],
    "open_netflix": ["firefox", "https://netflix.com"],
    "open_gmail": ["firefox", "https://mail.google.com"],
    "open_github": ["firefox", "https://github.com"],
    "volume_up": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
    "volume_down": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
    "toggle_mute": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
    "brightness_up": ["brightnessctl", "set", "+5%"],
    "brightness_down": ["brightnessctl", "set", "5%-"],
    "wifi_on": ["nmcli", "radio", "wifi", "on"],
    "wifi_off": ["nmcli", "radio", "wifi", "off"],
    "bluetooth_on": ["rfkill", "unblock", "bluetooth"],
    "bluetooth_off": ["rfkill", "block", "bluetooth"],
    "sleep": ["systemctl", "suspend"],
    "shutdown": ["systemctl", "poweroff"],
    "reboot": ["systemctl", "reboot"],
}

# commands that take a query; each builds argv from validated input
QUERY_COMMANDS = {
    "search_google": lambda q: ["firefox", "https://www.google.com/search?q=" + quote_plus(q)],
    "search_youtube": lambda q: ["firefox", "https://www.youtube.com/results?search_query=" + quote_plus(q)],
    "set_volume": lambda q: ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{_percent(q)}%"],
    "set_brightness": lambda q: ["brightnessctl", "set", f"{_percent(q, lo=5)}%"],
}


@app.route("/execute", methods=["POST"])
@require_key
def execute_command():
    data = request.get_json(silent=True) or {}
    command = data.get("command")
    query = str(data.get("query", ""))

    try:
        if command in STATIC_COMMANDS:
            argv = STATIC_COMMANDS[command]
        elif command in QUERY_COMMANDS:
            if not query.strip():
                return jsonify({"error": "query required"}), 400
            argv = QUERY_COMMANDS[command](query[:200])
        else:
            return jsonify({"error": "Command not allowed"}), 400
    except ValueError:
        return jsonify({"error": "Invalid query"}), 400

    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except FileNotFoundError:
        return jsonify({"error": f"{argv[0]} is not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "success", "message": f"Executed {command}"})


# ---------- status ----------

def get_system_status():
    battery = psutil.sensors_battery()
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
        "uptime": subprocess.check_output(["uptime", "-p"]).decode().strip(),
        "battery_percent": battery.percent if battery else None,
        "battery_plugged": battery.power_plugged if battery else None,
    }


@app.route("/status", methods=["GET"])
@require_key
def system_status():
    return jsonify(get_system_status())


# ---------- files ----------

def resolve_readable(path_str):
    """Return a safe real path inside ALLOWED_DIRS, or None."""
    real = os.path.realpath(os.path.expanduser(path_str))  # resolves symlinks and ..
    for base in ALLOWED_DIRS:
        if os.path.commonpath([real, base]) == base:
            rel_parts = os.path.relpath(real, base).split(os.sep)
            if any(part.startswith(".") and part != "." for part in rel_parts):
                return None  # no dotfiles/dot-directories (.ssh, .env, .git ...)
            return real
    return None


@app.route("/get_file", methods=["POST"])
@require_key
def get_file():
    data = request.get_json(silent=True) or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "path required"}), 400

    real = resolve_readable(path_str)
    if real is None:
        return jsonify({"error": "Access denied"}), 403
    if not os.path.isfile(real):
        return jsonify({"error": "File not found"}), 404
    size = os.path.getsize(real)
    if size > MAX_FILE_BYTES:
        return jsonify({"error": "File too large (>8MB)"}), 413

    with open(real, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return jsonify({"filename": os.path.basename(real), "data": encoded, "size": size})


@app.route("/save_file", methods=["POST"])
@require_key
def save_file():
    data = request.get_json(silent=True) or {}
    filename = os.path.basename(str(data.get("filename", ""))).strip().lstrip(".")
    if not filename:
        filename = "received_file"
    try:
        content = base64.b64decode(data.get("data", ""), validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"error": "Invalid file data"}), 400
    if len(content) > MAX_FILE_BYTES:
        return jsonify({"error": "File too large (>8MB)"}), 413

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    base, ext = os.path.splitext(filename)
    save_path = os.path.join(DOWNLOADS_DIR, filename)
    counter = 1
    while os.path.exists(save_path):
        save_path = os.path.join(DOWNLOADS_DIR, f"{base}_{counter}{ext}")
        counter += 1
    try:
        with open(save_path, "xb") as f:  # "x" = never overwrite
            f.write(content)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "success", "message": f"Saved to {save_path}"})


@app.route("/screenshot", methods=["GET"])
@require_key
def take_screenshot_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        shot = os.path.join(tmp, "screenshot.png")
        try:
            subprocess.run(["scrot", shot], check=True, timeout=10)
        except Exception as e:
            return jsonify({"error": f"Screenshot failed: {e}"}), 500
        with open(shot, "rb") as f:
            png = f.read()
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


if __name__ == "__main__":
    host = os.environ.get("WORKER_BIND", "127.0.0.1")
    port = int(os.environ.get("WORKER_PORT", "5000"))
    if host == "127.0.0.1":
        print("WARNING: bound to 127.0.0.1, so the brain can't reach it. "
              "Set WORKER_BIND to this machine's Tailscale IP (tailscale ip -4).")
    from waitress import serve
    serve(app, host=host, port=port)
