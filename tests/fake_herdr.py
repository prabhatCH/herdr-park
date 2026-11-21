"""A small in-memory stand-in for herdr, for testing herdr-park without herdr, Claude, or any real process.

herdr-park reaches the outside world through `subprocess.run` (the `herdr` CLI, `ps`, `stty`), `os.kill` and
`os.execvp`. `FakeHerdr` provides all of them. It keeps a little world of workspaces, tabs and panes, answers the
herdr subcommands herdr-park actually calls with the same JSON envelope (`{"result": ...}`) and the same failure
mode (non-zero exit plus stderr) as the real CLI, and records everything it was asked to do.

Anything herdr-park asks for that the fake does not know is an error, not a silent no-op, so a new herdr call in
the plugin fails loudly until the fake learns it. Nothing here touches a real process: `kill` only accepts pids
the fake handed out.
"""

from __future__ import annotations

import json
import shlex
import signal
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

FIRST_PID = 4000  # fake pids start well above anything a test could confuse with a real, low one
AGENT_KINDS = {"claude", "codex"}


class FakeError(Exception):
    """A herdr command that fails: becomes a non-zero exit with this text on stderr."""


class FakeHerdr:
    def __init__(self, bin_path: str, script: str) -> None:
        self.bin = bin_path        # what herdr-park was told is herdr (HERDR_BIN_PATH)
        self.script = script       # path of the herdr-park script; its `wake` stub shows up under this name
        self.workspaces: Dict[str, Dict[str, Any]] = {}
        self.tabs: Dict[str, Dict[str, Any]] = {}
        self.panes: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Tuple[str, ...]] = []           # every herdr call, in order
        self.runs: List[Tuple[str, str]] = []            # (pane, command line) for each `pane run`
        self.notifications: List[Tuple[str, str]] = []   # (title, body)
        self.signals: List[Tuple[int, int]] = []         # (pid, signal) for each os.kill
        self.execs: List[Tuple[str, List[str]]] = []     # (file, argv) for each os.execvp
        self.stty_calls: List[List[str]] = []
        self.unexpected: List[Any] = []                  # calls the fake does not know; the fixture asserts it is empty
        self.failures: Dict[Tuple[str, ...], str] = {}   # command prefix -> stderr: make that command fail
        self.before: List[Callable[[Tuple[str, ...]], None]] = []  # called with the args before each herdr command
        self.rss_kb: Dict[int, int] = {}
        self._pids: Dict[int, bool] = {}
        self._n = {"w": 0, "t": 0, "p": 0}
        self._pid = FIRST_PID

    # ------------------------------------------------------------------ building a world

    def _next(self, kind: str) -> int:
        self._n[kind] += 1
        return self._n[kind]

    def _new_pid(self) -> int:
        self._pid += 1
        self._pids[self._pid] = True
        return self._pid

    def _shell(self) -> List[Dict[str, Any]]:
        return [{"argv0": "-zsh", "name": "zsh", "argv": ["-zsh"], "pid": self._new_pid()}]

    def add_workspace(self, label: str, focused: bool = True) -> str:
        wid = f"w{self._next('w')}"
        self.workspaces[wid] = {"workspace_id": wid, "label": label, "focused": focused, "active_tab_id": None}
        return wid

    def add_tab(self, workspace: str, label: str, focused: bool = True) -> str:
        tid = f"{workspace}:t{self._next('t')}"
        self.tabs[tid] = {"tab_id": tid, "workspace_id": workspace, "label": label, "focused": focused}
        self.workspaces[workspace]["active_tab_id"] = tid
        return tid

    def add_pane(self, tab: str, cwd: str = "/work/proj") -> str:
        """A pane running only a shell."""
        ws = self.tabs[tab]["workspace_id"]
        pid = f"{ws}:p{self._next('p')}"
        self.panes[pid] = {"pane_id": pid, "tab_id": tab, "workspace_id": ws, "cwd": cwd, "agent": None,
                           "agent_session": None, "agent_status": "unknown", "title": "", "claimed": False,
                           "fg": self._shell()}
        return pid

    def add_agent(self, tab: str, kind: str = "claude", session: Optional[str] = None,
                  argv: Optional[List[str]] = None, status: str = "idle", title: str = "",
                  cwd: str = "/work/proj", rss_kb: int = 204800) -> str:
        """A pane with an agent running in the foreground, as herdr reports it."""
        pane = self.add_pane(tab, cwd)
        proc_pid = self._new_pid()
        self.rss_kb[proc_pid] = rss_kb
        self.panes[pane].update(
            agent=kind, agent_status=status, title=title,
            agent_session={"agent": kind, "value": session} if session else None,
            fg=[{"argv0": kind, "name": kind, "argv": argv or [kind, "--dangerously-skip-permissions"],
                 "pid": proc_pid}])
        return pane

    def agent_pid(self, pane: str) -> int:
        return self.panes[pane]["fg"][0]["pid"]

    def close_tab_silently(self, tab: str) -> None:
        """Close a tab the way a keybinding does: herdr sends the plugin no event and no `tab close` command runs."""
        self._remove_tab(tab)

    def close_workspace_silently(self, workspace: str) -> None:
        for tab in [t for t, v in self.tabs.items() if v["workspace_id"] == workspace]:
            self._remove_tab(tab)
        del self.workspaces[workspace]

    def called(self, *prefix: str) -> List[Tuple[str, ...]]:
        return [c for c in self.calls if c[:len(prefix)] == prefix]

    def stub_pane_command(self, pane: str) -> Optional[List[str]]:
        """The wake stub's argv if it is what the pane is running, else None."""
        for p in self.panes[pane]["fg"]:
            if p["argv"][:1] == ["python3"] and self.script in p["argv"]:
                return p["argv"]
        return None

    # ------------------------------------------------------------------ what herdr-park calls

    def run(self, argv: List[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        """Stand-in for subprocess.run (always capture_output, text)."""
        argv = [str(a) for a in argv]
        if argv[0] == self.bin:
            return self._herdr(tuple(argv[1:]))
        if argv[:2] == ["ps", "-o"] and len(argv) == 5 and argv[3] == "-p":
            return self._ps(argv[2], argv[4])
        if argv[:1] == ["stty"]:
            self.stty_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.unexpected.append(argv)
        raise AssertionError(f"herdr-park ran a command the fake does not know: {argv}")

    def kill(self, pid: int, sig: int) -> None:
        """Stand-in for os.kill. Only pids the fake created are accepted; SIGTERM stops an agent."""
        self.signals.append((pid, sig))
        if pid not in self._pids:
            raise AssertionError(f"herdr-park signalled pid {pid}, which is not one the fake created")
        if sig != signal.SIGTERM:
            return
        for pane in self.panes.values():
            if any(p["pid"] == pid for p in pane["fg"]):
                if pane["agent"] and not pane["claimed"]:
                    pane.update(agent=None, agent_session=None, agent_status="unknown", fg=self._shell())

    def execvp(self, file: str, argv: List[str]) -> None:
        """Stand-in for os.execvp: record it and return (the real one never returns)."""
        self.execs.append((file, list(argv)))

    # ------------------------------------------------------------------ ps

    def _ps(self, field: str, pids: str) -> "subprocess.CompletedProcess[str]":
        ids = [int(p) for p in pids.split(",") if p.isdigit()]
        if field == "rss=":
            out = "".join(f"{self.rss_kb.get(p, 1024)}\n" for p in ids if p in self._pids)
        elif field == "lstart=":
            out = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 3600)) + "\n"
        else:
            raise AssertionError(f"herdr-park ran ps with fields the fake does not know: {field}")
        return subprocess.CompletedProcess(["ps"], 0, out, "")

    # ------------------------------------------------------------------ herdr CLI

    def _herdr(self, args: Tuple[str, ...]) -> "subprocess.CompletedProcess[str]":
        self.calls.append(args)
        for hook in list(self.before):
            hook(args)
        try:
            for prefix, why in self.failures.items():
                if args[:len(prefix)] == prefix:
                    raise FakeError(why)
            handler = getattr(self, "_" + "_".join(args[:2]).replace("-", "_"), None)
            if handler is None:
                self.unexpected.append(args)
                raise AssertionError(f"herdr-park called a herdr command the fake does not know: {args}")
            result = handler(args)
        except FakeError as exc:
            return subprocess.CompletedProcess(["herdr", *args], 1, "", str(exc) + "\n")
        return subprocess.CompletedProcess(["herdr", *args], 0, json.dumps({"id": "1", "result": result}), "")

    @staticmethod
    def _opt(args: Tuple[str, ...], name: str) -> Optional[str]:
        return args[args.index(name) + 1] if name in args else None

    def _pane(self, pane: str) -> Dict[str, Any]:
        if pane not in self.panes:
            raise FakeError(f"pane {pane} not found")
        return self.panes[pane]

    def _tab(self, tab: str) -> Dict[str, Any]:
        if tab not in self.tabs:
            raise FakeError(f"tab {tab} not found")
        return self.tabs[tab]

    def _remove_tab(self, tab: str) -> None:
        self.tabs.pop(tab, None)
        for pid in [p for p, v in self.panes.items() if v["tab_id"] == tab]:
            del self.panes[pid]

    @staticmethod
    def _pane_view(p: Dict[str, Any]) -> Dict[str, Any]:
        return {k: p[k] for k in ("pane_id", "tab_id", "workspace_id", "cwd", "agent", "agent_session")}

    # workspaces

    def _workspace_list(self, args: Tuple[str, ...]) -> Any:
        return {"workspaces": list(self.workspaces.values())}

    def _workspace_get(self, args: Tuple[str, ...]) -> Any:
        if args[2] not in self.workspaces:
            raise FakeError(f"workspace {args[2]} not found")
        return {"workspace": self.workspaces[args[2]]}

    def _workspace_create(self, args: Tuple[str, ...]) -> Any:
        wid = self.add_workspace(self._opt(args, "--label") or "", focused="--no-focus" not in args)
        tid = self.add_tab(wid, "1", focused=True)
        pane = self.add_pane(tid, self._opt(args, "--cwd") or "/")
        return {"workspace": self.workspaces[wid], "tab": self.tabs[tid], "root_pane": {"pane_id": pane}}

    # tabs

    def _tab_list(self, args: Tuple[str, ...]) -> Any:
        ws = self._opt(args, "--workspace")
        return {"tabs": [t for t in self.tabs.values() if ws in (None, t["workspace_id"])]}

    def _tab_get(self, args: Tuple[str, ...]) -> Any:
        return {"tab": self._tab(args[2])}

    def _tab_create(self, args: Tuple[str, ...]) -> Any:
        ws = self._opt(args, "--workspace")
        if ws not in self.workspaces:
            raise FakeError(f"workspace {ws} not found")
        tid = self.add_tab(ws, self._opt(args, "--label") or "1", focused="--no-focus" not in args)
        pane = self.add_pane(tid, self._opt(args, "--cwd") or "/")
        return {"tab": self.tabs[tid], "root_pane": {"pane_id": pane}}

    def _tab_rename(self, args: Tuple[str, ...]) -> Any:
        self._tab(args[2])["label"] = args[3]
        return {}

    def _tab_close(self, args: Tuple[str, ...]) -> Any:
        self._tab(args[2])
        self._remove_tab(args[2])
        return {}

    def _tab_focus(self, args: Tuple[str, ...]) -> Any:
        tab = self._tab(args[2])
        for t in self.tabs.values():
            t["focused"] = t is tab if t["workspace_id"] == tab["workspace_id"] else t["focused"]
        return {}

    # panes

    def _pane_list(self, args: Tuple[str, ...]) -> Any:
        ws = self._opt(args, "--workspace")
        return {"panes": [self._pane_view(p) for p in self.panes.values() if ws in (None, p["workspace_id"])]}

    def _pane_get(self, args: Tuple[str, ...]) -> Any:
        return {"pane": self._pane_view(self._pane(args[2]))}

    def _pane_process_info(self, args: Tuple[str, ...]) -> Any:
        return {"process_info": {"foreground_processes": self._pane(self._opt(args, "--pane") or "")["fg"]}}

    def _pane_split(self, args: Tuple[str, ...]) -> Any:
        src = self._pane(args[2])
        return {"pane": {"pane_id": self.add_pane(src["tab_id"], self._opt(args, "--cwd") or src["cwd"])}}

    def _pane_run(self, args: Tuple[str, ...]) -> Any:
        pane, command = self._pane(args[2]), args[3]
        self.runs.append((args[2], command))
        words = shlex.split(command)
        if len(words) == 3 and words[0] == self.script and words[1] == "wake":
            pane["fg"] = [{"argv0": "python3", "name": "python3", "argv": ["python3", *words], "pid": self._new_pid()}]
        elif words[0] in AGENT_KINDS:
            session = words[words.index("--resume") + 1] if "--resume" in words else (
                words[2] if words[1:2] == ["resume"] else None)
            pane["fg"] = [{"argv0": words[0], "name": words[0], "argv": words, "pid": self._new_pid()}]
            if not pane["claimed"]:
                pane.update(agent=words[0], agent_session={"agent": words[0], "value": session} if session else None)
        return {}

    def _pane_send_keys(self, args: Tuple[str, ...]) -> Any:
        self._pane(args[2])
        return {}

    def _pane_report_agent(self, args: Tuple[str, ...]) -> Any:
        pane = self._pane(args[2])
        if pane["agent"] is None:   # herdr ignores a claim while it still holds an agent for the pane
            pane.update(agent=self._opt(args, "--agent"), claimed=True)
        return {}

    def _pane_release_agent(self, args: Tuple[str, ...]) -> Any:
        pane = self._pane(args[2])
        if pane["claimed"] and pane["agent"] == self._opt(args, "--agent"):
            pane.update(agent=None, claimed=False)
        return {}

    def _pane_report_metadata(self, args: Tuple[str, ...]) -> Any:
        self._pane(args[2])
        return {}

    def _pane_report_agent_session(self, args: Tuple[str, ...]) -> Any:
        self._pane(args[2])["agent_session"] = {"agent": self._opt(args, "--agent"),
                                                "value": self._opt(args, "--agent-session-id")}
        return {}

    # agents

    def _agent_list(self, args: Tuple[str, ...]) -> Any:
        rows = []
        for p in self.panes.values():
            if p["agent"]:
                rows.append({**self._pane_view(p), "agent_status": p["agent_status"],
                             "terminal_title_stripped": p["title"]})
        return {"agents": rows}

    # everything else

    def _notification_show(self, args: Tuple[str, ...]) -> Any:
        self.notifications.append((args[2], self._opt(args, "--body") or ""))
        return {}
