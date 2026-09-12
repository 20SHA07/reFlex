"""Exercise actual executor effects and the localhost HTTP trust boundary."""
import http.client
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_bridge import BridgeError, BridgeServer, BrowserBridge, PATHS, provider_from_args
from openrouter_agents import OpenRouterError
from test_agents import CleanupAgent, ReportAgent


CONTEXT = {"workspace_id": "T123", "channel_id": "C123", "url": "https://app.slack.com/client/T123/C123"}


def target(item):
    return {"id": item["id"], "revision": item["revision"]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.bridge = BrowserBridge()
        self.addCleanup(shutil.rmtree, self.bridge.root)

    def dispatch(self, operation, **kwargs):
        return self.bridge.dispatch({"operation": operation, "request_id": uuid.uuid4().hex,
                                     "context": CONTEXT, **kwargs})["state"]

    def test_complete_flow_preserves_source_and_requires_new_approval(self):
        initial = self.dispatch("start_report", input={"text": "Prepare the client update."})
        session = self.bridge.sessions[("T123", "C123")]
        source_hash = session.referee.fingerprint("data/source_metrics.csv")
        report = initial["report"]
        self.assertFalse((session.workspace / report["output_path"]).exists())
        self.assertIn("Total: 45", report["draft"])
        review = self.dispatch("start_cleanup")
        protected, working, debug = review["cleanup"]["items"]
        self.assertEqual([item["verdict"] for item in (protected, working, debug)], ["BLOCK", "DEFER", "REVIEW"])
        for unavailable in (protected, working):
            with self.assertRaises(BridgeError) as denied:
                self.dispatch("approve_cleanup", target=target(unavailable))
            self.assertEqual(denied.exception.code, "approval_unavailable")
        after_debug = self.dispatch("approve_cleanup", target=target(debug))
        self.assertTrue(after_debug["cleanup"]["items"][2]["executed"])
        self.assertFalse((session.workspace / "scratch/debug.log").exists())
        self.assertTrue((session.referee.quarantine / debug["id"] / "original.bin").exists())
        approved = self.dispatch("approve_report", target=target(report))
        self.assertEqual(approved["report"]["status"], "completed")
        self.assertEqual((session.workspace / report["output_path"]).read_text(), report["draft"])
        self.assertEqual(session.referee.status()["active_tasks"], [])
        fresh = approved["cleanup"]["items"][1]
        self.assertEqual(fresh["verdict"], "REVIEW")
        self.assertNotEqual(fresh["id"], working["id"])
        self.assertTrue((session.workspace / fresh["path"]).exists())
        with self.assertRaises(BridgeError) as stale:
            self.dispatch("approve_cleanup", target=target(working))
        self.assertEqual(stale.exception.code, "stale_review")
        done = self.dispatch("approve_cleanup", target=target(fresh))
        self.assertTrue(done["cleanup"]["items"][1]["executed"])
        self.assertFalse((session.workspace / fresh["path"]).exists())
        self.assertEqual(source_hash, session.referee.fingerprint("data/source_metrics.csv"))
        self.assertNotIn(self.bridge.token, json.dumps(done))
        self.assertNotIn(self.bridge.token, session.referee.audit_path.read_text())

    def test_approval_is_idempotent_and_checks_exact_revision(self):
        report = self.dispatch("start_report")["report"]
        bad_target = {**target(report), "revision": "changed"}
        with self.assertRaises(BridgeError):
            self.dispatch("approve_report", target=bad_target)
        first = self.dispatch("approve_report", target=target(report))
        second = self.dispatch("approve_report", target=target(report))
        self.assertEqual(first, second)
        session = self.bridge.sessions[("T123", "C123")]
        self.assertEqual(len(list((session.workspace / "reports").glob("*.md"))), 1)

    def test_request_deduplication_rejects_payload_reuse(self):
        body = {"operation": "start_report", "request_id": "retry-123", "context": CONTEXT,
                "input": {"text": "Report A"}}
        first = self.bridge.dispatch(body)
        self.assertEqual(first, self.bridge.dispatch(body))
        changed = {**body, "input": {"text": "Report B"}}
        with self.assertRaises(BridgeError) as conflict:
            self.bridge.dispatch(changed)
        self.assertEqual(conflict.exception.code, "request_conflict")

    def test_channel_cannot_approve_another_channels_action(self):
        report = self.dispatch("start_report")["report"]
        other = {"workspace_id": "T123", "channel_id": "C456", "url": "https://app.slack.com/client/T123/C456"}
        with self.assertRaises(BridgeError) as missing:
            self.dispatch("approve_report", context=other, target=target(report))
        self.assertEqual(missing.exception.code, "stale_review")
        self.assertNotEqual(self.bridge.sessions[("T123", "C123")].workspace,
                            self.bridge.sessions[("T123", "C456")].workspace)
        self.assertEqual(self.dispatch("status")["report"]["status"], "awaiting_approval")

    def test_stale_file_is_not_quarantined(self):
        debug = self.dispatch("start_cleanup")["cleanup"]["items"][2]
        session = self.bridge.sessions[("T123", "C123")]
        path = session.workspace / debug["path"]
        path.write_text("Changed after review")
        with self.assertRaises(BridgeError) as blocked:
            self.dispatch("approve_cleanup", target=target(debug))
        self.assertEqual(blocked.exception.code, "stale_review")
        self.assertEqual(path.read_text(), "Changed after review")
        self.assertEqual(self.dispatch("status")["cleanup"]["items"][2]["verdict"], "BLOCK")
        fresh = self.dispatch("start_cleanup")["cleanup"]["items"][2]
        self.assertNotEqual(debug["id"], fresh["id"])
        self.dispatch("approve_cleanup", target=target(fresh))
        self.assertFalse(path.exists())

    def test_report_start_replaces_earlier_cleanup_review_with_defer(self):
        old = self.dispatch("start_cleanup")["cleanup"]["items"][1]
        self.assertEqual(old["verdict"], "REVIEW")
        state = self.dispatch("start_report")
        fresh = state["cleanup"]["items"][1]
        self.assertEqual(fresh["verdict"], "DEFER")
        self.assertNotEqual(old["id"], fresh["id"])
        with self.assertRaises(BridgeError) as stale:
            self.dispatch("approve_cleanup", target=target(old))
        self.assertEqual(stale.exception.code, "stale_review")
        completed = self.dispatch("approve_report", target=target(state["report"]))
        self.assertEqual(completed["cleanup"]["items"][1]["verdict"], "REVIEW")

    def test_failure_after_registration_pauses_fixture_instead_of_hiding_lease(self):
        self.dispatch("status")
        session = self.bridge.sessions[("T123", "C123")]
        with patch.object(session.referee, "read_text", side_effect=OSError("Injected read failure")):
            with self.assertRaises(BridgeError) as failed:
                self.dispatch("start_report")
        self.assertEqual(failed.exception.code, "restart_required")
        self.assertEqual(len(session.referee.status()["active_tasks"]), 1)
        self.assertIn("paused", self.dispatch("status")["message"])
        with self.assertRaises(BridgeError) as paused:
            self.dispatch("start_cleanup")
        self.assertEqual(paused.exception.code, "restart_required")

    def test_report_after_input_was_quarantined_creates_no_hidden_task(self):
        working = self.dispatch("start_cleanup")["cleanup"]["items"][1]
        self.dispatch("approve_cleanup", target=target(working))
        with self.assertRaises(BridgeError) as missing:
            self.dispatch("start_report")
        self.assertEqual(missing.exception.code, "missing_file")
        self.assertEqual(self.bridge.sessions[("T123", "C123")].referee.status()["active_tasks"], [])

    def test_request_text_limit_matches_extension(self):
        with self.assertRaises(BridgeError) as oversized:
            self.dispatch("start_report", input={"text": "x" * 2001})
        self.assertEqual(oversized.exception.code, "invalid_input")

    def test_changed_report_input_keeps_dependency_and_no_publication(self):
        report = self.dispatch("start_report")["report"]
        session = self.bridge.sessions[("T123", "C123")]
        (session.workspace / report["input_path"]).write_text("day,count\nMonday,999\n")
        with self.assertRaises(BridgeError) as blocked:
            self.dispatch("approve_report", target=target(report))
        self.assertEqual(blocked.exception.code, "stale_review")
        self.assertFalse((session.workspace / report["output_path"]).exists())
        self.assertEqual(len(session.referee.status()["active_tasks"]), 1)

    def test_page_identity_cannot_choose_executor_owner(self):
        self.dispatch("start_report", owner_slack_id="ATTACKER", principal={"id": "ATTACKER"})
        session = self.bridge.sessions[("T123", "C123")]
        self.assertEqual(session.referee.status()["active_tasks"][0]["owner_slack_id"], "local-owner")
        decision = session.referee.status()["actions"][0]
        rejected = session.referee.execute_approved_action(decision["action_id"], "ATTACKER")
        self.assertFalse(rejected["executed"])
        self.assertEqual(rejected["reason_code"], "unauthorized_approver")

    def test_old_browser_session_cannot_approve_after_restart(self):
        report = self.dispatch("start_report")["report"]
        fresh_bridge = BrowserBridge()
        self.addCleanup(shutil.rmtree, fresh_bridge.root)
        self.assertNotEqual(self.bridge.token, fresh_bridge.token)
        with self.assertRaises(BridgeError) as stale:
            fresh_bridge.dispatch({"operation": "approve_report", "request_id": "after-restart",
                                   "context": CONTEXT, "target": target(report)})
        self.assertEqual(stale.exception.code, "stale_review")

    def test_context_url_must_agree_with_ids(self):
        for invalid in ("https://evil.example/client/T123/C123", "https://app.slack.com/client/T123/C999"):
            with self.assertRaises(BridgeError) as rejected:
                self.dispatch("status", context={**CONTEXT, "url": invalid})
            self.assertEqual(rejected.exception.code, "invalid_context")

    def test_returned_state_cannot_mutate_stored_approval(self):
        report = self.dispatch("start_report")["report"]
        original = report["draft"]
        report["draft"] = "Injected draft"
        approved = self.dispatch("approve_report", target=target(report))
        self.assertEqual(approved["report"]["draft"], original)


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = BrowserBridge()
        cls.extension_id = "a" * 32
        cls.server = BridgeServer(cls.bridge, port=0, extension_id=cls.extension_id)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        shutil.rmtree(cls.bridge.root)

    def request(self, method="GET", path="/v1/session", *, headers=None, body=None, auth=True):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        request_headers = {"Authorization": "Bearer " + self.bridge.token} if auth else {}
        request_headers.update(headers or {})
        client.request(method, path, body=body, headers=request_headers)
        response = client.getresponse()
        raw = response.read()
        result = response.status, dict(response.getheaders()), json.loads(raw) if raw else None
        client.close()
        return result

    def test_valid_pairing_has_no_token_in_response(self):
        status, headers, result = self.request(headers={"Origin": "chrome-extension://" + self.extension_id})
        self.assertEqual(status, 200)
        self.assertEqual(result["principal"]["id"], "local-owner")
        self.assertEqual(result["agent"], {"provider": "sample", "model": None})
        self.assertNotIn(self.bridge.token, json.dumps(result))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "chrome-extension://" + self.extension_id)

    def test_missing_or_wrong_token_is_denied(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic ignored"}):
            status, _, result = self.request(headers=headers, auth=False)
            self.assertEqual(status, 401)
            self.assertEqual(result["error"]["code"], "unauthorized")

    def test_webpage_origin_and_dns_rebinding_host_are_denied(self):
        for headers in ({"Origin": "https://app.slack.com"}, {"Origin": "null"},
                        {"Origin": "chrome-extension://" + "b" * 32}, {"Host": "attacker.example:8765"}):
            status, _, _ = self.request(headers=headers)
            self.assertEqual(status, 403)

    def test_preflight_restricts_origin_method_and_headers(self):
        good = {"Origin": "chrome-extension://" + self.extension_id,
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"}
        self.assertEqual(self.request("OPTIONS", "/v1/dispatch", headers=good, auth=False)[0], 204)
        bad = {**good, "Origin": "https://app.slack.com"}
        self.assertEqual(self.request("OPTIONS", "/v1/dispatch", headers=bad, auth=False)[0], 403)
        bad = {**good, "Access-Control-Request-Headers": "authorization, x-unexpected"}
        self.assertEqual(self.request("OPTIONS", "/v1/dispatch", headers=bad, auth=False)[0], 403)

    def test_post_validates_json_content_type_and_size(self):
        cases = [({"Content-Type": "text/plain"}, "{}", 415),
                 ({"Content-Type": "application/json"}, "{bad", 400),
                 ({"Content-Type": "application/json"}, "[]", 400),
                 ({"Content-Type": "application/json"}, '{"operation": []}', 400),
                 ({"Content-Type": "application/json"}, "[" * 1500 + "]" * 1500, 400),
                 ({"Content-Type": "application/json"}, "x" * 17000, 413)]
        for headers, body, expected in cases:
            self.assertEqual(self.request("POST", "/v1/dispatch", headers=headers, body=body)[0], expected)

    def test_authorized_dispatch_uses_real_server_session(self):
        body = {"operation": "start_report", "context": CONTEXT, "request_id": uuid.uuid4().hex}
        status, _, result = self.request("POST", "/v1/dispatch", headers={"Content-Type": "application/json"}, body=json.dumps(body))
        self.assertEqual(status, 200)
        self.assertEqual(result["state"]["mode"], "local-demo")
        self.assertEqual(result["state"]["report"]["status"], "awaiting_approval")


class FakeAgents:
    """Model stand-in; receives proposal inputs, never execution authority."""
    model = "test/fake-model"
    descriptor = {"provider": "openrouter", "model": model}

    def __init__(self):
        self.calls = []
        self.report_hook = None
        self.cleanup_hook = None

    def generate_report(self, **kwargs):
        self.calls.append(("report", copy.deepcopy(kwargs)))
        if self.report_hook:
            return self.report_hook(kwargs)
        return "# Model-generated client update\n\nThe total is 45.\n"

    def propose_cleanup(self, **kwargs):
        self.calls.append(("cleanup", copy.deepcopy(kwargs)))
        if self.cleanup_hook:
            return self.cleanup_hook(kwargs)
        return [{"path": item["path"], "operation": "quarantine", "reason": "Model suggestion for referee review."}
                for item in kwargs["candidates"]]


class ModelWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeAgents()
        self.bridge = BrowserBridge(agent_provider=self.provider)
        self.addCleanup(shutil.rmtree, self.bridge.root)

    def dispatch(self, operation, **kwargs):
        return self.bridge.dispatch({"operation": operation, "request_id": uuid.uuid4().hex,
                                     "context": CONTEXT, **kwargs})["state"]

    def session(self):
        self.dispatch("status")
        return self.bridge.sessions[("T123", "C123")]

    def test_two_real_agent_classes_run_and_referee_controls_complete_flow(self):
        session = self.session()
        self.assertIsInstance(session.report_agent, ReportAgent)
        self.assertIsInstance(session.cleanup_agent, CleanupAgent)
        self.assertEqual(self.provider.calls, [])
        source_hash = session.referee.fingerprint(PATHS[0])
        state = self.dispatch("start_report", input={"text": "Explain the weekly counts."})
        report = state["report"]
        self.assertEqual(state["agent"], self.provider.descriptor)
        self.assertEqual(report["draft"], "# Model-generated client update\n\nThe total is 45.\n")
        self.assertFalse((session.workspace / report["output_path"]).exists())
        self.assertEqual(self.provider.calls[0][1]["request_text"], "Explain the weekly counts.")
        cleanup = self.dispatch("start_cleanup", input={"text": "Review cleanup candidates."})["cleanup"]
        protected, working, debug = cleanup["items"]
        self.assertEqual([item["verdict"] for item in cleanup["items"]], ["BLOCK", "DEFER", "REVIEW"])
        self.assertEqual(self.provider.calls[1][1]["request_text"], "Review cleanup candidates.")
        for candidate in self.provider.calls[1][1]["candidates"]:
            self.assertEqual(set(candidate), {"path", "size_bytes", "sha256"})
        self.assertNotEqual(protected["reason"], protected["proposal_reason"])
        self.assertEqual({action["agent_id"] for action in session.referee.status()["actions"]},
                         {"report-agent", "cleanup-agent"})
        self.dispatch("approve_cleanup", target=target(debug))
        approved = self.dispatch("approve_report", target=target(report))
        self.assertEqual((session.workspace / report["output_path"]).read_text(), report["draft"])
        fresh = approved["cleanup"]["items"][1]
        self.assertEqual(fresh["verdict"], "REVIEW")
        self.assertNotEqual(fresh["id"], working["id"])
        self.assertEqual(fresh["proposal_reason"], working["proposal_reason"])
        with self.assertRaises(BridgeError):
            self.dispatch("approve_cleanup", target=target(working))
        self.dispatch("approve_cleanup", target=target(fresh))
        self.assertEqual(len(self.provider.calls), 2, "Approvals must never regenerate proposals")
        self.assertEqual(source_hash, session.referee.fingerprint(PATHS[0]))

    def test_dependency_is_registered_before_generation_and_snapshot_stays_bound(self):
        session = self.session()
        def change_input(kwargs):
            self.assertEqual(len(session.referee.status()["active_tasks"]), 1)
            self.assertIn("Monday,12", kwargs["source_text"])
            (session.workspace / PATHS[1]).write_text("day,count\nMonday,999\n")
            return "# Draft based on the original snapshot\n"
        self.provider.report_hook = change_input
        report = self.dispatch("start_report")["report"]
        with self.assertRaises(BridgeError) as stale:
            self.dispatch("approve_report", target=target(report))
        self.assertEqual(stale.exception.code, "stale_review")
        self.assertFalse((session.workspace / report["output_path"]).exists())
        self.assertEqual(len(session.referee.status()["active_tasks"]), 1)

    def test_cleanup_hash_is_captured_before_generation(self):
        session = self.session()
        old_hash = session.referee.fingerprint(PATHS[2])
        def change_debug(kwargs):
            self.assertEqual(kwargs["candidates"][2]["sha256"], old_hash)
            (session.workspace / PATHS[2]).write_text("Changed during model generation")
            return [{"path": item["path"], "operation": "quarantine", "reason": "Model suggestion."}
                    for item in kwargs["candidates"]]
        self.provider.cleanup_hook = change_debug
        debug = self.dispatch("start_cleanup")["cleanup"]["items"][2]
        self.assertEqual(debug["verdict"], "BLOCK")
        with self.assertRaises(BridgeError):
            self.dispatch("approve_cleanup", target=target(debug))
        self.assertTrue((session.workspace / PATHS[2]).exists())

    def test_file_missing_before_model_cannot_appear_as_a_fresh_approvable_file(self):
        session = self.session()
        (session.workspace / PATHS[2]).unlink()
        def create_debug(kwargs):
            self.assertIsNone(kwargs["candidates"][2]["sha256"])
            (session.workspace / PATHS[2]).write_text("Created during model generation")
            return [{"path": item["path"], "operation": "quarantine", "reason": "Model suggestion."}
                    for item in kwargs["candidates"]]
        self.provider.cleanup_hook = create_debug
        self.assertEqual(self.dispatch("start_cleanup")["cleanup"]["items"][2]["verdict"], "BLOCK")

    def test_invalid_whole_plan_creates_no_actions_and_preserves_previous_review(self):
        before = self.dispatch("start_cleanup")
        session = self.session()
        actions = session.referee.status()["actions"]
        def invalid_plan(kwargs):
            plan = [{"path": item["path"], "operation": "quarantine", "reason": "Model suggestion."}
                    for item in kwargs["candidates"]]
            plan[-1]["path"] = "../../important-file"
            return plan
        self.provider.cleanup_hook = invalid_plan
        with self.assertRaises(BridgeError) as rejected:
            self.dispatch("start_cleanup")
        self.assertEqual(rejected.exception.code, "provider_invalid_plan")
        self.assertEqual(session.referee.status()["actions"], actions)
        self.assertEqual(self.dispatch("status"), before)

    def test_report_provider_failure_holds_input_and_pauses_instead_of_falling_back(self):
        for code in ("provider_timeout", "provider_refused", "provider_invalid_response"):
            with self.subTest(code=code):
                provider = FakeAgents()
                bridge = BrowserBridge(agent_provider=provider)
                self.addCleanup(shutil.rmtree, bridge.root)
                def fail(_kwargs):
                    raise OpenRouterError(code, "No agent proposal was accepted.")
                provider.report_hook = fail
                body = {"operation": "start_report", "context": CONTEXT, "request_id": uuid.uuid4().hex}
                with self.assertRaises(BridgeError) as failed:
                    bridge.dispatch(body)
                self.assertEqual(failed.exception.code, code)
                self.assertIn("restart", str(failed.exception))
                session = bridge.sessions[("T123", "C123")]
                self.assertIsNone(session.state["report"])
                self.assertEqual(len(session.referee.status()["active_tasks"]), 1)
                self.assertEqual(session.referee.status()["actions"], [])
                with self.assertRaises(BridgeError) as paused:
                    bridge.dispatch({**body, "operation": "start_cleanup", "request_id": uuid.uuid4().hex})
                self.assertEqual(paused.exception.code, "restart_required")

    def test_cleanup_timeout_does_not_replace_review_or_create_actions(self):
        before = self.dispatch("start_cleanup")
        session = self.session()
        actions = session.referee.status()["actions"]
        def fail(_kwargs):
            raise OpenRouterError("provider_timeout", "OpenRouter timed out. No agent proposal was accepted.")
        self.provider.cleanup_hook = fail
        with self.assertRaises(BridgeError) as failed:
            self.dispatch("start_cleanup")
        self.assertEqual(failed.exception.code, "provider_timeout")
        self.assertEqual(self.dispatch("status"), before)
        self.assertEqual(session.referee.status()["actions"], actions)

    def test_malformed_report_from_injected_provider_cannot_be_published(self):
        self.provider.report_hook = lambda _kwargs: {"draft": "Wrong shape", "approved": True}
        with self.assertRaises(BridgeError):
            self.dispatch("start_report")
        session = self.session()
        self.assertTrue(session.restart_required)
        self.assertIsNone(session.state["report"])
        self.assertEqual(session.referee.status()["actions"], [])


class ProviderConfigurationTests(unittest.TestCase):
    def test_default_stays_offline_even_when_key_is_present(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-private-key"}), patch("browser_bridge.OpenRouterAgents") as provider:
            self.assertIsNone(provider_from_args(argparse.Namespace(openrouter=False, model=None)))
            provider.assert_not_called()

    def test_explicit_model_and_environment_key_stay_in_python(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-private-key", "OPENROUTER_MODEL": "test/environment"}), patch("browser_bridge.OpenRouterAgents") as provider:
            provider_from_args(argparse.Namespace(openrouter=True, model="test/override"))
            provider.assert_called_once_with(api_key="test-private-key", model="test/override")

    def test_missing_key_noninteractive_fails_before_server_start_without_fallback(self):
        env = {key: value for key, value in os.environ.items() if key != "OPENROUTER_API_KEY"}
        result = subprocess.run([sys.executable, "browser_bridge.py", "--openrouter"],
                                input="", capture_output=True, text=True, timeout=5,
                                cwd=Path(__file__).resolve().parents[1], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENROUTER_API_KEY is required", result.stderr)
        self.assertNotIn("Pairing token file", result.stdout)
        self.assertNotIn("Sample reports", result.stdout)

    def test_interactive_prompt_is_hidden_and_does_not_echo_key(self):
        with patch.dict(os.environ, {}, clear=True), patch("browser_bridge.sys.stdin.isatty", return_value=True), patch("browser_bridge.getpass.getpass", return_value="test-hidden-key") as prompt, patch("browser_bridge.OpenRouterAgents") as provider:
            provider_from_args(argparse.Namespace(openrouter=True, model=None))
            prompt.assert_called_once()
            self.assertEqual(provider.call_args.kwargs["api_key"], "test-hidden-key")


if __name__ == "__main__":
    unittest.main()
