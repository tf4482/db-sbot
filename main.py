#!/usr/bin/env python3

import json
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

_local_cfg  = Path(__file__).parent / "db-sbot-config.json"
_user_cfg   = Path.home() / ".config" / "db-sbot" / "db-sbot-config.json"
_cfg_path   = next((p for p in (_local_cfg, _user_cfg) if p.exists()), None)

if _cfg_path is None:
    _user_cfg.parent.mkdir(parents=True, exist_ok=True)
    _placeholder = {
        "USER_TOKEN":              "your-discord-user-token-here",
        "SERVER_ID":               "your-server-id-here",
        "LOGGING_ENABLED":         True,
        "EMAIL_ENABLED":           False,
        "GMAIL_ADDRESS":           "your-gmail@gmail.com",
        "GMAIL_APP_PASSWORD":      "your-gmail-app-password",
        "EMAIL_RECIPIENTS":        [],
        "DISCORD_WEBHOOK_ENABLED": False,
        "DISCORD_WEBHOOK_URL":     "your-discord-webhook-url-here",
    }
    with open(_user_cfg, "w", encoding="utf-8") as _f:
        json.dump(_placeholder, _f, indent=4)
    print(f"⚙️  No config file found. A placeholder has been created at:\n   {_user_cfg}\nPlease fill it in and restart the script.")
    raise SystemExit(1)

with open(_cfg_path, encoding="utf-8") as _f:
    _cfg = json.load(_f)

USER_TOKEN         = _cfg["USER_TOKEN"]
SERVER_ID          = _cfg["SERVER_ID"]
LOGGING_ENABLED    = _cfg.get("LOGGING_ENABLED", True)

# Email notification settings
EMAIL_ENABLED      = _cfg.get("EMAIL_ENABLED", True)
GMAIL_ADDRESS      = _cfg.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = _cfg.get("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENTS   = _cfg.get("EMAIL_RECIPIENTS", [])

# Discord webhook notification settings
DISCORD_WEBHOOK_ENABLED = _cfg.get("DISCORD_WEBHOOK_ENABLED", False)
DISCORD_WEBHOOK_URL     = _cfg.get("DISCORD_WEBHOOK_URL", "")

CHECK_INTERVAL = 60
LOG_FILE = "channel_log.txt"

HEADERS = {
    "Authorization": USER_TOKEN,
    "Content-Type": "application/json"
}

def get_server_name():
    """Fetches the name of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()["name"]
    else:
        print(f"Error fetching server name: {response.status_code}")
        return SERVER_ID

def get_channels():
    """Fetches all channels of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}/channels"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return {ch["id"]: ch["name"] for ch in response.json()}
    else:
        print(f"Error: {response.status_code}")
        return None

def send_email(subject: str, body: str, recipients: list[str]):
    """Sends an email from the configured Gmail account to one or more recipients."""
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())
        print(f"📧 Email sent to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Error sending email: {e}")

def send_discord_webhook(title: str, description: str, color: int):
    """Sends a Discord embed notification via a configured webhook URL."""
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code in (200, 204):
            print("🔔 Discord webhook notification sent.")
        else:
            print(f"Error sending Discord webhook: {response.status_code} – {response.text}")
    except Exception as e:
        print(f"Error sending Discord webhook: {e}")

def notify(subject: str, body: str, title: str, color: int):
    """Dispatches notifications to all enabled channels (email, Discord webhook)."""
    if EMAIL_ENABLED and EMAIL_RECIPIENTS:
        send_email(subject=subject, body=body, recipients=EMAIL_RECIPIENTS)
    if DISCORD_WEBHOOK_ENABLED and DISCORD_WEBHOOK_URL:
        send_discord_webhook(title=title, description=body, color=color)

def log_event(event: str, channel_name: str, channel_id: str):
    """Writes a channel event to the log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {event}: #{channel_name} (ID: {channel_id})\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

def main():
    server_name = get_server_name()
    print(f"🔍 Monitoring started on server: {server_name}")

    known_channels = get_channels()

    if known_channels is None:
        print("Could not load channels. Aborting.")
        return

    print(f"✅ {len(known_channels)} channels found. Monitoring...")

    while True:
        time.sleep(CHECK_INTERVAL)

        current_channels = get_channels()

        if current_channels is None:
            continue

        for ch_id, ch_name in current_channels.items():
            if ch_id not in known_channels:
                print(f"🆕 New channel: #{ch_name}")
                if LOGGING_ENABLED:
                    log_event("NEW CHANNEL", ch_name, ch_id)
                notify(
                    subject=f"New Discord channel detected: #{ch_name}",
                    body=f"A new channel was created on the server.\n\nName: #{ch_name}\nID: {ch_id}",
                    title=f"🆕 New channel: #{ch_name}",
                    color=0x57F287,  # green
                )

        for ch_id, ch_name in known_channels.items():
            if ch_id not in current_channels:
                print(f"🗑️ Channel deleted: #{ch_name}")
                if LOGGING_ENABLED:
                    log_event("CHANNEL DELETED", ch_name, ch_id)
                notify(
                    subject=f"Discord channel deleted: #{ch_name}",
                    body=f"A channel was deleted from the server.\n\nName: #{ch_name}\nID: {ch_id}",
                    title=f"🗑️ Channel deleted: #{ch_name}",
                    color=0xED4245,  # red
                )

        known_channels = current_channels

if __name__ == "__main__":
    main()
