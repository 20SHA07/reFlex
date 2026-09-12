"""CPU-only test scenario. Not a Slack or live-LLM demo.

Creates a NEW temporary fixture each run; never touches your actual project.
Run from the project root: python demo.py
"""
from pathlib import Path
import tempfile
from executor import RefereeService


def main():
    root = Path(tempfile.mkdtemp(prefix="agent_referee_demo_"))
    workspace = root / "demo_workspace"
    for folder in ("data", "working", "scratch", "reports"):
        (workspace / folder).mkdir(parents=True)
    (workspace / "data/source_metrics.csv").write_text("day,count\nMonday,12\n")
    (workspace / "working/report_input.csv").write_text("day,count\nMonday,12\n")
    (workspace / "scratch/debug.log").write_text("unused debug log\n")
    referee = RefereeService(workspace, root / "private_referee_state",
                             allowed_approver_ids={"U_REPORT", "U_CLEANUP"})
    original_hash = referee.fingerprint("data/source_metrics.csv")
    referee.register_task("report-1", "U_REPORT", ["working/report_input.csv"])
    proposals = []
    print("\nTEST FIXTURE:", root)
    print("Report task registered; input is now required.\n")
    for path in ("data/source_metrics.csv", "working/report_input.csv", "scratch/debug.log"):
        decision = referee.evaluate_action("quarantine", path,
                      agent_id="cleanup-agent", requested_by_slack_id="U_CLEANUP")
        proposals.append(decision)
        print(f"{decision['verdict']:6s} {path}: {decision['reason_code']}")
    clean = referee.execute_approved_action(proposals[2]["action_id"], "U_CLEANUP")
    print("\nApproved debug cleanup executed:", clean["executed"])
    draft = referee.evaluate_action("create", "reports/client_update.md",
                    agent_id="report-agent", requested_by_slack_id="U_REPORT",
                    content="# Client update\nMonday count: 12.\n")
    published = referee.execute_approved_action(draft["action_id"], "U_REPORT")
    assert published["executed"]
    referee.complete_task("report-1", "U_REPORT")
    print("Report published and owner completed task.")
    fresh = referee.reevaluate_action(proposals[1]["action_id"])
    print("Deferred cleanup reassessed:", fresh["verdict"], "(NEW approval required)")
    after = referee.execute_approved_action(fresh["action_id"], "U_CLEANUP")
    print("Approved working-file cleanup executed:", after["executed"])
    print("Protected source hash unchanged:", original_hash == referee.fingerprint("data/source_metrics.csv"))
    print("Report exists:", (workspace / "reports/client_update.md").is_file())
    print("Audit:", referee.audit_path)
    print("\nThis is an injected safety test. Slack and model integration belong to the other teammates.")


if __name__ == "__main__":
    main()
