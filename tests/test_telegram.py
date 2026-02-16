"""Tests for utils/telegram.py."""

from typing import Any
from unittest.mock import patch

from utils.telegram import send_telegram_message

TOKEN = "test_token"
CHAT = "12345"


class TestSendTelegramMessage:
    """Tests for send_telegram_message()."""

    @patch("utils.telegram.requests.post")
    def test_successful_send(self, mock_post: Any) -> None:
        """Message is sent with correct payload."""
        send_telegram_message("Hello test", bot_token=TOKEN, chat_id=CHAT)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = call_kwargs[1]["data"]
        assert payload["chat_id"] == CHAT
        assert payload["text"] == "Hello test"
        assert payload["parse_mode"] == "Markdown"
        assert payload["disable_web_page_preview"] is True
        assert call_kwargs[1]["timeout"] == 10

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram._get_env_credentials", return_value=(None, None))
    def test_missing_bot_token(self, _mock_creds: Any, mock_post: Any) -> None:
        """No HTTP call when bot_token is missing."""
        send_telegram_message("Hello", bot_token=None, chat_id=CHAT)
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram._get_env_credentials", return_value=(None, None))
    def test_missing_chat_id(self, _mock_creds: Any, mock_post: Any) -> None:
        """No HTTP call when chat_id is missing."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=None)
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    def test_empty_credentials(self, mock_post: Any) -> None:
        """No HTTP call when credentials are empty strings."""
        send_telegram_message("Hello", bot_token="", chat_id="")
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post", side_effect=Exception("Network error"))
    def test_network_error_handled(self, mock_post: Any) -> None:
        """Network errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.requests.post", side_effect=TimeoutError("Timeout"))
    def test_timeout_handled(self, mock_post: Any) -> None:
        """Timeout errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.requests.post")
    @patch(
        "utils.telegram._get_env_credentials", return_value=("env_token", "env_chat")
    )
    def test_falls_back_to_env(self, mock_creds: Any, mock_post: Any) -> None:
        """Falls back to env vars when no explicit credentials passed."""
        send_telegram_message("Hello")

        mock_creds.assert_called_once()
        mock_post.assert_called_once()
        assert "env_token" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["data"]["chat_id"] == "env_chat"
