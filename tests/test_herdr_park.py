"""herdr-park against a fake herdr: hibernate, wake, park, restore, list/drop, and the close-capture hooks.

Every test drives the real script (through its own `main()` where that makes sense) while the fake in
`fake_herdr.py` plays herdr, `ps`, `os.kill` and `os.execvp`. See conftest.py for how the script is loaded.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
from pathlib import Path

import pytest

from support import SID, entry

SID2 = "22222222-aaaa-4bbb-8ccc-000000000002"
# the flags `work.argv` was started with, minus the session selectors that resuming replaces
KEPT_FLAGS = ["--dangerously-skip-permissions", "--model", "opus"]


def test_harness_keeps_everything_inside_the_tmp_dir(harness, park, tmp_path):
    root = os.path.realpath(tmp_path)
    for path in (park.STATE_DIR, park.PARKED, park.LIVE, park.INDEX, park.ERROR_LOG, park.LEGACY_DIR,
                 park.CLAUDE_DIR, park.codex_home()):
        assert os.path.realpath(path).startswith(root + os.sep), path
    assert not os.path.exists(park.HERDR)   # the real CLI cannot be reached by accident
    assert park.SELF == os.path.realpath(str(Path(park.__file__)))


# ---------------------------------------------------------------- resume recipe

@pytest.mark.parametrize("agent, expected", [
    ({"agent": "claude", "session_id": SID, "argv": ["claude", "--model", "opus", "--resume", "old", "-c"]},
     ["claude", "--model", "opus", "--resume", SID]),
    ({"agent": "claude", "session_id": SID, "argv": ["claude", "-r", "old", "--resume=older", "--continue", "-v"]},
     ["claude", "-v", "--resume", SID]),
    ({"agent": "claude", "session_id": SID, "argv": None}, ["claude", "--resume", SID]),
    ({"agent": "codex", "session_id": SID, "argv": ["codex", "--full-auto"]}, ["codex", "resume", SID]),
    ({"agent": "claude", "session_id": None, "argv": ["claude"]}, None),
    ({"agent": "gemini", "session_id": SID, "argv": ["gemini"]}, None),
])
def test_resume_argv_lands_on_exactly_this_session(park, agent, expected):
    assert park.resume_argv(agent) == expected


# ---------------------------------------------------------------- hibernate

def test_hibernate_stops_the_agent_and_keeps_the_tab(harness, fake, park, work):
    assert harness.cli("hibernate", work.tab) == 0

    assert fake.signals == [(work.pid, signal.SIGTERM)]          # only the agent was signalled, with SIGTERM
    assert work.tab in fake.tabs and not fake.called("tab", "close")
    assert fake.tabs[work.tab]["label"] == "zz api"               # marked asleep in the tab bar
    assert fake.panes[work.pane]["agent"] == "hibernated"         # the sidebar row is held
    assert fake.stub_pane_command(work.pane) == ["python3", park.SELF, "wake", SID]   # Enter resumes it
    assert fake.notifications == [("hibernated work / api", "freed ~200 MB; press Enter in the tab to resume")]

    [saved] = harness.parked()
    assert saved["source"] == "hibernate" and saved["tab_label"] == "api"
    assert saved["hibernated"] == {"tab_id": work.tab, "pane_ids": [work.pane], "session": park.SESSION}
    [agent] = saved["agents"]
    assert (agent["session_id"], agent["cwd"], agent["argv"]) == (SID, "/work/api", work.argv)


@pytest.mark.parametrize("status, transcript, reason", [
    ("working", True, "agent is working"),
    ("idle", False, "has no resumable session yet"),
])
def test_hibernate_refuses_what_it_cannot_resume_or_would_interrupt(harness, fake, status, transcript, reason):
    ws = fake.add_workspace("work")
    s = harness.session(ws, "api", SID, status=status, transcript=transcript)

    assert harness.cli("hibernate", s.tab) == 1

    assert reason in fake.notifications[0][1]
    assert fake.signals == [] and harness.parked() == []          # nothing stopped, nothing saved
    assert fake.tabs[s.tab]["label"] == "api"


# ---------------------------------------------------------------- wake

def test_wake_resumes_the_same_session_with_the_original_flags(harness, fake, work, monkeypatch):
    assert harness.cli("hibernate", work.tab) == 0
    monkeypatch.setenv("HERDR_PANE_ID", work.pane)    # herdr sets this in the pane the stub runs in
    harness.stdin("\n")                               # the user presses Enter

    assert harness.cli("wake", SID) == 0

    # same session id; `--continue` and the stale `-r stale-id` are gone; every other flag is kept
    assert fake.execs == [("claude", ["claude", *KEPT_FLAGS, "--resume", SID])]
    assert harness.parked() == []                     # the hibernation entry is finished
    assert fake.tabs[work.tab]["label"] == "api"      # the zz marker is removed
    assert fake.panes[work.pane]["agent"] is None     # the sidebar claim is released before the agent starts


def test_restore_wakes_a_hibernated_tab_in_place(harness, fake, work, capsys):
    harness.cli("hibernate", work.tab)
    tabs_before = dict(fake.tabs)

    assert harness.cli("restore", "1") == 0

    assert fake.called("pane", "send-keys", work.pane, "enter")   # nudges the wake stub; no new tab
    assert fake.called("tab", "focus", work.tab)
    assert not fake.called("tab", "create") and fake.tabs.keys() == tabs_before.keys()
    assert "woke work / api" in capsys.readouterr().out


def test_restore_opens_a_new_tab_when_the_hibernated_one_was_closed_by_hand(harness, fake, work):
    harness.cli("hibernate", work.tab)
    fake.close_tab_silently(work.tab)

    assert harness.cli("restore", "1") == 0

    [tab] = [t for t in fake.tabs.values() if t["workspace_id"] == work.workspace]
    assert tab["label"] == "api"                      # not "zz api"
    [(pane, command)] = [r for r in fake.runs if shlex.split(r[1])[0] == "claude"]
    assert pane in fake.panes and fake.panes[pane]["tab_id"] == tab["tab_id"]
    assert command == shlex.join(["claude", *KEPT_FLAGS, "--resume", SID])
    assert harness.parked() == []


# ---------------------------------------------------------------- park

def test_park_saves_the_session_before_it_closes_the_tab(harness, fake, work):
    saved_when_closed = []
    fake.before.append(lambda args: saved_when_closed.append(harness.parked()) if args[:2] == ("tab", "close") else None)

    assert harness.cli("park", work.tab, "-y") == 0

    assert work.tab not in fake.tabs and fake.signals == []
    assert [e["agents"][0]["session_id"] for e in saved_when_closed[0]] == [SID]   # already on disk at close time
    [saved] = harness.parked()
    assert (saved["source"], saved["workspace_label"], saved["tab_label"]) == ("park", "work", "api")
    assert "hibernated" not in saved
    [agent] = saved["agents"]
    assert (agent["session_id"], agent["title"], agent["cwd"], agent["argv"]) == (
        SID, "Fix the login bug", "/work/api", work.argv)


def test_park_keeps_the_entry_when_closing_the_tab_fails(harness, fake, work, capsys):
    fake.failures[("tab", "close")] = "tab is pinned"

    assert harness.cli("park", work.tab, "-y") == 1

    assert work.tab in fake.tabs
    assert [e["agents"][0]["session_id"] for e in harness.parked()] == [SID]   # a failed close never loses the session
    assert "tab is pinned" in capsys.readouterr().err
    assert "tab is pinned" in harness.error_log()


def test_park_asks_first_and_does_nothing_without_a_terminal(harness, fake, work, capsys):
    assert harness.cli("park", work.tab) == 0

    assert "not a tty, pass -y to confirm" in capsys.readouterr().out
    assert work.tab in fake.tabs and harness.parked() == []


# ---------------------------------------------------------------- restore

def test_restore_reopens_a_parked_tab_where_it_was(harness, fake, work):
    harness.cli("park", work.tab, "-y")
    assert work.tab not in fake.tabs

    assert harness.cli("restore", "1") == 0

    [tab] = [t for t in fake.tabs.values() if t["workspace_id"] == work.workspace]
    assert tab["label"] == "api" and tab["focused"]
    assert fake.called("tab", "create") == [("tab", "create", "--workspace", work.workspace, "--cwd", "/work/api",
                                             "--focus", "--label", "api")]
    [(pane, command)] = fake.runs
    assert fake.panes[pane]["cwd"] == "/work/api"
    assert command == shlex.join(["claude", *KEPT_FLAGS, "--resume", SID])
    assert fake.panes[pane]["agent_session"]["value"] == SID
    assert harness.parked() == []                     # a restored entry is used up


def test_restore_recreates_a_missing_workspace_and_splits_for_a_second_agent(harness, fake):
    fake.add_workspace("elsewhere")
    saved = entry(SID, "Fix login", workspace="gone", cwd="/work/a")
    saved["agents"].append({**saved["agents"][0], "session_id": SID2, "cwd": "/work/b", "pane_id": "w1:p2"})
    harness.save_entries([saved])

    assert harness.cli("restore", "1") == 0

    [created] = fake.called("workspace", "create")
    assert created == ("workspace", "create", "--cwd", "/work/a", "--label", "gone", "--no-focus")
    [workspace] = [w for w in fake.workspaces.values() if w["label"] == "gone"]
    [tab] = [t for t in fake.tabs.values() if t["workspace_id"] == workspace["workspace_id"]]
    assert tab["label"] == "api"
    assert [c[3] for c in fake.called("pane", "split")] == ["--direction"]     # one split, to the right
    assert [shlex.split(cmd)[-1] for _, cmd in fake.runs] == [SID, SID2]       # each pane resumes its own session
    assert [fake.panes[p]["cwd"] for p, _ in fake.runs] == ["/work/a", "/work/b"]


def test_restore_picks_the_entry_matching_a_title_or_your_own_prompts(harness, fake, capsys):
    fake.add_workspace("work")
    harness.transcript(SID2, prompt="rotate the staging certificates")
    harness.save_entries([entry(SID, "Fix login", tab="auth"), entry(SID2, "Write docs", tab="docs")])

    assert harness.cli("restore", "work") == 1                     # matches both entries
    assert "2 entries match 'work'; be more specific" in capsys.readouterr().err
    assert len(harness.parked()) == 2

    assert harness.cli("restore", "certificates") == 0             # only in a prompt of the second session
    assert [e["agents"][0]["session_id"] for e in harness.parked()] == [SID]
    assert shlex.split(fake.runs[-1][1])[-1] == SID2

    assert harness.cli("restore", "login") == 0
    assert harness.parked() == []


# ---------------------------------------------------------------- list / drop

def test_list_and_drop(harness, capsys):
    assert harness.cli("list") == 0
    assert capsys.readouterr().out.strip() == "nothing saved"

    harness.save_entries([entry(SID, "Fix login"), entry(SID2, "Write docs", tab="docs")])
    assert harness.cli("list") == 0
    out = capsys.readouterr().out
    assert "work / api  ->  Fix login" in out and "work / docs  ->  Write docs" in out

    assert harness.cli("drop", "1") == 0
    assert "dropped" in capsys.readouterr().out
    assert [e["agents"][0]["session_id"] for e in harness.parked()] == [SID2]

    assert harness.cli("drop", "9") == 1
    assert "usage: herdr-park drop N" in capsys.readouterr().err
    assert len(harness.parked()) == 1


# ---------------------------------------------------------------- tabs closed by hand

@pytest.mark.parametrize("hook", ["refresh", "tab-closed"])
def test_hook_saves_a_tab_that_was_closed_by_hand(harness, fake, work, monkeypatch, hook):
    assert harness.cli("hook", "refresh") == 0        # the snapshot of every agent pane that the plugin keeps
    assert harness.parked() == []
    fake.close_tab_silently(work.tab)                 # a keybinding close: herdr tells the plugin nothing
    if hook == "tab-closed":                          # a close through herdr's API sends an event with only ids
        monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps({"data": {"tab_id": work.tab}}))

    assert harness.cli("hook", hook) == 0

    [saved] = harness.parked()
    assert (saved["source"], saved["workspace_label"], saved["tab_label"]) == ("auto", "work", "api")
    [agent] = saved["agents"]
    assert (agent["session_id"], agent["cwd"], agent["argv"]) == (SID, "/work/api", work.argv)   # argv from the snapshot


def test_hook_turns_a_hibernated_tab_closed_by_hand_into_a_plain_saved_one(harness, fake, work):
    harness.cli("hibernate", work.tab)
    fake.close_tab_silently(work.tab)

    assert harness.cli("hook", "refresh") == 0

    [saved] = harness.parked()                        # still one entry, not a duplicate
    assert "hibernated" not in saved and saved["source"] == "auto"
    assert saved["agents"][0]["session_id"] == SID
