"""Run: python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from executor import RefereeService, SafetyError


class RefereeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ws = root / "demo_workspace"
        for directory in ("data", "working", "scratch", "reports"):
            (self.ws / directory).mkdir(parents=True)
        (self.ws / "data/source_metrics.csv").write_text("day,count\nMonday,12\n")
        (self.ws / "working/report_input.csv").write_text("day,count\nMonday,12\n")
        (self.ws / "scratch/debug.log").write_text("debug\n")
        self.service = RefereeService(self.ws, root / "private_state",
                                      allowed_approver_ids={"U_REPORT", "U_CLEANUP"})

    def propose(self, path="scratch/debug.log", operation="quarantine", **kwargs):
        return self.service.evaluate_action(operation, path, agent_id="cleanup-agent",
                                             requested_by_slack_id="U_CLEANUP", **kwargs)

    def approve(self, proposal, user="U_CLEANUP"):
        return self.service.execute_approved_action(proposal["action_id"], user)

    def test_protected_source_cannot_be_approved(self):
        before = self.service.fingerprint("data/source_metrics.csv")
        result = self.propose("data/source_metrics.csv")
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["reason_code"], "protected_path")
        self.assertFalse(self.approve(result)["executed"])
        self.assertEqual(before, self.service.fingerprint("data/source_metrics.csv"))

    def test_protected_subtree_cannot_receive_new_files(self):
        result = self.propose("data/new.csv", "create", content="modified data")
        self.assertEqual(result["reason_code"], "protected_path")
        self.assertFalse((self.ws / "data/new.csv").exists())

    def test_active_dependency_is_deferred(self):
        self.service.register_task("report-1", "U_REPORT", ["working/report_input.csv"])
        result = self.propose("working/report_input.csv")
        self.assertEqual(result["verdict"], "DEFER")
        self.assertEqual(result["blocking_task_ids"], ["report-1"])
        self.assertFalse(self.approve(result)["executed"])
        self.assertTrue((self.ws / "working/report_input.csv").exists())

    def test_safe_quarantine_preserves_contents_and_audit(self):
        proposal = self.propose()
        self.assertEqual(proposal["verdict"], "REVIEW")
        self.assertTrue((self.ws / "scratch/debug.log").exists())
        result = self.approve(proposal)
        self.assertTrue(result["executed"])
        self.assertFalse((self.ws / "scratch/debug.log").exists())
        self.assertEqual(Path(result["backup_path"]).read_text(), "debug\n")
        rows = [json.loads(x) for x in self.service.audit_path.read_text().splitlines()]
        self.assertTrue(any(x["event"] == "execution_completed" for x in rows))

    def test_wrong_human_cannot_approve(self):
        proposal = self.propose()
        self.assertEqual(self.approve(proposal, "U_REPORT")["reason_code"], "unauthorized_approver")
        self.assertTrue((self.ws / "scratch/debug.log").exists())
        self.assertTrue(self.approve(proposal)["executed"])

    def test_unknown_requester_cannot_create_proposal(self):
        with self.assertRaises(PermissionError):
            self.service.evaluate_action("quarantine", "scratch/debug.log",
                                         agent_id="cleanup-agent", requested_by_slack_id="U_UNKNOWN")

    def test_changed_file_invalidates_old_approval(self):
        proposal = self.propose()
        (self.ws / "scratch/debug.log").write_text("new content")
        result = self.approve(proposal)
        self.assertEqual(result["reason_code"], "stale_file")
        self.assertFalse(result["executed"])
        self.assertEqual((self.ws / "scratch/debug.log").read_text(), "new content")

    def test_read_time_hash_is_checked(self):
        snapshot = self.service.fingerprint("scratch/debug.log")
        (self.ws / "scratch/debug.log").write_text("changed since agent read")
        result = self.propose(expected_hash=snapshot)
        self.assertEqual(result["reason_code"], "stale_file")

    def test_repeat_approval_executes_only_once(self):
        proposal = self.propose()
        first = self.approve(proposal)
        second = self.approve(proposal)
        self.assertTrue(first["executed"])
        self.assertTrue(second["already_executed"])
        self.assertEqual(first["backup_path"], second["backup_path"])
        rows = [json.loads(x) for x in self.service.audit_path.read_text().splitlines()]
        self.assertEqual(sum(x["event"] == "execution_completed" for x in rows), 1)

    def test_concurrent_approvals_execute_once(self):
        proposal = self.propose()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.approve(proposal), range(6)))
        self.assertEqual(sum(not r["already_executed"] for r in results), 1)

    def test_dependency_added_after_review_still_prevents_execution(self):
        proposal = self.propose()
        self.service.register_task("investigate", "U_REPORT", ["scratch/debug.log"])
        result = self.approve(proposal)
        self.assertEqual(result["verdict"], "DEFER")
        self.assertFalse(result["executed"])

    def test_dependency_release_requires_new_review(self):
        self.service.register_task("report-1", "U_REPORT", ["working/report_input.csv"])
        deferred = self.propose("working/report_input.csv")
        self.service.complete_task("report-1", "U_REPORT")
        # Completing a task alone never approves a pending mutation.
        self.assertEqual(self.approve(deferred)["verdict"], "DEFER")
        fresh = self.service.reevaluate_action(deferred["action_id"])
        self.assertNotEqual(deferred["action_id"], fresh["action_id"])
        self.assertEqual(fresh["verdict"], "REVIEW")
        self.assertEqual(self.approve(deferred)["reason_code"], "superseded_action")
        self.assertTrue(self.approve(fresh)["executed"])

    def test_wrong_owner_cannot_release_dependency(self):
        self.service.register_task("report-1", "U_REPORT", ["working/report_input.csv"])
        with self.assertRaises(PermissionError):
            self.service.complete_task("report-1", "U_CLEANUP")
        self.assertEqual(self.propose("working/report_input.csv")["verdict"], "DEFER")

    def test_path_escape_and_ambiguous_paths_blocked(self):
        for path in ("../outside.txt", "/tmp/file.txt", "C:/data/file.txt", "scratch/../debug.log",
                     "scratch\\debug.log", "scratch//debug.log", "scratch/./debug.log", "scratch/NUL"):
            with self.subTest(path=path):
                self.assertEqual(self.propose(path)["verdict"], "BLOCK")

    def test_symlink_is_blocked(self):
        target = self.ws / "scratch/alias.log"
        try:
            target.symlink_to(self.ws / "data/source_metrics.csv")
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit symlinks")
        result = self.propose("scratch/alias.log")
        self.assertEqual(result["reason_code"], "symlink_path")

    def test_symlinked_parent_is_blocked(self):
        target = self.ws / "alias"
        try:
            target.symlink_to(self.ws / "data", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit symlinks")
        self.assertEqual(self.propose("alias/source_metrics.csv")["reason_code"], "symlink_path")

    def test_hardlink_is_blocked(self):
        try:
            os.link(self.ws / "data/source_metrics.csv", self.ws / "scratch/hardlink.log")
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit hardlinks")
        self.assertEqual(self.propose("scratch/hardlink.log")["reason_code"], "hardlinked_file")

    def test_shell_and_permanent_delete_unsupported(self):
        for operation in ("delete", "rm", "shell", "remove_directory"):
            self.assertEqual(self.propose(operation=operation)["reason_code"], "unsupported_operation")

    def test_directory_quarantine_not_supported(self):
        self.assertEqual(self.propose("scratch")["reason_code"], "not_regular_file")

    def test_create_then_replace_keeps_backup(self):
        create = self.propose("reports/client_update.md", "create", content="# Draft\n")
        self.assertTrue(self.approve(create)["executed"])
        conflict = self.propose("reports/client_update.md", "create", content="overwrite")
        self.assertEqual(conflict["reason_code"], "target_exists")
        update = self.propose("reports/client_update.md", "replace", content="# Approved\n")
        result = self.approve(update)
        self.assertTrue(result["executed"])
        self.assertEqual((self.ws / "reports/client_update.md").read_text(), "# Approved\n")
        self.assertEqual(Path(result["backup_path"]).read_text(), "# Draft\n")

    def test_metadata_filename_does_not_overwrite_backup(self):
        (self.ws / "scratch/metadata.json").write_text('{"important":"old"}')
        proposal = self.propose("scratch/metadata.json", "replace", content='{"new":true}')
        result = self.approve(proposal)
        self.assertEqual(Path(result["backup_path"]).read_text(), '{"important":"old"}')

    def test_returned_dictionary_is_not_an_authorization_token(self):
        proposal = self.propose("data/source_metrics.csv")
        proposal["verdict"] = "REVIEW"
        proposal["operation"] = "quarantine"
        self.assertFalse(self.approve(proposal)["executed"])

    def test_missing_audit_prevents_execution(self):
        proposal = self.propose()
        old_audit = self.service._audit
        def fail(*args, **kwargs):
            raise OSError("disk unavailable")
        self.service._audit = fail
        try:
            with self.assertRaises(OSError):
                self.approve(proposal)
        finally:
            self.service._audit = old_audit
        self.assertTrue((self.ws / "scratch/debug.log").exists())

    def test_unknown_action_fails_closed(self):
        result = self.service.execute_approved_action("old-message-id", "U_CLEANUP")
        self.assertEqual(result["reason_code"], "unknown_action")
        self.assertFalse(result["executed"])

    def test_state_inside_workspace_rejected(self):
        with self.assertRaises(ValueError):
            RefereeService(self.ws, self.ws / "state", allowed_approver_ids={"U_REPORT"})

    def test_multiple_active_tasks_are_all_reported_as_blockers(self):
        self.service.register_task("report-a", "U_REPORT", ["working/report_input.csv"])
        self.service.register_task("report-b", "U_CLEANUP", ["working/report_input.csv"])
        result = self.propose("working/report_input.csv")
        self.assertEqual(result["verdict"], "DEFER")
        self.assertEqual(result["reason_code"], "active_dependency")
        self.assertEqual(result["blocking_task_ids"], ["report-a", "report-b"])

    def test_completing_one_of_multiple_blockers_still_defers(self):
        self.service.register_task("report-a", "U_REPORT", ["working/report_input.csv"])
        self.service.register_task("report-b", "U_CLEANUP", ["working/report_input.csv"])
        first = self.propose("working/report_input.csv")
        self.assertEqual(first["blocking_task_ids"], ["report-a", "report-b"])

        self.service.complete_task("report-a", "U_REPORT")
        reassessed = self.service.reevaluate_action(first["action_id"])
        self.assertEqual(reassessed["verdict"], "DEFER")
        self.assertEqual(reassessed["blocking_task_ids"], ["report-b"])
        self.assertTrue((self.ws / "working/report_input.csv").exists())

    def test_missing_cleanup_target_fails_closed(self):
        result = self.propose("scratch/already_gone.log")
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["reason_code"], "missing_file")
        self.assertFalse(result["executed"])

    def test_oversized_file_is_not_read_or_mutated(self):
        big = self.ws / "scratch/big.log"
        big.write_bytes(b"x" * (self.service.max_file_bytes + 1))
        result = self.propose("scratch/big.log")
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["reason_code"], "file_too_large")
        self.assertTrue(big.exists())
        self.assertEqual(big.stat().st_size, self.service.max_file_bytes + 1)

    def test_same_basename_quarantines_do_not_collide(self):
        (self.ws / "working/debug.log").write_text("working copy\n")
        first = self.propose("scratch/debug.log")
        second = self.propose("working/debug.log")
        first_result = self.approve(first)
        second_result = self.approve(second)

        self.assertTrue(first_result["executed"])
        self.assertTrue(second_result["executed"])
        self.assertNotEqual(first_result["backup_path"], second_result["backup_path"])
        self.assertEqual(Path(first_result["backup_path"]).read_text(), "debug\n")
        self.assertEqual(Path(second_result["backup_path"]).read_text(), "working copy\n")


if __name__ == "__main__":
    unittest.main()
