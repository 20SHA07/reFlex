"""Run both reFlex test agents against the referee without approving mutations."""
from __future__ import annotations

import argparse
import sys
import uuid

from browser_bridge import BrowserBridge, BridgeError, provider_from_args
from openrouter_agents import OpenRouterError


def run_pair(bridge: BrowserBridge, request_text: str) -> dict:
    """Prepare a report and cleanup review. Neither request grants approval."""
    context = {"workspace_id": "TDEMO", "channel_id": "CDEMO",
               "url": "https://app.slack.com/client/TDEMO/CDEMO"}
    for operation in ("start_report", "start_cleanup"):
        result = bridge.dispatch({"operation": operation, "context": context,
                                  "request_id": uuid.uuid4().hex,
                                  "input": {"text": request_text}})
    return result["state"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openrouter", action="store_true", help="Use real model calls; otherwise use offline sample agents.")
    parser.add_argument("--model", help="OpenRouter model ID; defaults to OPENROUTER_MODEL or google/gemini-2.5-flash.")
    parser.add_argument("--request", default="Prepare a concise client update and review quarantine of all demo cleanup candidates.",
                        help="Optional task context for the two agents, at most 2,000 characters.")
    parser.add_argument("--show-report", action="store_true", help="Print the complete proposed report draft.")
    args = parser.parse_args()
    if len(args.request) > 2000:
        parser.error("Keep the request at most 2,000 characters.")
    bridge = None
    try:
        provider = provider_from_args(args)
        bridge = BrowserBridge(agent_provider=provider)
        if provider:
            print(f"Running two OpenRouter test agents with {provider.model}.", flush=True)
            print("The request and demo report data will be sent to OpenRouter. No file operation is pre-approved.", flush=True)
        else:
            print("Running two offline sample agents. No model API call.", flush=True)
        state = run_pair(bridge, args.request)
        report = state["report"]
        print(f"\nReport Agent: {report['status']}")
        print(f"Holding input: {report['input_path']}")
        print(f"Proposed output: {report['output_path']}")
        if args.show_report:
            print("\n--- Proposed report, not published ---")
            print(report["draft"])
        print("\nCleanup Agent proposals and referee decisions:")
        for item in state["cleanup"]["items"]:
            print(f"  {item['verdict']:6} {item['path']}")
            if item.get("proposal_reason"):
                print(f"    Agent proposal: {item['proposal_reason']}")
            print(f"    Referee: {item['reason']}")
        if any(item["executed"] for item in state["cleanup"]["items"]) or report["status"] != "awaiting_approval":
            raise RuntimeError("The test agents unexpectedly changed an approval state.")
        print("\nNo proposal was approved. Protected data and working input remain in place.")
        print("Use browser_bridge.py and the Edge extension for the full human-approval flow.")
        print(f"Disposable test files and audit evidence: {bridge.root}")
        return 0
    except (OpenRouterError, BridgeError, ValueError) as error:
        # Provider and bridge errors are deliberately sanitized before this boundary.
        print(f"Test agents stopped: {error}", file=sys.stderr)
        return 1
    finally:
        if bridge is not None:
            # This command never hosts HTTP; its generated pairing token is not useful.
            bridge.token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
