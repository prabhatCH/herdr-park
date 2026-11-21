"""Fixtures: load the herdr-park script against a fake herdr, with every path and process kept inside a tmp dir.

The script is a single extension-less file whose paths are read from the environment at import time, so each
test sets the environment first and then loads a fresh copy of the script as a module. Nothing in the script
is changed. Its three ways out (`subprocess.run`, `os.kill` and `os.execvp`, plus `time.sleep`) are replaced on
the loaded module only, never globally.
"""

from __future__ import annotations

import io
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from fake_herdr import FakeHerdr
from support import SID

SCRIPT = Path(__file__).resolve().parent.parent / "herdr-park"

# environment the script reads; all of it is cleared or pointed into the tmp dir for every test
CLEARED_ENV = ("HERDR_PLUGIN_CONTEXT_JSON", "HERDR_PLUGIN_EVENT_JSON", "HERDR_PANE_ID", "HERDR_TAB_ID",
               "HERDR_ACTIVE_PANE_ID", "HERDR_PARK_TAB", "HERDR_PLUGIN_ID", "XDG_STATE_HOME")


class Proxy:
    """The real module, except for the named attributes. Used so a patched os/time/subprocess stays inside the script."""

    def __init__(self, real: Any, **overrides: Any) -> None:
        self._real, self._overrides = real, overrides

    def __getattr__(self, name: str) -> Any:
        return self._overrides[name] if name in self._overrides else getattr(self._real, name)


class Clock:
    """Virtual time: sleep() advances it instead of waiting, so the script's wait loops finish at once."""

    def __init__(self) -> None:
        import time
        self._offset = 0.0
        self._time = time

    def time(self) -> float:
        return self._time.time() + self._offset

    def sleep(self, seconds: float) -> None:
        self._offset += seconds


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mp = monkeypatch
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.state_dir = tmp_path / "state"
        self.claude_dir = tmp_path / "claude"
        self.home.mkdir()
        herdr_bin = str(tmp_path / "no-such-herdr")  # never exists: the real CLI cannot be reached by accident

        for name in CLEARED_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            "HOME": str(self.home),   # ~/.local/share/herdr-park (legacy state) and ~/.codex resolve here
            "HERDR_BIN_PATH": herdr_bin,
            "HERDR_PLUGIN_STATE_DIR": str(self.state_dir),
            "HERDR_SOCKET_PATH": str(tmp_path / "herdr" / "herdr.sock"),
            "CLAUDE_CONFIG_DIR": str(self.claude_dir),
            "CODEX_HOME": str(tmp_path / "codex"),
            "HERDR_ENV": "1",
        }.items():
            monkeypatch.setenv(name, value)

        park = types.ModuleType("herdr_park")
        park.__file__ = str(SCRIPT)   # a real import sets __file__; exec() does not
        # exec, not an import hook: nothing is cached next to the script, and __name__ is not "__main__"
        exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), park.__dict__)
        self.park = park
        self.fake = FakeHerdr(herdr_bin, park.SELF)
        self.clock = Clock()
        park.subprocess = SimpleNamespace(run=self.fake.run)
        park.os = Proxy(os, kill=self.fake.kill, execvp=self.fake.execvp)
        park.time = Proxy(__import__("time"), time=self.clock.time, sleep=self.clock.sleep)

        self.stdin("")   # never read a real terminal, even under `pytest -s`

    # ---------------------------------------------------------------- driving the script

    def stdin(self, text: str) -> None:
        self.mp.setattr(sys, "stdin", io.StringIO(text))

    def cli(self, *args: str) -> Any:
        """Run `herdr-park ARGS` through its own main(). Returns the exit status: 0, or what sys.exit() was given."""
        self.mp.setattr(sys, "argv", ["herdr-park", *args])
        try:
            self.park.main()
        except SystemExit as exc:
            return exc.code
        return 0

    # ---------------------------------------------------------------- a world to act on

    def transcript(self, sid: str, prompt: str = "please fix the login bug") -> Path:
        """Claude only writes a transcript after the first prompt; herdr-park refuses sessions without one."""
        path = self.claude_dir / "projects" / "-work-proj" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"type": "user", "message": {"role": "user", "content": prompt}, "cwd": "/work/proj"}
        path.write_text(json.dumps(line, separators=(",", ":")) + "\n")   # compact, as Claude writes it
        return path

    def session(self, workspace: str, tab_label: str, sid: str, *, argv: Optional[List[str]] = None,
                status: str = "idle", title: str = "", cwd: str = "/work/proj", transcript: bool = True,
                focused: bool = True) -> SimpleNamespace:
        """A tab with one Claude agent in it (and a transcript for it), inside an existing workspace."""
        tab = self.fake.add_tab(workspace, tab_label, focused=focused)
        pane = self.fake.add_agent(tab, session=sid, argv=argv, status=status, title=title or tab_label, cwd=cwd)
        if transcript:
            self.transcript(sid)
        return SimpleNamespace(tab=tab, pane=pane, sid=sid, workspace=workspace)

    # ---------------------------------------------------------------- state on disk

    def parked(self) -> List[Dict[str, Any]]:
        path = self.state_dir / "parked.json"
        return json.loads(path.read_text()) if path.exists() else []

    def save_entries(self, entries: List[Dict[str, Any]]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "parked.json").write_text(json.dumps(entries))

    def error_log(self) -> str:
        path = self.state_dir / "error.log"
        return path.read_text() if path.exists() else ""


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    h = Harness(tmp_path, monkeypatch)
    yield h
    assert not h.fake.unexpected, f"herdr-park made calls the fake does not know: {h.fake.unexpected}"


@pytest.fixture
def fake(harness: Harness) -> FakeHerdr:
    return harness.fake


@pytest.fixture
def park(harness: Harness) -> types.ModuleType:
    return harness.park


@pytest.fixture
def work(harness: Harness) -> SimpleNamespace:
    """The common case: workspace "work" with one tab "api" holding an idle Claude session that has a transcript."""
    ws = harness.fake.add_workspace("work")
    argv = ["claude", "--dangerously-skip-permissions", "--continue", "-r", "stale-id", "--model", "opus"]
    s = harness.session(ws, "api", SID, argv=argv, title="Fix the login bug", cwd="/work/api")
    s.argv = argv
    s.pid = harness.fake.agent_pid(s.pane)
    return s
