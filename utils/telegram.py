import logging
import os
import time

import requests

_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2]  # seconds between attempt 1→2 and 2→3


def _get_env_credentials() -> tuple[str | None, str | None]:
    """Read Telegram credentials from environment."""
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
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            return
        except requests.HTTPError as e:
            last_exc = e
            status = e.response.status_code if e.response is not None else None
            if status == 400:
                preview = text[:200].replace("\n", " ")
                logging.warning(
                    "Telegram 400 Bad Request (attempt %d/%d). Message preview: %r",
                    attempt,
                    _MAX_RETRIES,
                    preview,
                )
            else:
                logging.warning(
                    "Telegram HTTP error %s (attempt %d/%d): %s",
                    status,
                    attempt,
                    _MAX_RETRIES,
                    e,
                )
        except Exception as e:
            last_exc = e
            logging.warning(
                "Telegram request failed (attempt %d/%d): %s",
                attempt,
                _MAX_RETRIES,
                e,
            )

        if attempt < _MAX_RETRIES:
            delay = _RETRY_DELAYS[attempt - 1]
            logging.info("Retrying Telegram send in %ds...", delay)
            time.sleep(delay)

    logging.error(
        "\u274c Failed to send Telegram message after %d attempts: %s",
        _MAX_RETRIES,
        last_exc,
    )
