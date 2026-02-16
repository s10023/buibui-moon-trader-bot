import logging
import os

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s")


def _get_env_credentials() -> tuple[str | None, str | None]:
    """Load Telegram credentials from environment."""
    load_dotenv()
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_message(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    if bot_token is None or chat_id is None:
        env_token, env_chat_id = _get_env_credentials()
        bot_token = bot_token or env_token
        chat_id = chat_id or env_chat_id

    if not bot_token or not chat_id:
        logging.error("Telegram not configured properly.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logging.error(f"\u274c Failed to send Telegram message: {e}")
