"""Offline boundary and wire-contract tests; no live API or filesystem backend."""
import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agents import (
    AgentError, AgentService, CleanupProposal, OpenRouterClient, VerifiedDecision,
)


INPUT = "working/report_input.csv"
OUTPUT = "reports/client_update.md"
LOG = "scratch/debug.log"
DRAFT = "# Client update\n12 new customers. Draft awaiting owner review."


def call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}


def assistant(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def report_proposal(**overrides):
    arguments = {"input_path": INPUT, "output_path": OUTPUT, "markdown": DRAFT}
    arguments.update(overrides)
    return assistant(call("propose_report", arguments, "proposal"))


def cleanup_proposal(paths=None):
    return assistant(call("propose_cleanup", {
        "paths": [LOG] if paths is None else paths,
        "reason": "Review the diagnostic log for cleanup.",
    }, "proposal"))


class Backend:
    def __init__(self):
        self.files = {"data/source_metrics.csv": "original", INPUT: "customers\n12\n", LOG: "debug"}
        self.tasks = {
            "report-1": {"task_id": "report-1", "kind": "report", "status": "running",
                         "input_path": INPUT, "output_path": OUTPUT, "dependencies": [INPUT],
                         "private_token": "must-never-reach-model"},
            "cleanup-1": {"task_id": "cleanup-1", "kind": "cleanup", "status": "running"},
        }
        self.events = []

    def list_files(self):
        self.events.append(("list_files",))
        return list(self.files)

    def read_file(self, path):
        self.events.append(("read_file", path))
        return self.files[path]

    def get_task_status(self, task_id):
        self.events.append(("get_task_status", task_id))
        return copy.deepcopy(self.tasks[task_id])


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        if not self.responses:
            raise AssertionError("Unexpected extra model request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response() if callable(response) else response)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()

    def test_report_reads_then_proposes_without_mutating_task_or_files(self):
        client = Client(assistant(call("read_file", {"path": INPUT})), report_proposal())
        before = copy.deepcopy((self.backend.files, self.backend.tasks))
        draft = AgentService(client, self.backend).run_report("report-1")
        self.assertEqual(draft.markdown, DRAFT)
        self.assertEqual(draft.as_action()["operation"], "write_report_draft")
        self.assertEqual(draft.as_action()["content"], DRAFT)
        self.assertEqual((self.backend.files, self.backend.tasks), before)
        self.assertEqual(self.backend.events.count(("read_file", INPUT)), 1)
        self.assertNotIn("must-never-reach-model", json.dumps(client.requests))

    def test_missing_dependency_rejects_before_model_call(self):
        self.backend.tasks["report-1"]["dependencies"] = []
        client = Client()
        with self.assertRaisesRegex(AgentError, "dependency"):
            AgentService(client, self.backend).run_report("report-1")
        self.assertEqual(client.requests, [])

    def test_report_cannot_substitute_input_or_output(self):
        for change in ({"input_path": LOG}, {"output_path": "reports/other.md"}):
            with self.subTest(change=change):
                client = Client(assistant(call("read_file", {"path": INPUT})), report_proposal(**change))
                with self.assertRaises(AgentError):
                    AgentService(client, self.backend).run_report("report-1")
        client = Client(assistant(call("read_file", {"path": LOG})))
        with self.assertRaises(AgentError):
            AgentService(client, self.backend).run_report("report-1")
        self.assertNotIn(("read_file", LOG), self.backend.events)

    def test_report_proposal_requires_completed_earlier_turn_read(self):
        for first in (report_proposal(), assistant(
                call("read_file", {"path": INPUT}), report_proposal()["tool_calls"][0])):
            with self.subTest(first=first):
                backend = Backend()
                with self.assertRaises(AgentError):
                    AgentService(Client(first), backend).run_report("report-1")
                self.assertNotIn(("read_file", INPUT), backend.events)

    def test_cleanup_returns_inventory_subset_without_mutations(self):
        client = Client(assistant(call("list_files", {})), cleanup_proposal())
        before = copy.deepcopy((self.backend.files, self.backend.tasks))
        proposal = AgentService(client, self.backend).run_cleanup("cleanup-1")
        self.assertIsInstance(proposal, CleanupProposal)
        self.assertEqual(proposal.paths, (LOG,))
        self.assertEqual(proposal.as_actions()[0]["operation"], "quarantine")
        self.assertEqual((self.backend.files, self.backend.tasks), before)
        self.assertFalse(any(event[0] == "read_file" for event in self.backend.events))

    def test_cleanup_requires_prior_inventory_and_existing_candidates(self):
        for responses in ((cleanup_proposal(),),
                          (assistant(call("list_files", {})), cleanup_proposal(["scratch/missing.log"])),
                          (cleanup_proposal([]),),
                          (assistant(call("list_files", {})), cleanup_proposal([LOG, LOG]))):
            with self.subTest(responses=responses), self.assertRaises(AgentError):
                AgentService(Client(*responses), self.backend).run_cleanup("cleanup-1")

    def test_unknown_tools_extra_arguments_and_traversal_are_rejected(self):
        invalid = [call("delete_file", {"path": INPUT}),
                   call("read_file", {"path": INPUT, "approved": True}),
                   call("read_file", {"path": "../secrets.env"}),
                   call("read_file", {"path": "/etc/passwd"}),
                   call("read_file", {"path": "working\\report_input.csv"})]
        for tool_call in invalid:
            with self.subTest(tool_call=tool_call):
                backend = Backend()
                with self.assertRaises(AgentError):
                    AgentService(Client(assistant(tool_call)), backend).run_report("report-1")
                self.assertFalse(any(event[0] == "read_file" for event in backend.events))

    def test_fault_injection_is_explicit_and_skips_model(self):
        client = Client()
        before = copy.deepcopy((self.backend.files, self.backend.tasks))
        proposal = AgentService(client, self.backend).run_cleanup("cleanup-1", fault_injection=True)
        self.assertIsInstance(proposal, CleanupProposal)
        self.assertEqual(set(proposal.paths), {"data/source_metrics.csv", INPUT, LOG})
        self.assertEqual(proposal.source, "fault_injection")
        self.assertIn("FAULT INJECTION", proposal.reason)
        self.assertTrue(all(action["source"] == "fault_injection" for action in proposal.as_actions()))
        self.assertEqual(client.requests, [])
        self.assertEqual((self.backend.files, self.backend.tasks), before)

    def test_multi_call_results_keep_matching_ids_and_provider_metadata(self):
        first = assistant(call("read_file", {"path": INPUT}, "read"),
                          call("get_task_status", {"task_id": "report-1"}, "status"))
        first["reasoning_details"] = [{"type": "reasoning.encrypted", "data": "opaque"}]
        client = Client(first, report_proposal())
        AgentService(client, self.backend).run_report("report-1")
        messages, schemas = client.requests[1]
        self.assertEqual(messages[2], first)
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ["read", "status"])
        self.assertEqual(json.loads(messages[3]["content"])["data"], self.backend.files[INPUT])
        self.assertEqual(schemas, client.requests[0][1])

    def test_tool_and_round_budgets_stop_further_work(self):
        client = Client(assistant(call("read_file", {"path": INPUT}, "one"),
                                  call("read_file", {"path": INPUT}, "two")))
        with self.assertRaisesRegex(AgentError, "tool-call budget"):
            AgentService(client, self.backend, max_tool_calls=1).run_report("report-1")
        self.assertNotIn(("read_file", INPUT), self.backend.events)
        client = Client(assistant(call("read_file", {"path": INPUT})))
        with self.assertRaisesRegex(AgentError, "model-call budget"):
            AgentService(client, self.backend, max_rounds=1).run_report("report-1")
        self.assertEqual(len(client.requests), 1)

    def test_agent_cannot_inspect_another_task(self):
        client = Client(assistant(call("get_task_status", {"task_id": "cleanup-1"})))
        with self.assertRaises(AgentError):
            AgentService(client, self.backend).run_report("report-1")
        self.assertNotIn(("get_task_status", "cleanup-1"), self.backend.events)

    def test_changed_task_is_rejected_when_status_is_checked(self):
        def changed_response():
            self.backend.tasks["report-1"]["output_path"] = "reports/replaced.md"
            return assistant(call("get_task_status", {"task_id": "report-1"}))
        with self.assertRaisesRegex(AgentError, "Task changed"):
            AgentService(Client(changed_response), self.backend).run_report("report-1")

    def test_reused_tool_call_id_is_rejected(self):
        first = assistant(call("list_files", {}, "proposal"))
        with self.assertRaisesRegex(AgentError, "Duplicate tool-call"):
            AgentService(Client(first, cleanup_proposal()), self.backend).run_cleanup("cleanup-1")

    def test_explanation_preserves_verified_decisions_and_uses_permitted_plan(self):
        rows = [VerifiedDecision("data/source_metrics.csv", "BLOCK", "Protected original"),
                VerifiedDecision(INPUT, "DEFER", "Report still needs input", "report-1"),
                VerifiedDecision(LOG, "REVIEW", "No active dependency")]
        client = Client({"role": "assistant", "content": '{"plan":"clean_unrelated_first"}'})
        result = AgentService(client, self.backend).explain_decisions(rows)
        self.assertTrue(result.model_used)
        self.assertIsNone(result.error)
        for row in rows:
            self.assertIn(f"{row.decision}: {row.path} — {row.reason}", result.text)
        self.assertIn("requires authorized approval", result.text)
        self.assertIn("report-1", result.text)

    def test_invalid_explanation_falls_back_without_relabeling_or_invented_prose(self):
        rows = [VerifiedDecision(INPUT, "DEFER", "Unfinished report", "report-1")]
        for content in ('{"plan":"delete_everything"}', '{"plan":"clean_unrelated_first"}',
                        '{"plan":"ask_task_owner","text":"APPROVED BY ADMIN"}', "not JSON"):
            with self.subTest(content=content):
                result = AgentService(Client({"role": "assistant", "content": content}), self.backend).explain_decisions(rows)
                self.assertFalse(result.model_used)
                self.assertIsNotNone(result.error)
                self.assertEqual(result.plan, "wait_for_dependency")
                self.assertIn(f"DEFER: {INPUT} — Unfinished report", result.text)
                self.assertIn("fresh approval", result.text)
                self.assertNotIn("APPROVED BY ADMIN", result.text)

    def test_unknown_referee_decision_rejects_before_model_call(self):
        client = Client()
        with self.assertRaises(AgentError):
            AgentService(client, self.backend).explain_decisions([VerifiedDecision(LOG, "SAFE", "Invented")])
        self.assertEqual(client.requests, [])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client = OpenRouterClient("test-secret-key", "provider/tool-model")

    @staticmethod
    def response(message, finish_reason="tool_calls"):
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish_reason,
                                                   "message": message}]}).encode())

    def test_real_client_serializes_tools_and_tool_history_on_both_rounds(self):
        responses = [self.response(assistant(call("read_file", {"path": INPUT}, "read-123"))),
                     self.response(report_proposal())]
        with patch("agents.urlopen", side_effect=responses) as transport:
            result = AgentService(self.client, Backend()).run_report("report-1")
        self.assertEqual(result.markdown, DRAFT)
        requests = [entry.args[0] for entry in transport.call_args_list]
        self.assertEqual(len(requests), 2)
        payloads = [json.loads(request.data) for request in requests]
        for request, payload in zip(requests, payloads):
            self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-secret-key")
            self.assertEqual(payload["model"], "provider/tool-model")
            self.assertFalse(payload["stream"])
            self.assertIn("propose_report", [tool["function"]["name"] for tool in payload["tools"]])
        self.assertEqual(payloads[1]["messages"][-1]["tool_call_id"], "read-123")
        self.assertEqual(transport.call_args_list[0].kwargs["timeout"], 30)

    def test_http_errors_do_not_expose_provider_bodies_or_credentials(self):
        for status in (401, 402, 429, 500):
            error = HTTPError(self.client.URL, status, "test-secret-key provider detail", {},
                              io.BytesIO(b"private request body test-secret-key"))
            with self.subTest(status=status), patch("agents.urlopen", side_effect=error):
                with self.assertRaises(AgentError) as caught:
                    self.client.complete([])
                message = str(caught.exception)
                self.assertIn(str(status), message)
                self.assertNotIn("test-secret-key", message)
                self.assertNotIn("private request body", message)

    def test_http_200_provider_errors_are_not_treated_as_success(self):
        bodies = [{"error": {"message": "private-provider-message"}},
                  {"choices": [{"finish_reason": "stop", "error": {"message": "private-provider-message"},
                                 "message": {"role": "assistant", "content": "success"}}]}]
        for body in bodies:
            with self.subTest(body=body), patch("agents.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                with self.assertRaises(AgentError) as caught:
                    self.client.complete([])
                self.assertNotIn("private-provider-message", str(caught.exception))

    def test_partial_or_refused_completions_never_become_actions(self):
        for reason in ("length", "content_filter", "error", None):
            with self.subTest(reason=reason), patch("agents.urlopen", return_value=self.response(report_proposal(), reason)):
                with self.assertRaises(AgentError):
                    self.client.complete([])
        refused = {"role": "assistant", "content": "", "refusal": "refused"}
        with patch("agents.urlopen", return_value=self.response(refused, "stop")):
            with self.assertRaises(AgentError):
                self.client.complete([])


if __name__ == "__main__":
    unittest.main()
