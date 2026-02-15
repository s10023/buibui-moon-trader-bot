from unittest.mock import patch


class TestSendTelegramMessage:
    """Tests for send_telegram_message()."""

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram.BOT_TOKEN", "test_token")
    @patch("utils.telegram.CHAT_ID", "12345")
    def test_successful_send(self, mock_post):
        """Message is sent with correct payload."""
        from utils.telegram import send_telegram_message

        send_telegram_message("Hello test")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://api.telegram.org/bottest_token/sendMessage"
        payload = call_kwargs[1]["data"]
        assert payload["chat_id"] == "12345"
        assert payload["text"] == "Hello test"
        assert payload["parse_mode"] == "Markdown"
        assert payload["disable_web_page_preview"] is True
        assert call_kwargs[1]["timeout"] == 10

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram.BOT_TOKEN", None)
    @patch("utils.telegram.CHAT_ID", "12345")
    def test_missing_bot_token(self, mock_post):
        """No HTTP call when BOT_TOKEN is missing."""
        from utils.telegram import send_telegram_message

        send_telegram_message("Hello")
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram.BOT_TOKEN", "test_token")
    @patch("utils.telegram.CHAT_ID", None)
    def test_missing_chat_id(self, mock_post):
        """No HTTP call when CHAT_ID is missing."""
        from utils.telegram import send_telegram_message

        send_telegram_message("Hello")
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram.BOT_TOKEN", "")
    @patch("utils.telegram.CHAT_ID", "")
    def test_empty_credentials(self, mock_post):
        """No HTTP call when credentials are empty strings."""
        from utils.telegram import send_telegram_message

        send_telegram_message("Hello")
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post", side_effect=Exception("Network error"))
    @patch("utils.telegram.BOT_TOKEN", "test_token")
    @patch("utils.telegram.CHAT_ID", "12345")
    def test_network_error_handled(self, mock_post):
        """Network errors are caught and logged, not raised."""
        from utils.telegram import send_telegram_message

        # Should not raise
        send_telegram_message("Hello")

    @patch("utils.telegram.requests.post", side_effect=TimeoutError("Timeout"))
    @patch("utils.telegram.BOT_TOKEN", "test_token")
    @patch("utils.telegram.CHAT_ID", "12345")
    def test_timeout_handled(self, mock_post):
        """Timeout errors are caught and logged, not raised."""
        from utils.telegram import send_telegram_message

        send_telegram_message("Hello")
