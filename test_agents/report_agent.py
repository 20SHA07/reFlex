"""Report proposal agent. The host reserves input and reviews publication."""
from __future__ import annotations

import csv
import io

from openrouter_agents import OpenRouterAgents, validate_report_draft


class ReportAgent:
    agent_id = "report-agent"

    def __init__(self, provider: OpenRouterAgents | None = None):
        self._provider = provider

    @property
    def descriptor(self) -> dict:
        return dict(self._provider.descriptor) if self._provider is not None else {"provider": "sample", "model": None}

    def run(self, *, input_path: str, source_text: str, request_text: str) -> str:
        if self._provider is not None:
            return validate_report_draft(self._provider.generate_report(
                input_path=input_path, source_text=source_text, request_text=request_text))
        records = list(csv.DictReader(io.StringIO(source_text)))
        if not records or any(set(row) != {"day", "count"} or not row["day"] for row in records):
            raise ValueError("The sample input must contain day,count CSV records.")
        try:
            total = sum(int(row["count"]) for row in records)
        except (TypeError, ValueError):
            raise ValueError("The sample input must contain integer counts.") from None
        draft = "# Sample client update\n\nDeterministic local demo, generated without an AI model.\n\n"
        draft += f"Input: `{input_path}`\n\n"
        draft += "\n".join(f"- {row['day']}: {row['count']}" for row in records)
        draft += f"\n\nTotal: {total}.\n"
        if request_text:
            draft += "\nSelected request context (quoted, not executed):\n\n"
            draft += "\n".join("> " + line for line in request_text.splitlines()) + "\n"
        return validate_report_draft(draft)
