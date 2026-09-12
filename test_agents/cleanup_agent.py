"""Cleanup proposal agent. It can ask for quarantine, never decide or execute."""
from __future__ import annotations

from openrouter_agents import OpenRouterAgents, validate_candidates, validate_cleanup_proposals


class CleanupAgent:
    agent_id = "cleanup-agent"

    def __init__(self, provider: OpenRouterAgents | None = None):
        self._provider = provider

    @property
    def descriptor(self) -> dict:
        return dict(self._provider.descriptor) if self._provider is not None else {"provider": "sample", "model": None}

    def run(self, *, candidates: list[dict], request_text: str) -> list[dict]:
        candidates = validate_candidates(candidates)
        if self._provider is not None:
            # Keep the allowed path snapshot independent of provider-owned data.
            result = self._provider.propose_cleanup(candidates=[dict(item) for item in candidates],
                                                    request_text=request_text)
        else:
            result = [{"path": candidate["path"], "operation": "quarantine",
                       "reason": "Sample cleanup suggestion for referee review; file eligibility is not assumed."}
                      for candidate in candidates]
        return validate_cleanup_proposals(result, candidates)
