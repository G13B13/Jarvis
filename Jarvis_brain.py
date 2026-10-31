import discord
from openai import OpenAI
import requests
import base64
import io
import re
import asyncio
import datetime
from reminders import init_db, add_reminder, get_due_reminders, delete_reminder, parse_time
import random
import json
import tempfile
import os
import subprocess

from dotenv import load_dotenv
load_dotenv()

try:
    from faster_whisper import WhisperModel
    print("Pre-loading Whisper model...")
    whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    print("Whisper model ready.")
except ImportError:
    whisper_model = None
    print("⚠️  faster-whisper not installed — voice messages disabled")

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===== CONFIGURATION =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
WORKER_URL = os.getenv("WORKER_URL")
WORKER_SECRET = os.getenv("WORKER_SECRET")
ROBOT_URL = os.getenv("ROBOT_URL")
YOUR_DISCORD_USER_ID = int(os.getenv("YOUR_DISCORD_USER_ID"))
# =========================

conversation_history = {}
DAILY_MEMORY = ""
pending_confirmations = {}
alert_cooldowns = {}

deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

scheduler = AsyncIOScheduler()

ACTION_KEYWORDS = {
    "firefox": "open_firefox", "vs code": "open_vscode", "code": "open_vscode",
    "terminal": "open_terminal", "konsole": "open_terminal", "screenshot": "take_screenshot",
    "spotify": "open_spotify", "discord": "open_discord", "dolphin": "open_dolphin",
    "file manager": "open_dolphin", "settings": "open_settings",
    "calculator": "open_calculator", "calc": "open_calculator",
    "kate": "open_notepad", "notepad": "open_notepad",
    "libreoffice": "open_libreoffice", "youtube": "open_youtube",
    "netflix": "open_netflix", "gmail": "open_gmail", "github": "open_github",
}

# ===== HELPERS =====

async def get_laptop_status():
    try:
        r = await asyncio.to_thread(
            requests.get, WORKER_URL.replace("/execute", "/status"),
            headers={"X-API-Key": WORKER_SECRET}, timeout=5
        )
        if r.status_code == 200:
            d = r.json()
            bat = ""
            if d.get("battery_percent") is not None:
                s = "🔌" if d["battery_plugged"] else "🔋"
                bat = f"Battery: {d['battery_percent']}% ({s})\n"
            return (f"**Laptop Status**\nCPU: {d['cpu_percent']}%\n"
                    f"Memory: {d['memory_percent']}%\nDisk: {d['disk_percent']}%\n"
                    f"{bat}Uptime: {d['uptime']}")
        return f"❌ Status check failed: {r.text}"
    except Exception as e:
        return f"❌ Worker unreachable: {e}"

async def send_to_worker(command, query=None):
    payload = {"command": command}
    if query:
        payload["query"] = query
    try:
        r = await asyncio.to_thread(
            requests.post, WORKER_URL, json=payload,
            headers={"X-API-Key": WORKER_SECRET}, timeout=5
        )
        if r.status_code == 200:
            return True, r.json().get("message", "Done")
        return False, f"Worker error: {r.text}"
    except Exception as e:
        return False, f"Worker unreachable: {e}"

async def get_weather(city="Lahore"):
    try:
        geo = await asyncio.to_thread(
            requests.get, f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1", timeout=5
        )
        geo_data = geo.json()
        if not geo_data.get("results"):
            return "City not found"
        loc = geo_data["results"][0]
        w = await asyncio.to_thread(
            requests.get, f"https://api.open-meteo.com/v1/forecast?latitude={loc['latitude']}&longitude={loc['longitude']}&current_weather=true", timeout=5
        )
        c = w.json()["current_weather"]
        return f"{city}: {c['temperature']}°C, Wind {c['windspeed']} km/h"
    except Exception as e:
        return f"Weather unavailable: {e}"

QUOTES = [
    "The only way to do great work is to love what you do. – Steve Jobs",
    "Stay hungry, stay foolish. – Steve Jobs",
    "The best time to plant a tree was 20 years ago. The second best time is now. – Chinese Proverb",
    "Believe you can and you're halfway there. – Theodore Roosevelt",
    "Don't count the days, make the days count. – Muhammad Ali",
]

def get_quote():
    return random.choice(QUOTES)

