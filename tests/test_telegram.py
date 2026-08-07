"""Tests for utils/telegram.py."""

from typing import Any
from unittest.mock import MagicMock, call, patch

import requests as req

from utils.telegram import send_telegram_message

TOKEN = "test_token"
CHAT = "12345"


class TestSendTelegramMessage:
    """Tests for send_telegram_message()."""

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_successful_send(self, mock_post: Any, mock_sleep: Any) -> None:
        """Message is sent with correct payload on first attempt."""
        send_telegram_message("Hello test", bot_token=TOKEN, chat_id=CHAT)

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = call_kwargs[1]["data"]
        assert payload["chat_id"] == CHAT
        assert payload["text"] == "Hello test"
        assert payload["parse_mode"] == "HTML"
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

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_raise_for_status_called(self, mock_post: Any, mock_sleep: Any) -> None:
        """raise_for_status() is called on every attempt."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)
        assert mock_post.return_value.raise_for_status.call_count == 1

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_http_error_is_caught_not_raised(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """HTTPError from raise_for_status is caught — callers are not crashed."""
        mock_post.return_value.raise_for_status.side_effect = req.HTTPError(
            "403 Forbidden"
        )
        # Must not raise — error is logged and swallowed after all retries
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post", side_effect=Exception("Network error"))
    def test_network_error_handled(self, mock_post: Any, mock_sleep: Any) -> None:
        """Network errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post", side_effect=TimeoutError("Timeout"))
    def test_timeout_handled(self, mock_post: Any, mock_sleep: Any) -> None:
        """Timeout errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    @patch(
        "utils.telegram._get_env_credentials", return_value=("env_token", "env_chat")
    )
    def test_falls_back_to_env(
        self, mock_creds: Any, mock_post: Any, mock_sleep: Any
    ) -> None:
        """Falls back to env vars when no explicit credentials passed."""
        send_telegram_message("Hello")

        mock_creds.assert_called_once()
        mock_post.assert_called_once()
        assert "env_token" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["data"]["chat_id"] == "env_chat"

    # --- Retry behaviour ---

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_success_on_first_attempt_no_retry(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """No sleep when message sends successfully on first try."""
        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)
        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_success_after_one_retry(self, mock_post: Any, mock_sleep: Any) -> None:
        """Succeeds on 2nd attempt; sleeps once with 1s delay."""
        # First call raises, second succeeds
        first_response = MagicMock()
        first_response.raise_for_status.side_effect = req.HTTPError("500 Server Error")
        first_response.response = MagicMock()
        first_response.response.status_code = 500
        second_response = MagicMock()
        second_response.raise_for_status.return_value = None

        mock_post.side_effect = [first_response, second_response]

        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)

        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_raises_after_max_retries_exhausted(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """After 3 failed attempts, error is logged and function returns without raising."""
        mock_post.return_value.raise_for_status.side_effect = req.HTTPError("500")
        # Must not raise to callers
        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([call(1), call(2)])

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_400_logs_message_preview(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """400 Bad Request logs a preview of the message text."""
        import logging

        bad_response = MagicMock()
        bad_response.status_code = 400
        http_err = req.HTTPError("400 Bad Request")
        http_err.response = bad_response
        mock_post.return_value.raise_for_status.side_effect = http_err

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("Special chars: *_[]", bot_token=TOKEN, chat_id=CHAT)

        assert any(
            "400" in r.message and "Special chars" in r.message for r in caplog.records
        )

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_400_retries_without_parse_mode(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """A 400 drops parse_mode so the retry can deliver as plain text.

        Regression for a real 2026-08-07 failure: run-job.sh Telegrams the log
        tail when a job dies, and a Python traceback contains
        `line 33, in <module>` -- which Telegram's HTML parser reads as an
        unclosed tag and rejects with 400. Retrying the identical payload can
        never succeed, so the alert that reports a crash failed on every crash
        that produced a traceback.
        """
        bad_response = MagicMock()
        bad_response.status_code = 400
        http_err = req.HTTPError("400 Bad Request")
        http_err.response = bad_response
        mock_post.return_value.raise_for_status.side_effect = http_err

        send_telegram_message("Traceback in <module>", bot_token=TOKEN, chat_id=CHAT)

        # First attempt carries the formatting; every later one must not.
        assert mock_post.call_count == 3
        first = mock_post.call_args_list[0][1]["data"]
        assert first["parse_mode"] == "HTML"
        for later in mock_post.call_args_list[1:]:
            assert "parse_mode" not in later[1]["data"]

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_non_400_keeps_parse_mode(self, mock_post: Any, mock_sleep: Any) -> None:
        """A 500 is transient -- formatting must survive so the retry is faithful.

        Negative control for the test above: without this, dropping parse_mode
        unconditionally would also pass, and the fix would silently degrade every
        formatted alert on any transient server error.
        """
        bad_response = MagicMock()
        bad_response.status_code = 500
        http_err = req.HTTPError("500 Server Error")
        http_err.response = bad_response
        mock_post.return_value.raise_for_status.side_effect = http_err

        send_telegram_message("<b>still bold</b>", bot_token=TOKEN, chat_id=CHAT)

        assert mock_post.call_count == 3
        for c in mock_post.call_args_list:
            assert c[1]["data"]["parse_mode"] == "HTML"

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_bot_token_never_reaches_the_log(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """The final error must not carry the bot token.

        requests puts the full request URL in HTTPError.__str__, and that URL
        embeds the bot token -- so logging the bare exception wrote a live
        credential into the systemd journal on every exhausted send. Observed
        2026-08-07.
        """
        import logging

        leaky = req.HTTPError(
            f"400 Client Error for url: https://api.telegram.org/bot{TOKEN}/sendMessage"
        )
        leaky.response = MagicMock()
        leaky.response.status_code = 400
        mock_post.return_value.raise_for_status.side_effect = leaky

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("boom", bot_token=TOKEN, chat_id=CHAT)

        # getMessage() applies args exactly once; `record.message % record.args`
        # double-formats an already-rendered message and raises TypeError.
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert TOKEN not in joined, "bot token leaked into the log"
        assert "REDACTED" in joined
