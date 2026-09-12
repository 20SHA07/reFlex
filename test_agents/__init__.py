"""The two proposal-only demo agents used by the browser bridge and CLI runner."""
from .report_agent import ReportAgent
from .cleanup_agent import CleanupAgent

__all__ = ["ReportAgent", "CleanupAgent"]