async def send_file_to_discord(message, filepath):
    try:
        r = await asyncio.to_thread(
            requests.post, WORKER_URL.replace("/execute", "/get_file"),
            json={"path": filepath}, headers={"X-API-Key": WORKER_SECRET}, timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            fb = base64.b64decode(d['data'])
            await message.reply(f"Here's `{d['filename']}`:",
                                file=discord.File(fp=io.BytesIO(fb), filename=d['filename']))
        else:
            await message.reply(f"❌ {r.json().get('error', 'Unknown')}")
    except Exception as e:
        await message.reply(f"❌ File retrieval failed: {e}")

async def save_attachment_to_laptop(message):
    if not message.attachments:
        await message.reply("❌ No file attached.")
        return
    a = message.attachments[0]
    if a.size > 8 * 1024 * 1024:
        await message.reply("❌ File too large (>8MB).")
        return
    try:
        fb = await a.read()
        b64 = base64.b64encode(fb).decode('utf-8')
        r = await asyncio.to_thread(
            requests.post, WORKER_URL.replace("/execute", "/save_file"),
            json={"filename": a.filename, "data": b64},
            headers={"X-API-Key": WORKER_SECRET}, timeout=15
        )
        if r.status_code == 200:
            await message.reply(f"✅ {r.json().get('message', 'Saved.')}")
        else:
            await message.reply(f"❌ {r.json().get('error', 'Unknown')}")
    except Exception as e:
        await message.reply(f"❌ File save failed: {e}")

async def reminder_checker():
    await client.wait_until_ready()
    while not client.is_closed():
        for rem_id, user_id, msg in get_due_reminders():
            user = await client.fetch_user(user_id)
            if user:
                try:
                    await user.send(f"⏰ **Reminder:** {msg}")
                except Exception:
                    pass
            delete_reminder(rem_id)
        await asyncio.sleep(60)

def build_messages(user_id, user_input):
    sp = ("You are Jarvis, a helpful personal assistant. You can control the user's laptop, "
          "search the web, check laptop status, take screenshots, get weather, set reminders, "
          "and control a robot arm. Always respond in a friendly, concise manner.")
    if DAILY_MEMORY:
        sp += "\n\n[Memory]\n" + DAILY_MEMORY
    msgs = [{"role": "system", "content": sp}]
    if user_id in conversation_history:
        msgs.extend(conversation_history[user_id])
    msgs.append({"role": "user", "content": user_input})
    return msgs

# Fix: Wrapper function for Whisper to run in a separate thread
def _run_whisper(wav_path):
    segments, _ = whisper_model.transcribe(wav_path, beam_size=5)
    return " ".join([s.text for s in segments]).strip()

async def transcribe_audio(attachment):
    if whisper_model is None:
        return None
    ogg_path = None
    wav_path = None
    try:
        audio_bytes = await attachment.read()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            ogg_path = tmp.name
        wav_path = ogg_path.replace(".ogg", ".wav")
        await asyncio.to_thread(
            subprocess.run, ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            check=True, capture_output=True
        )
        # Fix: Run Whisper in a separate thread so it doesn't freeze the bot
        text = await asyncio.to_thread(_run_whisper, wav_path)
        return text
    except Exception as e:
        print(f"Voice transcription error: {e}")
        return None
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.unlink(ogg_path)
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)

# ===== TOOLS =====

TOOLS = [
    {"type": "function", "function": {"name": "open_app_or_website",
        "description": "Open an app or website on the laptop",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["open_firefox","open_youtube","open_netflix",
                "open_github","open_gmail","open_vscode","open_terminal","open_spotify",
                "open_discord","open_dolphin","open_settings","open_calculator",
                "open_notepad","open_libreoffice","take_screenshot"]}},
            "required": ["action"]}}},
    {"type": "function", "function": {"name": "search_web",
        "description": "Search Google or YouTube",
        "parameters": {"type": "object", "properties": {
            "platform": {"type": "string", "enum": ["google","youtube"]},
            "query": {"type": "string"}}, "required": ["platform","query"]}}},
    {"type": "function", "function": {"name": "get_laptop_status",
        "description": "Get laptop CPU, memory, disk, battery, uptime",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "take_screenshot_to_discord",
        "description": "Screenshot the laptop and send to Discord",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
            "required": ["city"]}}},
    {"type": "function", "function": {"name": "set_reminder",
        "description": "Set a reminder",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"}, "time_string": {"type": "string"}},
            "required": ["message","time_string"]}}},
    {"type": "function", "function": {"name": "move_robot_axis",
        "description": "Move robot arm axis. 0=base,1=shoulder,2=elbow,3=gripper",
        "parameters": {"type": "object", "properties": {
            "axis": {"type": "integer"}, "angle": {"type": "integer"}},
            "required": ["axis","angle"]}}},
]

