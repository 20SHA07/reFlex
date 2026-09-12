"""Behavioral checks for the in-memory sample, not real security validation."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from preview_backend import DEBUG, REPORT_INPUT, SOURCE, create_backend
from slack_contract import RequestContext, WorkflowError


class PreviewBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = create_backend()
        self.context = RequestContext("U1", "T1", "C1", "request-1")

    def item(self, batch, path):
        return next(item for item in batch.items if item.path == path)

    def test_report_approval_releases_dependency_but_does_not_execute_cleanup(self):
        report = self.backend.start_report(self.context)
        batch = self.backend.start_cleanup(self.context)
        original = self.item(batch, REPORT_INPUT)
        self.assertEqual(original.verdict, "DEFER")
        self.assertEqual(self.item(batch, SOURCE).verdict, "BLOCK")
        result = self.backend.approve_report(report.task_id, report.revision, self.context)
        fresh = self.item(result.followups[0], REPORT_INPUT)
        self.assertEqual(fresh.verdict, "REVIEW")
        self.assertNotEqual(original.revision, fresh.revision)
        self.assertEqual(result.report.status, "completed")
        self.assertIsNone(result.audit_id)
        with self.assertRaises(WorkflowError):
            self.backend.approve_cleanup(original.action_id, original.revision, self.context)

    def test_new_dependency_invalidates_existing_cleanup_card(self):
        batch = self.backend.start_cleanup(self.context)
        old = self.item(batch, REPORT_INPUT)
        self.assertEqual(old.verdict, "REVIEW")
        self.backend.start_report(self.context)
        with self.assertRaises(WorkflowError):
            self.backend.approve_cleanup(old.action_id, old.revision, self.context)
        current = self.backend.get_status(self.context).cleanups[0]
        self.assertEqual(self.item(current, REPORT_INPUT).verdict, "DEFER")

    def test_other_active_report_keeps_input_held_and_views_private(self):
        first = self.backend.start_report(self.context)
        other = replace(self.context, user_id="U2", request_id="request-2")
        second = self.backend.start_report(other)
        self.backend.start_cleanup(self.context)
        other_batch = self.backend.start_cleanup(other)
        result = self.backend.approve_report(first.task_id, first.revision, self.context)
        self.assertEqual(len(result.followups), 1)
        self.assertEqual(self.item(result.followups[0], REPORT_INPUT).verdict, "DEFER")
        self.assertNotEqual(result.followups[0].batch_id, other_batch.batch_id)
        self.backend.approve_report(second.task_id, second.revision, other)
        state = self.backend.get_status(self.context)
        self.assertEqual(len(state.reports), 1)
        self.assertEqual(self.item(state.cleanups[0], REPORT_INPUT).verdict, "REVIEW")

    def test_wrong_owner_channel_and_team_cannot_approve_or_read(self):
        report = self.backend.start_report(self.context)
        batch = self.backend.start_cleanup(self.context)
        debug = self.item(batch, DEBUG)
        for change in ({"user_id": "U2"}, {"channel_id": "C2"}, {"team_id": "T2"}):
            with self.subTest(change=change):
                outsider = replace(self.context, **change)
                with self.assertRaises(WorkflowError):
                    self.backend.approve_report(report.task_id, report.revision, outsider)
                with self.assertRaises(WorkflowError):
                    self.backend.approve_cleanup(debug.action_id, debug.revision, outsider)
                state = self.backend.get_status(outsider)
                self.assertEqual(state.reports, ())
                self.assertEqual(state.cleanups, ())

    def test_commands_and_approval_retries_do_not_repeat_transitions(self):
        report = self.backend.start_report(self.context)
        self.assertEqual(report, self.backend.start_report(self.context))
        batch = self.backend.start_cleanup(self.context)
        self.assertEqual(batch, self.backend.start_cleanup(self.context))
        debug = self.item(batch, DEBUG)
        first = self.backend.approve_cleanup(debug.action_id, debug.revision, self.context)
        second = self.backend.approve_cleanup(debug.action_id, debug.revision, self.context)
        self.assertTrue(first.accepted and second.accepted)
        self.assertEqual(first.cleanup, second.cleanup)
        self.assertEqual(self.item(second.cleanup, DEBUG).revision, "2")
        self.assertEqual(self.item(second.cleanup, DEBUG).verdict, "ALLOW")
        approved = self.backend.approve_report(report.task_id, report.revision, self.context)
        retried = self.backend.approve_report(report.task_id, report.revision, self.context)
        self.assertTrue(approved.accepted and retried.accepted)
        self.assertEqual(approved.report, retried.report)
        self.assertEqual(retried.followups, ())
        self.assertEqual(len(self.backend.get_status(self.context).reports), 1)
        outsider = replace(self.context, user_id="U2")
        with self.assertRaises(WorkflowError):
            self.backend.approve_cleanup(debug.action_id, debug.revision, outsider)

    def test_protected_deferred_and_unknown_actions_rejected(self):
        self.backend.start_report(self.context)
        batch = self.backend.start_cleanup(self.context)
        for path in (SOURCE, REPORT_INPUT):
            item = self.item(batch, path)
            with self.assertRaises(WorkflowError):
                self.backend.approve_cleanup(item.action_id, item.revision, self.context)
        with self.assertRaises(WorkflowError):
            self.backend.approve_cleanup("unknown", "1", self.context)

    def test_stale_report_revision_rejected(self):
        report = self.backend.start_report(self.context)
        with self.assertRaises(WorkflowError):
            self.backend.approve_report(report.task_id, "old-draft", self.context)
        self.assertEqual(self.backend.get_status(self.context).reports[0].status, "awaiting_approval")

    def test_cards_from_previous_session_cannot_approve_new_objects(self):
        old_report = self.backend.start_report(self.context)
        old_batch = self.backend.start_cleanup(self.context)
        old_debug = self.item(old_batch, DEBUG)

        restarted = create_backend()
        new_report = restarted.start_report(self.context)
        new_batch = restarted.start_cleanup(self.context)
        self.assertNotEqual(old_report.task_id, new_report.task_id)
        self.assertNotEqual(old_batch.batch_id, new_batch.batch_id)

        with self.assertRaises(WorkflowError):
            restarted.approve_report(old_report.task_id, old_report.revision, self.context)
        with self.assertRaises(WorkflowError):
            restarted.approve_cleanup(old_debug.action_id, old_debug.revision, self.context)

        state = restarted.get_status(self.context)
        self.assertEqual(state.reports[0].status, "awaiting_approval")
        self.assertEqual(self.item(state.cleanups[0], DEBUG).verdict, "REVIEW")

    def test_preview_performs_no_filesystem_io(self):
        forbidden = AssertionError("Preview must not access files")
        with patch("builtins.open", side_effect=forbidden), \
             patch("os.remove", side_effect=forbidden), \
             patch("os.unlink", side_effect=forbidden), \
             patch("os.rename", side_effect=forbidden), \
             patch("os.replace", side_effect=forbidden), \
             patch("shutil.move", side_effect=forbidden):
            backend = create_backend()
            report = backend.start_report(self.context)
            backend.start_cleanup(self.context)
            result = backend.approve_report(report.task_id, report.revision, self.context)
            item = self.item(result.followups[0], DEBUG)
            approved = backend.approve_cleanup(item.action_id, item.revision, self.context)
            self.assertTrue(backend.preview)
            self.assertIsNone(approved.audit_id)
            self.assertIn("No files were moved", approved.message)


if __name__ == "__main__":
    unittest.main()
