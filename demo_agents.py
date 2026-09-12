"""Standalone agent demonstration: no Slack sends, file writes, or approvals."""

import argparse
import csv
import io
import json
import sys
from dataclasses import replace

from agents import AgentError, OpenRouterClient, ReFlexAgents, VerifiedDecision


INPUT_PATH = "logs/agent_activity.log"
INPUT_TEXT = "day,count\nMonday,12\nTuesday,18\nWednesday,15\n"
INVENTORY = (INPUT_PATH,)


class ScriptedClient:
    """Fixed model-shaped replies exercise parsing and tool routing offline."""

    @staticmethod
    def _call(name, arguments, number):
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"offline-{name}-{number}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}

    def complete(self, messages, tools=None):
        if tools is None:
            plans = json.loads(messages[1]["content"])["allowed_plans"]
            plan = ("wait_for_dependency" if "wait_for_dependency" in plans else
                    "clean_unrelated_first" if "clean_unrelated_first" in plans else "ask_task_owner")
            return {"role": "assistant", "content": json.dumps({"plan": plan})}
        names = {tool["function"]["name"] for tool in tools}
        results = [message for message in messages if message["role"] == "tool"]
        task = json.loads(messages[1]["content"])["task"]
        if "propose_report" in names:
            if not results:
                return self._call("read_file", {"path": task["input_path"]}, 1)
            source = next(json.loads(result["content"])["data"] for result in results
                          if isinstance(json.loads(result["content"])["data"], str))
            rows = list(csv.DictReader(io.StringIO(source)))
            observations = "\n".join("- " + "; ".join(f"{key}: {value}" for key, value in row.items())
                                     for row in rows)
            return self._call("propose_report", {
                "input_path": task["input_path"], "output_path": task["output_path"],
                "markdown": (
                    "# Shared activity log report\n\n"
                    "Draft awaiting the report owner's review and approval.\n\n"
                    + observations + "\n\n"
                    "OFFLINE SCRIPT: this sample is assembled by demo code, not AI-generated."
                ),
            }, 2)
        if not results:
            return self._call("list_files", {}, 1)
        inventory = json.loads(results[-1]["content"])["data"]
        return self._call("propose_cleanup", {
            "paths": [path for path in inventory if path.endswith(".log")][:12],
            "reason": "The log is a candidate for review; its name does not establish that it is safe to remove.",
        }, 2)


def sample_decisions():
    """Separate explanation fixture; no referee ran to produce these verdicts."""
    return (
        VerifiedDecision(INPUT_PATH, "DEFER", "SAMPLE report still requires the shared log until publication is approved."),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Try the reFlex agents with in-memory sample data; no files are changed.")
    parser.add_argument("--live", action="store_true",
                        help="Call OpenRouter using OPENROUTER_API_KEY and OPENROUTER_MODEL")
    parser.add_argument("--fault-injection", action="store_true",
                        help="Submit the demo inventory as fixed cleanup candidates for referee demonstrations")
    args = parser.parse_args(argv)
    print("LIVE MODEL DEMO: OpenRouter calls with synthetic input." if args.live else
          "OFFLINE DEMO: fixed scripted replies, no AI requests or credentials.", flush=True)
    print("No Slack messages, report publication, file moves, or persistent task state.\n", flush=True)
    try:
        client = OpenRouterClient.from_env() if args.live else ScriptedClient()
        agents = ReFlexAgents(client)
        # This is an in-memory fixture. Production must register the dependency first.
        draft = agents.run_report("sample-report-1", input_path=INPUT_PATH, input_text=INPUT_TEXT)
        print("REPORT DRAFT — content only; no output file was written\n")
        print(draft.markdown)
        proposal = agents.run_cleanup("sample-cleanup-1", INVENTORY,
                                      fault_injection=args.fault_injection)
        if not args.live and not args.fault_injection:
            proposal = replace(proposal, source="offline_script")
        print("\nCLEANUP PROPOSAL — awaits a real referee and approval\n")
        if args.fault_injection:
            print("FAULT INJECTION: these candidates were inserted by demo code, not chosen by AI.")
        elif not args.live:
            print("OFFLINE SCRIPT: the candidate below comes from a fixed test reply.")
        print(json.dumps(proposal.as_actions(), indent=2))
        print("\nSEPARATE EXPLANATION EXAMPLE — supplied SAMPLE verdicts\n")
        print("These rows are a fixture, not an evaluation of the proposal above.")
        explanation = agents.explain_cleanup(sample_decisions())
        print(explanation.text)
        if explanation.error is not None:
            print("Explanation model call failed; only supplied facts and a rule-based plan were shown.",
                  file=sys.stderr)
            return 1
        if not args.live:
            print("\nThe plan choice was scripted too. This checks wiring, not model quality.")
        return 0
    except AgentError as error:
        print(f"Agent demo stopped: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("Agent demo stopped unexpectedly. Check the local setup; no operation was executed.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