async def process_with_function_calling(message, user_input):
    uid = message.author.id
    try:
        msgs = build_messages(uid, user_input)
        resp = await asyncio.to_thread(
            deepseek_client.chat.completions.create,
            model="deepseek-chat", messages=msgs, tools=TOOLS, tool_choice="auto", stream=False
        )
        rm = resp.choices[0].message
        final = None
        if rm.tool_calls:
            msgs.append({"role": "assistant", "content": rm.content,
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in rm.tool_calls]})
            for tc in rm.tool_calls:
                fn = tc.function.name
                fa = json.loads(tc.function.arguments)
                print(f"Tool: {fn}({fa})")
                result = await execute_function(fn, fa, message)
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            fu = await asyncio.to_thread(
                deepseek_client.chat.completions.create,
                model="deepseek-chat", messages=msgs, stream=False
            )
            final = fu.choices[0].message.content[:1900] if fu.choices[0].message.content else "Done."
            await message.reply(final)
        else:
            final = rm.content[:1900] if rm.content else "I'm not sure how to help."
            await message.reply(final)
        if final:
            conversation_history.setdefault(uid, []).append({"role": "user", "content": user_input})
            conversation_history[uid].append({"role": "assistant", "content": final})
            conversation_history[uid] = conversation_history[uid][-10:]
    except Exception as e:
        await message.reply(f"❌ Error: {e}")

async def execute_function(fn, args, message):
    if fn == "open_app_or_website":
        a = args.get("action")
        if a:
            ok, msg = await send_to_worker(a)
            return f"Success: {msg}" if ok else f"Failed: {msg}"
        return "No action specified"
    elif fn == "search_web":
        p, q = args.get("platform"), args.get("query")
        if p == "google":
            ok, msg = await send_to_worker("search_google", query=q)
            return f"Opened Google for '{q}'" if ok else f"Failed: {msg}"
        elif p == "youtube":
            ok, msg = await send_to_worker("search_youtube", query=q)
            return f"Opened YouTube for '{q}'" if ok else f"Failed: {msg}"
        return "Invalid platform"
    elif fn == "get_laptop_status":
        return await get_laptop_status()
    elif fn == "take_screenshot_to_discord":
        try:
            r = await asyncio.to_thread(
                requests.get, WORKER_URL.replace("/execute", "/screenshot"),
                headers={"X-API-Key": WORKER_SECRET}, timeout=30
            )
            if r.status_code == 200:
                await message.reply("Screenshot:",
                    file=discord.File(fp=io.BytesIO(r.content), filename="screenshot.png"))
                return "Screenshot sent"
            return f"Screenshot failed: {r.text}"
        except Exception as e:
            return f"Screenshot error: {e}"
    elif fn == "get_weather":
        return await get_weather(args.get("city", "Lahore"))
    elif fn == "set_reminder":
        m, t = args.get("message"), args.get("time_string")
        rt = parse_time(t)
        if rt:
            add_reminder(message.author.id, rt.strftime("%Y-%m-%d %H:%M:%S"), m)
            return f"Reminder set: '{m}' at {rt.strftime('%Y-%m-%d %I:%M %p')}"
        return f"Could not parse time: '{t}'"
    elif fn == "move_robot_axis":
        ax, ang = args.get("axis"), args.get("angle")
        if ax is None or ang is None:
            return "axis and angle required"
        try:
            r = await asyncio.to_thread(
                requests.post, f"{ROBOT_URL}/robot/move", json={"axis": ax, "angle": ang},
                headers={"X-API-Key": WORKER_SECRET}, timeout=5
            )
            if r.status_code == 200:
                return f"Moved axis {ax} to {ang}"
            return f"Robot error: {r.text}"
        except Exception as e:
            return f"Robot unreachable: {e}"
    return "Unknown function"

# ===== BACKGROUND TASKS =====

async def memory_summary_task():
    global DAILY_MEMORY
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(86400)
        if conversation_history:
            all_text = ""
            for uid, h in conversation_history.items():
                msgs = [m['content'] for m in h if m['role'] == 'user']
                if msgs:
                    all_text += f"User {uid}: " + " | ".join(msgs) + "\n"
            if all_text.strip():
                try:
                    sr = await asyncio.to_thread(
                        deepseek_client.chat.completions.create,
                        model="deepseek-chat",
                        messages=[{"role": "system", "content": "Summarize concisely."},
                                  {"role": "user", "content": all_text}],
                        stream=False
                    )
                    DAILY_MEMORY = sr.choices[0].message.content.strip()
                    print("✅ Daily memory updated.")
                except Exception as e:
                    print(f"❌ Summary failed: {e}")

