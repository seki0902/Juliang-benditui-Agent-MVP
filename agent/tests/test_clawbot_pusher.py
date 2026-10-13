from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import clawbot_pusher


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ClawBotPusherTests(unittest.TestCase):
    def test_loads_token_from_configured_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "token.txt"
            token_path.write_text("test-token-1234567890\n", encoding="utf-8")
            with patch.dict("os.environ", {"PUSHPLUS_TOKEN_FILE": str(token_path)}, clear=True):
                self.assertEqual(clawbot_pusher.load_pushplus_config().token, "test-token-1234567890")

    @patch("clawbot_pusher.urlopen")
    def test_sends_plaintext_to_clawbot_channel(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _Response({"code": 200, "data": {"messageId": "msg-1"}})
        report = {"data_date": "2026-09-14", "yesterday": {"spend": 100.0, "leads": 5}, "anomalies": []}
        with patch.dict("os.environ", {"PUSHPLUS_TOKEN": "test-token-1234567890"}, clear=True):
            result = clawbot_pusher.push_daily_report(report, [])
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(payload["channel"], "clawbot")
        self.assertEqual(payload["template"], "txt")
        self.assertEqual(request.get_method(), "POST")

    @patch("clawbot_pusher.urlopen")
    def test_uses_rendered_report_as_message_body_when_provided(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _Response({"code": 200, "data": {"messageId": "msg-rendered"}})
        report = {"data_date": "2026-09-18", "yesterday": {}, "anomalies": []}
        final_report = "# Rendered report\n\n[Today focus]\nKeep observing.\n"
        with patch.dict("os.environ", {"PUSHPLUS_TOKEN": "test-token-1234567890"}, clear=True):
            result = clawbot_pusher.push_daily_report(
                report,
                [],
                title="Rendered title",
                final_report=final_report,
            )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(payload["title"], "Rendered title")
        self.assertEqual(payload["content"], final_report)
