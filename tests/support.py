"""Constants and builders shared by conftest.py and the tests."""

from __future__ import annotations

from typing import Any, Dict

SID = "11111111-aaaa-4bbb-8ccc-000000000001"


def entry(sid: str, title: str, *, workspace: str = "work", tab: str = "api", cwd: str = "/work/proj",
          parked_at: str = "2026-01-02 03:04") -> Dict[str, Any]:
    """A saved entry as herdr-park writes it, for tests that need state without first parking something."""
    return {"parked_at": parked_at, "source": "park", "workspace_id": "w1", "workspace_label": workspace,
            "tab_label": tab,
            "agents": [{"agent": "claude", "session_id": sid, "title": title, "cwd": cwd,
                        "argv": ["claude", "--dangerously-skip-permissions"], "pane_id": "w1:p1"}]}