async def system_monitor_task():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            r = await asyncio.to_thread(
                requests.get, WORKER_URL.replace("/execute", "/status"),
                headers={"X-API-Key": WORKER_SECRET}, timeout=5
            )
            if r.status_code == 200:
                d = r.json()
                user = await client.fetch_user(YOUR_DISCORD_USER_ID)
                now = asyncio.get_running_loop().time()
                if d.get("battery_percent") and d["battery_percent"] < 20 and not d["battery_plugged"]:
                    if now - alert_cooldowns.get("bat", 0) > 1800:
                        await user.send(f"🔋 Battery: {d['battery_percent']}%")
                        alert_cooldowns["bat"] = now
        except Exception as e:
            print(f"Monitor error: {e}")
        await asyncio.sleep(300)

async def send_scheduled_briefing():
    await client.wait_until_ready()
    user = await client.fetch_user(YOUR_DISCORD_USER_ID)
    if user:
        try:
            now = datetime.datetime.now()
            b = (f"**🌅 Briefing**\n📅 {now.strftime('%A, %B %d, %Y')} | "
                 f"🕒 {now.strftime('%I:%M %p')}\n\n**🌤️ Weather**\n{await get_weather()}\n\n"
                 f"{await get_laptop_status()}\n\n💬 *{get_quote()}*")
            await user.send(b)
            print("✅ Briefing sent.")
        except Exception as e:
            print(f"❌ Briefing failed: {e}")

# ===== EVENTS =====

@client.event
async def on_ready():
    # Fix: Initialize DB on startup
    init_db()
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(reminder_checker())
    client.loop.create_task(memory_summary_task())
    client.loop.create_task(system_monitor_task())
    scheduler.add_job(send_scheduled_briefing, 'cron', hour=7, minute=0, id='morning')
    scheduler.start()

