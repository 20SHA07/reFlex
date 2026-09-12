"""Offline agent/bridge/executor integration and nonblocking HTTP job checks."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents import CleanupProposal, Explanation, ReFlexAgents, ReportDraft
from browser_bridge import BridgeError, BridgeServer, BrowserBridge, PATHS
from demo_agents import ScriptedClient


CONTEXT = {"workspace_id": "T123", "channel_id": "C123",
           "url": "https://app.slack.com/client/T123/C123"}


def request(operation, **extra):
    return {"operation": operation, "context": dict(CONTEXT),
            "request_id": uuid.uuid4().hex, **extra}


def target(item):
    return {"id": item["id"], "revision": item["revision"]}


class FakeAgents:
    def __init__(self):
        self.report_error = None
        self.cleanup_error = None
        self.explanation_error = None
        self.paths = tuple(PATHS)
        self.report_calls = 0
        self.cleanup_calls = 0
        self.explanation_calls = 0
        self.before_report = lambda: None
        self.before_cleanup = lambda: None
        self.report_entered = threading.Event()
        self.report_release = threading.Event()
        self.report_release.set()

    def run_report(self, task_id, *, input_path, input_text, output_path, request_text=""):
        self.report_calls += 1
        self.report_args = dict(task_id=task_id, input_path=input_path, input_text=input_text,
                                output_path=output_path, request_text=request_text)
        self.before_report()
        self.report_entered.set()
        if not self.report_release.wait(5):
            raise RuntimeError("Test failed to release the fake report")
        if self.report_error:
            raise self.report_error
        return ReportDraft(task_id, input_path, output_path, "# Reviewed AI-shaped draft\n\nTotal: 45.\n")

    def run_cleanup(self, task_id, inventory, *, fault_injection=False):
        self.cleanup_calls += 1
        self.cleanup_inventory = tuple(inventory)
        self.fault_injection = fault_injection
        self.before_cleanup()
        if self.cleanup_error:
            raise self.cleanup_error
        return CleanupProposal(task_id, self.paths, "Test cleanup candidates for referee review.")

    def explain_cleanup(self, rows):
        self.explanation_calls += 1
        self.explanation_rows = tuple(rows)
        if self.explanation_error:
            raise self.explanation_error
        return Explanation("Test model explanation: keep the shared activity log until the report is approved.",
                           "wait_for_dependency", True)


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.agents = FakeAgents()
        self.bridge = BrowserBridge(ai_agents=self.agents)
        self.addCleanup(shutil.rmtree, self.bridge.root)

    def dispatch(self, operation, **extra):
        return self.bridge.dispatch(request(operation, **extra))["state"]

    def session(self):
        return self.bridge.sessions[("T123", "C123")]

    def test_report_reserves_input_before_agent_and_publishes_only_reviewed_content(self):
        self.dispatch("status")
        session = self.session()
        self.agents.before_report = lambda: self.assertEqual(
            session.referee.status()["active_tasks"][0]["required_files"], [PATHS[0]])
        state = self.dispatch("start_report", input={"text": "Summarize this channel's sample data."})
        report = state["report"]
        self.assertEqual(state["generation"], "openrouter")
        self.assertEqual(self.agents.report_args["input_text"], session.referee.read_text(PATHS[0])["text"])
        self.assertEqual(self.agents.report_args["request_text"], "Summarize this channel's sample data.")
        self.assertFalse((session.workspace / report["output_path"]).exists())
        final = self.dispatch("approve_report", target=target(report))
        self.assertEqual(final["report"]["status"], "completed")
        self.assertEqual((session.workspace / report["output_path"]).read_text(), report["draft"])
        self.assertEqual(session.referee.status()["active_tasks"], [])

    def test_actual_scripted_agent_tool_loop_composes_with_real_referee_and_approvals(self):
        for fault_injection in (False, True):
            with self.subTest(fault_injection=fault_injection):
                self.bridge = BrowserBridge(ai_agents=ReFlexAgents(ScriptedClient()),
                                            fault_injection=fault_injection)
                self.addCleanup(shutil.rmtree, self.bridge.root)
                initial = self.dispatch("start_report")
                session = self.session()
                report = initial["report"]
                source_hash = session.referee.fingerprint(PATHS[0])
                original_log = (session.workspace / PATHS[0]).read_bytes()
                self.assertEqual(report["input_path"], "logs/agent_activity.log")
                self.assertIn("OFFLINE SCRIPT", report["draft"])
                self.assertFalse((session.workspace / report["output_path"]).exists())
                reviewed = self.dispatch("start_cleanup")
                [held] = reviewed["cleanup"]["items"]
                self.assertEqual(held["path"], report["input_path"])
                self.assertEqual(held["verdict"], "DEFER")
                self.assertEqual(reviewed["cleanup"]["proposal_source"],
                                 "fault_injection" if fault_injection else "model")
                self.assertTrue(reviewed["cleanup"]["explanation_model_used"])
                self.assertIn("DEFER: " + PATHS[0], reviewed["cleanup"]["explanation"])
                with self.assertRaises(BridgeError) as denied:
                    self.dispatch("approve_cleanup", target=target(held))
                self.assertEqual(denied.exception.code, "approval_unavailable")
                self.assertEqual(session.referee.fingerprint(PATHS[0]), source_hash)
                published = self.dispatch("approve_report", target=target(report))
                [fresh] = published["cleanup"]["items"]
                self.assertEqual(fresh["verdict"], "REVIEW")
                self.assertNotEqual(fresh["id"], held["id"])
                self.assertFalse(published["cleanup"]["explanation_model_used"])
                self.assertEqual(session.referee.status()["active_tasks"], [])
                self.assertEqual(session.referee.fingerprint(PATHS[0]), source_hash)
                with self.assertRaises(BridgeError) as stale:
                    self.dispatch("approve_cleanup", target=target(held))
                self.assertEqual(stale.exception.code, "stale_review")
                final = self.dispatch("approve_cleanup", target=target(fresh))
                self.assertTrue(final["cleanup"]["items"][0]["executed"])
                self.assertIn("already quarantined", final["cleanup"]["explanation"])
                self.assertFalse((session.workspace / PATHS[0]).exists())
                backup = session.referee.quarantine / fresh["id"] / "original.bin"
                self.assertEqual(backup.read_bytes(), original_log)
                self.assertEqual((session.workspace / report["output_path"]).read_text(), report["draft"])
                audit = [json.loads(line) for line in session.referee.audit_path.read_text().splitlines()]
                self.assertTrue(any(row["event"] == "execution_completed"
                                    and row["action_id"] == fresh["id"] for row in audit))

    def test_cleanup_failure_or_unscoped_paths_add_no_proposals_or_state(self):
        self.dispatch("start_report")
        before = copy.deepcopy(self.session().state)
        actions_before = self.session().referee.status()
        for mode in ("provider_failure", "unscoped_path"):
            with self.subTest(mode=mode):
                self.agents.cleanup_error = RuntimeError("private-provider-detail") if mode == "provider_failure" else None
                self.agents.paths = ("../outside.txt",) if mode == "unscoped_path" else tuple(PATHS)
                with self.assertRaises(BridgeError) as failed:
                    self.dispatch("start_cleanup")
                self.assertEqual(failed.exception.code, "agent_unavailable")
                self.assertNotIn("private-provider-detail", str(failed.exception))
                self.assertEqual(self.session().state, before)
                self.assertEqual(self.session().referee.status(), actions_before)
                self.assertTrue(all((self.session().workspace / path).exists() for path in PATHS))

    def test_cleanup_carries_pre_model_file_hash_and_blocks_changed_candidate(self):
        self.dispatch("status")
        self.agents.paths = (PATHS[0],)
        self.agents.before_cleanup = lambda: (self.session().workspace / PATHS[0]).write_text("new content")
        reviewed = self.dispatch("start_cleanup")
        item = reviewed["cleanup"]["items"][0]
        self.assertEqual(item["verdict"], "BLOCK")
        self.assertIn("changed", item["reason"])
        self.assertEqual((self.session().workspace / PATHS[0]).read_text(), "new content")

    def test_report_provider_failure_keeps_visible_lease_and_pauses_fixture(self):
        self.agents.report_error = RuntimeError("private-provider-detail")
        with self.assertRaises(BridgeError) as failed:
            self.dispatch("start_report")
        self.assertEqual(failed.exception.code, "restart_required")
        self.assertNotIn("private-provider-detail", str(failed.exception))
        state = self.dispatch("status")
        self.assertIsNone(state["report"])
        self.assertIn("paused", state["message"])
        self.assertEqual(len(self.session().referee.status()["active_tasks"]), 1)
        self.assertEqual(list((self.session().workspace / "reports").iterdir()), [])
        with self.assertRaises(BridgeError) as paused:
            self.dispatch("start_cleanup")
        self.assertEqual(paused.exception.code, "restart_required")

    def test_failed_explanation_keeps_rule_decisions_and_uses_labeled_fallback(self):
        self.dispatch("start_report")
        self.agents.explanation_error = RuntimeError("private-provider-detail")
        cleanup = self.dispatch("start_cleanup")["cleanup"]
        self.assertEqual([item["verdict"] for item in cleanup["items"]], ["DEFER"])
        self.assertFalse(cleanup["explanation_model_used"])
        self.assertTrue(cleanup["explanation_error"])
        self.assertNotIn("private-provider-detail", json.dumps(cleanup))
        for item in cleanup["items"]:
            self.assertIn(item["reason"], cleanup["explanation"])
        self.assertTrue(all((self.session().workspace / path).exists() for path in PATHS))

    def test_empty_cleanup_is_valid_and_does_not_call_explanation_model(self):
        self.agents.paths = ()
        cleanup = self.dispatch("start_cleanup")["cleanup"]
        self.assertEqual(cleanup["items"], [])
        self.assertFalse(cleanup["explanation_error"])
        self.assertEqual(self.agents.explanation_calls, 0)
        self.assertEqual(self.session().referee.status()["actions"], [])


class AsyncHttpTests(unittest.TestCase):
    def setUp(self):
        self.agents = FakeAgents()
        self.bridge = BrowserBridge(ai_agents=self.agents)
        self.server = BridgeServer(self.bridge, port=0, extension_id="a" * 32)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.agents.report_release.set()
        deadline = time.monotonic() + 5
        while self.bridge.pending_jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.bridge.root)

    def http(self, method, path, body=None, *, authorized=True, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        headers = {"Authorization": "Bearer " + self.bridge.token} if authorized else {}
        if origin is not None:
            headers["Origin"] = origin
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body)
        try:
            client.request(method, path, body=payload, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def result(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, result = self.http("GET", "/v1/jobs/" + job_id)
            if status != 202:
                return status, result
            time.sleep(0.01)
        self.fail("The offline job did not finish")

    def test_pending_job_does_not_block_http_and_duplicate_request_uses_one_agent_call(self):
        self.agents.report_release.clear()
        body = request("start_report")
        status, queued = self.http("POST", "/v1/dispatch", body)
        self.assertEqual(status, 202)
        self.assertTrue(self.agents.report_entered.wait(2))
        job_path = "/v1/jobs/" + queued["job_id"]
        self.assertEqual(self.http("GET", job_path), (202, queued))
        self.assertEqual(self.http("GET", job_path, authorized=False)[0], 401)
        self.assertEqual(self.http("GET", job_path, origin="https://app.slack.com")[0], 403)
        session_status, session = self.http("GET", "/v1/session")
        self.assertEqual(session_status, 200)
        self.assertEqual(session["generation"], "openrouter")
        self.assertEqual(self.http("POST", "/v1/dispatch", body), (202, queued))
        changed = {**body, "input": {"text": "Changed request"}}
        self.assertEqual(self.http("POST", "/v1/dispatch", changed)[0], 409)
        self.agents.report_release.set()
        status, completed = self.result(queued["job_id"])
        self.assertEqual(status, 200)
        self.assertEqual(completed["state"]["report"]["status"], "awaiting_approval")
        self.assertEqual(self.agents.report_calls, 1)
        self.assertNotIn(self.bridge.token, json.dumps(completed))
        approve_status, approval_job = self.http("POST", "/v1/dispatch",
            request("approve_report", target=target(completed["state"]["report"])))
        self.assertEqual(approve_status, 202)
        approved_status, approved = self.result(approval_job["job_id"])
        self.assertEqual(approved_status, 200)
        self.assertEqual(approved["state"]["report"]["status"], "completed")

    def test_job_error_returns_safe_original_bridge_error_without_provider_detail(self):
        self.agents.cleanup_error = RuntimeError("private-provider-detail")
        status, queued = self.http("POST", "/v1/dispatch", request("start_cleanup"))
        self.assertEqual(status, 202)
        status, result = self.result(queued["job_id"])
        self.assertEqual(status, 502)
        self.assertEqual(result["error"]["code"], "agent_unavailable")
        self.assertNotIn("private-provider-detail", json.dumps(result))
        self.assertEqual(self.http("GET", "/v1/jobs/" + "0" * 32)[0], 404)

    def test_job_queue_is_bounded_while_report_is_pending(self):
        self.agents.report_release.clear()
        with patch("browser_bridge.MAX_PENDING_JOBS", 1):
            status, queued = self.http("POST", "/v1/dispatch", request("start_report"))
            self.assertEqual(status, 202)
            self.assertTrue(self.agents.report_entered.wait(2))
            limited_status, limited = self.http("POST", "/v1/dispatch", request("status"))
            self.assertEqual(limited_status, 429)
            self.assertEqual(limited["error"]["code"], "job_limit")
            self.assertEqual(self.bridge.pending_jobs, 1)
            self.agents.report_release.set()
            self.assertEqual(self.result(queued["job_id"])[0], 200)


if __name__ == "__main__":
    unittest.main()
