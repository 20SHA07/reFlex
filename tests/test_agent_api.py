"""Offline integration checks for the UI-independent agent entry points."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from agents import AgentError, ReFlexAgents, VerifiedDecision


INPUT = "working/report_input.csv"
OUTPUT = "reports/client_update.md"
INPUT_TEXT = "month,revenue\nJanuary,12000\nFebruary,13500\n"
LOG = "scratch/debug.log"


def tool_call(name, arguments, call_id):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments),
        },
    }]}


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append(copy.deepcopy((messages, tools)))
        if not self.responses:
            raise AssertionError("Unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def report_responses(output_path=OUTPUT):
    return [
        tool_call("read_file", {"path": INPUT}, "read-input"),
        tool_call("propose_report", {
            "input_path": INPUT, "output_path": output_path,
            "markdown": "# Draft report\nRevenue rose from 12000 to 13500.",
        }, "report-proposal"),
    ]


class PublicAgentApiTests(unittest.TestCase):
    def test_module_imports_without_slack_contract_or_sdk(self):
        root = str(Path(__file__).resolve().parents[1])
        script = """
import builtins
import sys
sys.path.insert(0, sys.argv[1])
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'slack_contract', 'slack_sdk', 'slack_bolt'}:
        raise AssertionError('Agent module must not require Slack: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from agents import ReFlexAgents, VerifiedDecision
assert callable(ReFlexAgents)
assert callable(VerifiedDecision)
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, root],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_report_uses_task_id_and_preserves_request_through_status_read(self):
        request_text = "Compare revenue in January and February. Keep the report concise."
        client = ScriptedClient(
            tool_call("get_task_status", {"task_id": "report-1"}, "read-status"),
            *report_responses(),
        )
        result = ReFlexAgents(client).run_report(
            "report-1", input_path=INPUT, input_text=INPUT_TEXT,
            request_text=request_text,
        )
        self.assertEqual(result.task_id, "report-1")
        self.assertEqual(result.input_path, INPUT)
        self.assertEqual(result.output_path, OUTPUT)
        self.assertEqual(result.as_action()["task_id"], "report-1")

        first_messages = client.requests[0][0]
        task = json.loads(first_messages[1]["content"])["task"]
        self.assertEqual(task["request_text"], request_text)
        self.assertIn(INPUT, task["dependencies"])
        self.assertNotIn(INPUT_TEXT, first_messages[0]["content"])

        status_message = next(
            message for message in client.requests[1][0]
            if message.get("tool_call_id") == "read-status"
        )
        self.assertEqual(json.loads(status_message["content"])["data"], task)
        input_message = next(
            message for message in client.requests[2][0]
            if message.get("tool_call_id") == "read-input"
        )
        self.assertEqual(json.loads(input_message["content"])["data"], INPUT_TEXT)

    def test_report_custom_output_and_maximum_request_length(self):
        output_path = "reports/monthly.md"
        client = ScriptedClient(*report_responses(output_path))
        draft = ReFlexAgents(client).run_report(
            "report-2", input_path=INPUT, input_text=INPUT_TEXT,
            output_path=output_path, request_text="r" * 2000,
        )
        self.assertEqual(draft.output_path, output_path)

    def test_report_invalid_inputs_stop_before_model_call(self):
        valid = {"task_id": "report-1", "input_path": INPUT, "input_text": INPUT_TEXT}
        cases = [
            {"task_id": ""}, {"task_id": None}, {"task_id": "t" * 201},
            {"input_path": "../secrets.env"}, {"input_path": "C:\\private.csv"},
            {"input_text": ""}, {"input_text": None}, {"input_text": "x" * 20001},
            {"output_path": "data/original.csv"}, {"output_path": "reports/../escape.md"},
            {"request_text": None}, {"request_text": "x" * 2001},
        ]
        for change in cases:
            with self.subTest(field=next(iter(change)), value_type=type(next(iter(change.values()))).__name__):
                client = ScriptedClient()
                with self.assertRaises(AgentError):
                    ReFlexAgents(client).run_report(**(valid | change))
                self.assertEqual(client.requests, [])

    def test_cleanup_empty_inventory_returns_without_model(self):
        client = ScriptedClient()
        result = ReFlexAgents(client).run_cleanup("cleanup-1", [])
        self.assertEqual(result.task_id, "cleanup-1")
        self.assertEqual(result.paths, ())
        self.assertEqual(result.as_actions(), [])
        self.assertTrue(result.reason)
        self.assertEqual(client.requests, [])

    def test_cleanup_can_propose_nothing_from_nonempty_inventory(self):
        reason = "The listed file is still useful; keep it."
        client = ScriptedClient(
            tool_call("list_files", {}, "read-inventory"),
            tool_call("propose_cleanup", {"paths": [], "reason": reason}, "cleanup-proposal"),
        )
        inventory = [LOG]
        result = ReFlexAgents(client).run_cleanup("cleanup-1", inventory)
        self.assertEqual(result.paths, ())
        self.assertEqual(result.reason, reason)
        self.assertEqual(result.as_actions(), [])
        self.assertEqual(inventory, [LOG])
        self.assertEqual(len(client.requests), 2)

    def test_empty_cleanup_still_requires_inventory_read_and_reason(self):
        client = ScriptedClient(tool_call(
            "propose_cleanup", {"paths": [], "reason": "Keep the files."}, "proposal",
        ))
        with self.assertRaises(AgentError):
            ReFlexAgents(client).run_cleanup("cleanup-1", [LOG])
        for arguments in ({"paths": []}, {"paths": [], "reason": ""}):
            with self.subTest(arguments=arguments):
                client = ScriptedClient(
                    tool_call("list_files", {}, "read-inventory"),
                    tool_call("propose_cleanup", arguments, "proposal"),
                )
                with self.assertRaises(AgentError):
                    ReFlexAgents(client).run_cleanup("cleanup-1", [LOG])

    def test_cleanup_invalid_inventory_stops_before_model(self):
        for inventory in (LOG, [LOG, LOG], ["../outside.log"], [LOG] * 201):
            with self.subTest(inventory_type=type(inventory).__name__):
                client = ScriptedClient()
                with self.assertRaises(AgentError):
                    ReFlexAgents(client).run_cleanup("cleanup-1", inventory)
                self.assertEqual(client.requests, [])

    def test_empty_explanation_returns_without_model(self):
        client = ScriptedClient()
        result = ReFlexAgents(client).explain_cleanup(())
        self.assertFalse(result.model_used)
        self.assertEqual(result.plan, "cancel_cleanup")
        self.assertIsNone(result.error)
        self.assertEqual(client.requests, [])

    def test_explanation_accepts_verified_rows_and_preserves_facts_on_failure(self):
        rows = (
            VerifiedDecision("data/source_metrics.csv", "BLOCK", "Protected original"),
            VerifiedDecision(INPUT, "DEFER", "Needed by active report", "report-1"),
        )
        for response in (AgentError("Model unavailable"),
                         {"role": "assistant", "content": '{"plan":"approve_everything"}'}):
            with self.subTest(response_type=type(response).__name__):
                result = ReFlexAgents(ScriptedClient(response)).explain_cleanup(rows)
                self.assertFalse(result.model_used)
                self.assertIsNotNone(result.error)
                self.assertEqual(result.plan, "wait_for_dependency")
                self.assertIn("BLOCK: data/source_metrics.csv — Protected original", result.text)
                self.assertIn(f"DEFER: {INPUT} — Needed by active report", result.text)
                self.assertIn("report-1", result.text)
                self.assertNotIn("approve_everything", result.text)

    def test_explanation_rejects_unverified_rows_before_model(self):
        for rows in ([{"path": LOG, "decision": "ALLOW", "reason": "model says so"}],
                     [VerifiedDecision(LOG, "UNKNOWN", "Unrecognized decision")],
                     [VerifiedDecision(LOG, "ALLOW", "reason")] * 13):
            with self.subTest(row_type=type(rows[0]).__name__, count=len(rows)):
                client = ScriptedClient()
                with self.assertRaises(AgentError):
                    ReFlexAgents(client).explain_cleanup(rows)
                self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
