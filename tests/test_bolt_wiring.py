"""Exercise the real Bolt listener adapter without connecting to Slack.

SDK dependency is optional for the original offline suite, mandatory in CI.
No tokens or outbound Slack requests are used here.
"""

import importlib.util
import threading
import unittest
from unittest.mock import patch

from preview_backend import create_backend
from slack_ui import register_handlers


@unittest.skipUnless(importlib.util.find_spec("slack_bolt"), "Slack SDK is not installed")
class BoltWiringTests(unittest.TestCase):
    def test_real_bolt_dispatches_command_and_acknowledges(self):
        from slack_bolt import App
        from slack_bolt.authorization import AuthorizeResult
        from slack_bolt.request import BoltRequest

        completed = threading.Event()
        responded = threading.Event()
        backend = create_backend()
        original = backend.get_status

        def get_status(context):
            result = original(context)
            completed.set()
            return result

        backend.get_status = get_status

        def authorize(enterprise_id, team_id, user_id):
            return AuthorizeResult(
                enterprise_id=enterprise_id, team_id=team_id,
                bot_token="xoxb-unit-test", bot_id="BREFEREE", bot_user_id="UREFEREE",
            )

        # Verification is disabled ONLY for this synthetic fixture, never for
        # the live create_app / SocketModeHandler path.
        app = App(authorize=authorize, request_verification_enabled=False)
        register_handlers(app, backend, expected_team_id="T123")
        body = {
            "command": "/referee", "text": "status", "user_id": "U123",
            "team_id": "T123", "channel_id": "C123", "trigger_id": "fixture-1",
            "response_url": "https://example.invalid/slack-test-response",
        }
        def capture_send(*args, **kwargs):
            responded.set()

        with patch("slack_sdk.webhook.WebhookClient.send", side_effect=capture_send) as send:
            response = app.dispatch(BoltRequest(body=body, mode="socket_mode"))
            self.assertEqual(response.status, 200)
            self.assertTrue(completed.wait(timeout=2), "Registered listener did not reach the backend")
            # Bolt may return the ack before it finishes rendering the response;
            # request completion is deliberately independent of backend work.
            self.assertTrue(responded.wait(timeout=2), "The listener did not send its status response")
            send.assert_called_once()
            self.assertIn("preview", send.call_args.kwargs["text"].lower())


if __name__ == "__main__":
    unittest.main()
