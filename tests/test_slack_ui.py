import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from preview_backend import create_backend, DEBUG, REPORT_INPUT
from slack_contract import WorkflowError, RequestContext, CleanupItem, CleanupView, ApprovalResult
from slack_ui import (
    SlackUI, PREVIEW, APPROVE_REPORT, APPROVE_CLEANUP, report_card, cleanup_card,
    register_handlers,
)


def command(text="status", trigger="req-1"):
    return dict(text=text, user_id="U123", team_id="T123", channel_id="C123", trigger_id=trigger)


def action(action_id, identifier, revision, user="U123", channel="C123", team="T123"):
    return {
        "user": {"id": user}, "team": {"id": team}, "channel": {"id": channel},
        "trigger_id": "button-1", "message": {"ts": "1234.5678"},
        "actions": [{"action_id": action_id, "value": json.dumps({"id": identifier, "revision": revision})}],
    }


class SlackUITests(unittest.TestCase):
    def setUp(self):
        self.backend = create_backend()
        self.ui = SlackUI(self.backend, expected_team_id="T123")
        self.ack = Mock()
        self.respond = Mock()
        self.client = Mock()
        self.client.chat_postMessage.return_value = {"ts": "1234.5678"}
        self.context = RequestContext("U123", "T123", "C123", "fixture-1")

    def call_command(self, body):
        self.ui.command(self.ack, body, self.client, self.respond)

    def call_action(self, body):
        self.ui.action(self.ack, body, self.client, self.respond)

    def test_ack_happens_before_any_backend_call(self):
        original = self.backend.get_status
        def check(context):
            self.ack.assert_called_once_with()
            return original(context)
        self.backend.get_status = check
        self.call_command(command())
        self.assertEqual(self.respond.call_args.kwargs["response_type"], "ephemeral")
        self.assertIn(PREVIEW, json.dumps(self.respond.call_args.kwargs))
        self.client.chat_postMessage.assert_not_called()

    def test_report_posts_loading_card_then_updates_same_message(self):
        self.call_command(command("report"))
        self.ack.assert_called_once_with()
        self.assertEqual(self.client.chat_postMessage.call_args.kwargs["channel"], "C123")
        update = self.client.chat_update.call_args.kwargs
        self.assertEqual(update["ts"], "1234.5678")
        self.assertIn(APPROVE_REPORT, json.dumps(update))

    def test_cleanup_only_review_items_receive_buttons(self):
        self.backend.start_report(self.context)
        self.call_command(command("cleanup"))
        blocks = self.client.chat_update.call_args.kwargs["blocks"]
        buttons = [e for b in blocks if b["type"] == "actions" for e in b["elements"]]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["action_id"], APPROVE_CLEANUP)
        self.assertIn("DEFERRED", json.dumps(blocks))
        self.assertIn("BLOCKED", json.dumps(blocks))

    def test_wrong_workspace_does_not_reach_backend(self):
        self.backend.start_report = Mock()
        self.call_command({**command("report"), "team_id": "T999"})
        self.backend.start_report.assert_not_called()
        self.client.chat_postMessage.assert_not_called()
        self.assertIn("different workspace", self.respond.call_args.kwargs["text"])

    def test_status_offers_current_review_without_creating_duplicate_work(self):
        report = self.backend.start_report(self.context)
        self.backend.start_cleanup(replace(self.context, request_id="cleanup-status"))
        self.call_command(command("status"))
        result = self.respond.call_args.kwargs
        self.assertIn(APPROVE_REPORT, json.dumps(result))
        self.assertIn(APPROVE_CLEANUP, json.dumps(result))
        self.assertEqual(len(self.backend.get_status(self.context).reports), 1)
        self.assertLessEqual(len(result["blocks"]), 50)

    def test_ephemeral_approval_uses_response_url_and_keeps_outcome_private(self):
        report = self.backend.start_report(self.context)
        body = action(APPROVE_REPORT, report.task_id, report.revision)
        body["container"] = {"is_ephemeral": True}
        self.call_action(body)
        self.client.chat_update.assert_not_called()
        self.client.chat_postMessage.assert_not_called()
        self.assertTrue(self.respond.call_args_list[0].kwargs["replace_original"])
        self.assertFalse(self.respond.call_args_list[1].kwargs["replace_original"])
        self.assertEqual(self.respond.call_args_list[1].kwargs["response_type"], "ephemeral")

    def test_unknown_subcommand_does_not_start_work(self):
        self.call_command(command("report now"))
        self.client.chat_postMessage.assert_not_called()
        self.assertIn("Use /referee", self.respond.call_args.kwargs["text"])

    def test_button_identity_comes_from_slack_payload(self):
        report = self.backend.start_report(self.context)
        spy = Mock(wraps=self.backend.approve_report)
        self.backend.approve_report = spy
        self.call_action(action(APPROVE_REPORT, report.task_id, report.revision, user="U999"))
        self.ack.assert_called_once_with()
        self.assertEqual(spy.call_args.args[2].user_id, "U999")
        self.client.chat_update.assert_not_called()
        self.client.chat_postMessage.assert_not_called()
        self.assertIn("another user", self.respond.call_args.kwargs["text"])

    def test_approval_ack_before_backend_and_fresh_followup(self):
        report = self.backend.start_report(self.context)
        self.backend.start_cleanup(replace(self.context, request_id="cleanup-1"))
        original = self.backend.approve_report
        def check(*args):
            self.ack.assert_called_once_with()
            return original(*args)
        self.backend.approve_report = check
        self.call_action(action(APPROVE_REPORT, report.task_id, report.revision))
        update = self.client.chat_update.call_args.kwargs
        self.assertNotIn(APPROVE_REPORT, json.dumps(update))
        self.assertIn("Approval simulated", json.dumps(update))
        self.assertNotIn("Published", json.dumps(update))
        self.assertEqual(self.client.chat_postMessage.call_count, 2)
        followup = self.client.chat_postMessage.call_args.kwargs
        self.assertEqual(followup["thread_ts"], "1234.5678")
        self.assertIn(APPROVE_CLEANUP, json.dumps(followup))
        self.assertIn(PREVIEW, json.dumps(followup))

    def test_stale_action_leaves_shared_card_intact(self):
        report = self.backend.start_report(self.context)
        self.call_action(action(APPROVE_REPORT, report.task_id, "old-version"))
        self.client.chat_update.assert_not_called()
        self.client.chat_postMessage.assert_not_called()
        self.assertEqual(self.respond.call_args.kwargs["response_type"], "ephemeral")

    def test_malformed_button_does_not_reach_backend(self):
        self.backend.approve_report = Mock()
        for invalid in ('null', '{}', '[]', '{"id":"x","revision":"1","user_id":"U123"}', 'not json'):
            body = action(APPROVE_REPORT, "x", "1")
            body["actions"][0]["value"] = invalid
            self.call_action(body)
        self.backend.approve_report.assert_not_called()

    def test_backend_error_removes_spinner_and_hides_exception_details(self):
        self.backend.start_report = Mock(side_effect=RuntimeError("secret-token-value"))
        self.call_command(command("report"))
        self.assertIn("interrupted", json.dumps(self.client.chat_update.call_args.kwargs))
        self.assertNotIn("secret-token-value", json.dumps(self.respond.call_args.kwargs))

    def test_rejected_result_is_private(self):
        self.backend.approve_cleanup = Mock(return_value=ApprovalResult("Not authorized", accepted=False))
        self.call_action(action(APPROVE_CLEANUP, "action-1", "1"))
        self.client.chat_update.assert_not_called()
        self.client.chat_postMessage.assert_not_called()
        self.assertEqual(self.respond.call_args.kwargs["response_type"], "ephemeral")

    def test_backend_committed_but_slack_update_failed_does_not_execute_again(self):
        batch = self.backend.start_cleanup(self.context)
        item = next(i for i in batch.items if i.path == DEBUG)
        original = Mock(wraps=self.backend.approve_cleanup)
        self.backend.approve_cleanup = original
        self.client.chat_update.side_effect = RuntimeError("Slack unavailable")
        self.call_action(action(APPROVE_CLEANUP, item.action_id, item.revision))
        self.assertEqual(original.call_count, 1)
        self.assertIn("status before retrying", self.respond.call_args.kwargs["text"])
        self.assertEqual(self.backend.get_status(self.context).cleanups[0].items[-1].verdict, "ALLOW")

    def test_request_id_stable_on_redelivery_and_scoped_to_user(self):
        first = self.ui._context_from(command("report"))
        repeated = self.ui._context_from(command("report"))
        other = self.ui._context_from({**command("report"), "user_id": "U999"})
        self.assertEqual(first.request_id, repeated.request_id)
        self.assertNotEqual(first.request_id, other.request_id)

    def test_untrusted_text_is_literal_and_bounded(self):
        report = self.backend.start_report(self.context)
        report = replace(report, draft_excerpt="<!channel> <@U999> " + "x" * 6000)
        card = report_card(report, preview=True)
        for block in card["blocks"]:
            if block["type"] == "section":
                self.assertEqual(block["text"]["type"], "plain_text")
                self.assertLessEqual(len(block["text"]["text"]), 3000)

    def test_large_cleanup_batches_are_explicitly_rejected(self):
        item = CleanupItem("a", "p", "REVIEW", "r", "1")
        with self.assertRaises(ValueError):
            cleanup_card(CleanupView("batch", "U123", (item,) * 13))

    def test_registers_expected_command_and_buttons(self):
        app = Mock()
        ui = register_handlers(app, self.backend)
        app.command.assert_called_once_with("/referee")
        self.assertEqual({call.args[0] for call in app.action.call_args_list}, {APPROVE_REPORT, APPROVE_CLEANUP})
        self.assertIs(ui.backend, self.backend)


if __name__ == "__main__":
    unittest.main()
