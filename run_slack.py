"""Local Socket Mode runner. Choose preview or a real adapter explicitly."""

import argparse
import importlib
import logging
import os
from pathlib import Path


def load_backend(spec: str):
    module_name, separator, factory_name = spec.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("Use --backend module_name:factory_name")
    factory = getattr(importlib.import_module(module_name), factory_name)
    backend = factory()
    required = ("start_report", "start_cleanup", "get_status", "approve_report", "approve_cleanup")
    if not all(callable(getattr(backend, method, None)) for method in required):
        raise ValueError("The backend must implement all methods in slack_contract.WorkflowBackend")
    if type(getattr(backend, "preview", None)) is not bool:
        raise ValueError("The backend must explicitly declare preview=True or preview=False")
    return backend


def main():
    parser = argparse.ArgumentParser(description="Run Agent Referee's Slack interface.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Sample UI state only; no AI or file operations")
    mode.add_argument("--backend", metavar="MODULE:FACTORY", help="Factory for the team's real workflow adapter")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        parser.error("Install dependencies first: python -m pip install -r requirements-slack.txt")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    app_token = os.environ.get("SLACK_APP_TOKEN", "").strip()
    if not bot_token.startswith("xoxb-") or not app_token.startswith("xapp-"):
        parser.error("Set SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in your local .env file")

    from slack_ui import create_app
    try:
        backend = load_backend("preview_backend:create_backend" if args.preview else args.backend)
    except Exception as error:
        parser.error(f"Could not load the backend ({type(error).__name__}). Check its module, factory, and contract.")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    print("UI PREVIEW: sample state only; no AI calls or file operations." if backend.preview
          else "Using the configured workflow backend.", flush=True)
    app = create_app(backend, bot_token=bot_token,
                     expected_team_id=os.environ.get("SLACK_TEAM_ID", "").strip() or None)
    print("Connecting to Slack. Keep this process running; use /referee status in your demo channel.", flush=True)
    try:
        SocketModeHandler(app, app_token).start()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
