# Jarvis: Personal AI Assistant

A self-hosted personal assistant you control from Discord. A **brain** (home server) talks to a **worker** (laptop) over a private **Tailscale** network and uses **DeepSeek** for natural-language understanding and function calling.

Built for personal use: open apps, manage files, set reminders, control system settings and more, by text or voice memo.

```
 You (Discord) ──► Brain (home server)                 Worker (laptop)
                   Jarvis_brain.py  ── Tailscale ──►   worker.py (Flask/waitress)
                   ├─ DeepSeek (function calling)      ├─ allowlisted commands only
                   ├─ faster-whisper (voice memos)     ├─ file get/save (allowed dirs)
                   └─ SQLite reminders                 └─ status, screenshot
```

## Features

- **Chat:** natural language via DeepSeek function calling; `jarvis ...` or `j ...` prefix; only your Discord user ID is obeyed.
- **Voice memos:** send a Discord voice message, it's transcribed locally with faster-whisper and executed.
- **Laptop control:** launch apps, open sites, Google/YouTube search, volume, brightness, Wi-Fi, Bluetooth, screenshots, sleep/shutdown/reboot (with `YES` confirmation).
- **Files:** pull a file from the laptop into Discord (`j send me ~/Documents/report.pdf`), push an attachment to `~/Downloads`. 8 MB limit.
- **Reminders:** `j remind me to call mom in 30 minutes` (SQLite-backed).
- **Automation:** 07:00 daily briefing (weather, laptop status), low-battery alerts, daily conversation summary.

## Status

| Working | In progress / not in this repo yet |
|---|---|
| Text + voice commands, function calling | Robot arm server (`robot_server.py`, PCA9685 + Arduino): works locally, not published yet |
| File transfer, screenshots, system control | Camera object detection |
| Reminders, briefing, battery alerts | Google Meet creation (planned) |

Known limitation: Discord voice-channel wake word was dropped (unstable `discord-ext-voice-recv`). Voice memos are the reliable alternative.

Planned: ESP32 push-to-talk node, multi-worker support, persistent conversation memory.

## Repo layout

```
Jarvis_brain.py        Discord bot + DeepSeek function calling (brain host)
reminders.py           SQLite reminders + time parsing
worker.py              Laptop API (worker host)
services/              systemd units for brain and worker
tests/                 pytest suite (worker security + reminder parsing)
requirements-brain.txt / requirements-worker.txt / requirements-dev.txt
.env.example
```

## Setup

You need two machines on one Tailscale network, a Discord bot token and a DeepSeek API key.

### 1. Discord bot
Developer Portal → New Application → Bot → Reset Token. Enable **MESSAGE CONTENT INTENT**. Invite it with scopes `bot, applications.commands`.

### 2. Tailscale on both machines
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4        # note each machine's IP
```

### 3. Worker (laptop)
```bash
sudo apt install xclip pulseaudio-utils brightnessctl network-manager rfkill scrot
git clone https://github.com/G13B13/Jarvis.git ~/jarvis-worker && cd ~/jarvis-worker
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-worker.txt
cp .env.example .env && chmod 600 .env   # set WORKER_SECRET, WORKER_BIND (laptop's Tailscale IP)
python worker.py                         # test run
```
Run it as a service: edit `YOUR_USERNAME` in `services/jarvis-worker.service`, then
`sudo cp services/jarvis-worker.service /etc/systemd/system/ && sudo systemctl enable --now jarvis-worker`.

### 4. Brain (home server)
```bash
sudo apt install ffmpeg
sudo useradd --system --home /opt/jarvis --shell /usr/sbin/nologin jarvis
sudo git clone https://github.com/G13B13/Jarvis.git /opt/jarvis && cd /opt/jarvis
sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements-brain.txt
sudo cp .env.example .env    # set DISCORD_TOKEN, DEEPSEEK_API_KEY, WORKER_SECRET, YOUR_DISCORD_USER_ID, WORKER_URL
sudo chown -R jarvis:jarvis /opt/jarvis && sudo chmod 600 /opt/jarvis/.env
sudo cp services/jarvis-brain.service /etc/systemd/system/ && sudo systemctl enable --now jarvis-brain
```
`WORKER_SECRET` must be identical on both machines.

## Usage

```
j hello
j open firefox
j status
j send me ~/Documents/report.pdf
j remind me to call mom in 30 minutes
j volume 40
j search youtube for lo-fi
j shutdown laptop        (asks for YES)
j I want to watch some videos     -> LLM decides (opens YouTube)
```
Exact short commands are handled directly; anything else goes to DeepSeek.

## Security model

- Worker routes all require `X-API-Key` (constant-time comparison); the secret lives in `.env`, never in source.
- `/execute` runs only commands from a fixed allowlist as argv lists. No shell, no arbitrary programs. User input is validated or URL-encoded.
- `/get_file` only reads inside `WORKER_ALLOWED_DIRS` (default `~/Documents`, `~/Downloads`), resolves symlinks and `..`, and refuses dotfiles (`.ssh`, `.env`, ...).
- The worker binds to the Tailscale IP only, never `0.0.0.0`.
- The brain obeys a single Discord user ID and runs as an unprivileged systemd user with `NoNewPrivileges` and a read-only filesystem except its own directory.
- `.env` is gitignored. If a key ever lands in a commit, rotate it. Deleting the commit is not enough.

## Tests

```bash
pip install -r requirements-dev.txt && python -m pytest -q tests
```

## Tech stack

discord.py · DeepSeek (OpenAI-compatible SDK) · faster-whisper · Flask + waitress · SQLite · APScheduler · Tailscale · systemd

## License

MIT, see `LICENSE`.