@client.event
async def on_message(message):
    if message.author == client.user or message.author.id != YOUR_DISCORD_USER_ID:
        return

    now = asyncio.get_running_loop().time()
    
    # Confirmation flow
    if message.author.id in pending_confirmations:
        if now > pending_confirmations[message.author.id]["expires"]:
            del pending_confirmations[message.author.id]
            await message.reply("⚠️ Confirmation expired.")
            return
        if message.content.strip().upper() == "YES":
            cmd = pending_confirmations.pop(message.author.id)["command"]
            ok, msg = await send_to_worker(cmd)
            await message.reply(f"{'✅' if ok else '❌'} {msg}")
        else:
            del pending_confirmations[message.author.id]
            await message.reply("❌ Cancelled.")
        return

    user_input = None
    if message.attachments and whisper_model:
        for a in message.attachments:
            if a.filename.lower().endswith(('.ogg', '.mp3', '.wav', '.m4a', '.webm', '.flac')):
                t = await transcribe_audio(a)
                if t:
                    print(f"Voice: {t}")
                    user_input = t.strip()
                    if user_input.lower().startswith("jarvis "):
                        user_input = user_input[7:]
                    elif user_input.lower().startswith("j "):
                        user_input = user_input[2:]
                break

    if not user_input:
        ml = message.content.lower()
        if ml.startswith("jarvis "):
            user_input = message.content[7:].strip()
        elif ml.startswith("j "):
            user_input = message.content[2:].strip()
        else:
            return

    if not user_input:
        await message.reply("Yes?")
        return

    print(f"[{message.author.name}] {user_input}")
    
    lower = user_input.lower().strip()

    if lower in ["status", "how's my laptop", "laptop health"]:
        await message.reply(await get_laptop_status())
        return

    if lower.startswith("send me ") or lower.startswith("get me "):
        p = user_input.split(" ", 2)
        if len(p) >= 3:
            await send_file_to_discord(message, p[2].strip())
        return

    if lower in ["save this", "save file"]:
        await save_attachment_to_laptop(message)
        return

    if lower in ["volume up", "vol up"]:
        ok, m = await send_to_worker("volume_up")
        await message.reply(f"{'✅' if ok else '❌'} {m}"); return
    if lower in ["volume down", "vol down"]:
        ok, m = await send_to_worker("volume_down")
        await message.reply(f"{'✅' if ok else '❌'} {m}"); return
    if lower in ["mute", "unmute"]:
        ok, m = await send_to_worker("toggle_mute")
        await message.reply(f"{'✅' if ok else '❌'} {m}"); return
    vm = re.match(r'(?:set )?volume(?: to)? (\d{1,3})%?', lower)
    if vm:
        ok, m = await send_to_worker("set_volume", query=vm.group(1))
        await message.reply(f"{'✅' if ok else '❌'} Volume {vm.group(1)}%"); return

    if lower in ["brightness up", "bri up"]:
        ok, m = await send_to_worker("brightness_up")
        await message.reply(f"{'✅' if ok else '❌'} {m}"); return
    if lower in ["brightness down", "bri down"]:
        ok, m = await send_to_worker("brightness_down")
        await message.reply(f"{'✅' if ok else '❌'} {m}"); return

    if lower in ["wifi on", "wi-fi on"]:
        await send_to_worker("wifi_on")
        await message.reply("✅ Wi-Fi on"); return
    if lower in ["wifi off", "wi-fi off"]:
        await send_to_worker("wifi_off")
        await message.reply("✅ Wi-Fi off"); return
    if lower in ["bluetooth on", "bt on"]:
        await send_to_worker("bluetooth_on")
        await message.reply("✅ BT on"); return
    if lower in ["bluetooth off", "bt off"]:
        await send_to_worker("bluetooth_off")
        await message.reply("✅ BT off"); return

    if lower in ["sleep laptop", "suspend laptop"]:
        await message.reply("⚠️ Sleep? Reply `YES`.")
        pending_confirmations[message.author.id] = {"command": "sleep", "expires": now + 30}; return
    if lower in ["shutdown laptop", "poweroff laptop"]:
        await message.reply("⚠️ Shutdown? Reply `YES`.")
        pending_confirmations[message.author.id] = {"command": "shutdown", "expires": now + 30}; return
    if lower in ["reboot laptop", "restart laptop"]:
        await message.reply("⚠️ Reboot? Reply `YES`.")
        pending_confirmations[message.author.id] = {"command": "reboot", "expires": now + 30}; return

    # Fix: Reminder casing hack
    if "remind me" in lower:
        match = re.search(r'remind me to (.+?) (in .+|at .+|tomorrow .+)', user_input, re.IGNORECASE)
        if match:
            rm = match.group(1).strip()
            ts = match.group(2).strip()
        else:
            match = re.search(r'remind me (in .+|at .+|tomorrow .+) to (.+)', user_input, re.IGNORECASE)
            if match:
                ts = match.group(1).strip()
                rm = match.group(2).strip()
            else:
                await message.reply("❌ Usage: `j remind me to [task] in 10 minutes`")
                return
        rt = parse_time(ts)
        if rt:
            add_reminder(message.author.id, rt.strftime("%Y-%m-%d %H:%M:%S"), rm)
            await message.reply(f"✅ Reminder: {rm} at {rt.strftime('%Y-%m-%d %I:%M %p')}")
        else:
            await message.reply("❌ Couldn't parse time.")
        return

    if "briefing" in lower:
        n = datetime.datetime.now()
        b = (f"**🌅 Briefing**\n📅 {n.strftime('%A, %B %d, %Y')}\n\n"
             f"**🌤️ Weather**\n{await get_weather()}\n\n{await get_laptop_status()}\n\n💬 *{get_quote()}*")
        await message.reply(b); return

    if lower.startswith("search google for "):
        q = user_input[18:].strip()
        ok, r = await send_to_worker("search_google", query=q)
        await message.reply(f"✅ Google: `{q}`" if ok else f"❌ {r}"); return
    if lower.startswith("search youtube for "):
        q = user_input[19:].strip()
        ok, r = await send_to_worker("search_youtube", query=q)
        await message.reply(f"✅ YouTube: `{q}`" if ok else f"❌ {r}"); return

    if lower in ACTION_KEYWORDS:
        ok, resp = await send_to_worker(ACTION_KEYWORDS[lower])
        await message.reply(f"✅ {resp}" if ok else f"❌ {resp}")
        return
    elif lower.startswith("open ") and lower[5:].strip() in ACTION_KEYWORDS:
        app = lower[5:].strip()
        ok, resp = await send_to_worker(ACTION_KEYWORDS[app])
        await message.reply(f"✅ {resp}" if ok else f"❌ {resp}")
        return

    async with message.channel.typing():
        await process_with_function_calling(message, user_input)

client.run(DISCORD_TOKEN)
