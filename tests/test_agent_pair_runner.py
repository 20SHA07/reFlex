"""Exercise both test agents through the actual provider client and referee."""
import json
import shutil
import unittest

from browser_bridge import BrowserBridge
from openrouter_agents import OpenRouterAgents
from run_test_agents import run_pair


class PairRunnerTests(unittest.TestCase):
    def test_two_requests_create_reviews_without_approving_any_file_changes(self):
        calls = []

        def transport(payload, headers, timeout):
            request = json.loads(payload)
            self.assertNotIn("test-only-api-key", payload.decode())
            self.assertEqual(timeout, 18)
            schema_name = request["response_format"]["json_schema"]["name"]
            calls.append(schema_name)
            data = json.loads(request["messages"][1]["content"])["untrusted_input"]
            if schema_name == "reflex_report_draft":
                content = {"draft": "# Model draft\n\nTotal: 45. Awaiting human review."}
            else:
                content = {"proposals": [{"path": entry["path"], "operation": "quarantine",
                                           "reason": "Candidate proposed for referee review."}
                                          for entry in data["candidates"]]}
            return 200, json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(content)}}]}).encode()

        provider = OpenRouterAgents("test-only-api-key", transport=transport)
        bridge = BrowserBridge(agent_provider=provider)
        self.addCleanup(shutil.rmtree, bridge.root)
        state = run_pair(bridge, "A concise client update, please.")
        self.assertEqual(calls, ["reflex_report_draft", "reflex_cleanup_proposals"])
        self.assertEqual(state["agent"]["provider"], "openrouter")
        self.assertEqual(state["report"]["status"], "awaiting_approval")
        self.assertEqual([item["verdict"] for item in state["cleanup"]["items"]], ["BLOCK", "DEFER", "REVIEW"])
        self.assertFalse(any(item["executed"] for item in state["cleanup"]["items"]))
        session = next(iter(bridge.sessions.values()))
        self.assertTrue((session.workspace / "working/report_input.csv").exists())
        self.assertTrue((session.workspace / "data/source_metrics.csv").exists())
        self.assertTrue((session.workspace / "scratch/debug.log").exists())
        self.assertFalse((session.workspace / state["report"]["output_path"]).exists())


if __name__ == "__main__":
    unittest.main()
