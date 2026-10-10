"""Run locally to find the ID of a private channel. Does not send messages."""
from getpass import getpass
import json
from urllib.request import Request, urlopen


def main():
    token = getpass("Telegram bot token (hidden): ").strip()
    try:
        body = json.dumps({"allowed_updates": ["channel_post", "message"]}).encode()
        req = Request(f"https://api.telegram.org/bot{token}/getUpdates", data=body,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise ValueError("Telegram rejected request")
        chats = {}
        for update in result.get("result", []):
            message = update.get("channel_post") or update.get("message") or {}
            chat = message.get("chat", {})
            if chat.get("id"):
                chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("type")
        for chat_id, name in chats.items():
            print(f"{name}: {chat_id}")
        if not chats:
            print("No recent chats found. Post a new message in your channel after adding the bot, then retry. Use a dedicated bot without a webhook.")
    except Exception:
        print("Could not retrieve chat IDs. Check the token, network and whether this bot already uses a webhook. Token was not printed.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
