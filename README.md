# herdr-park

A [herdr](https://herdr.dev) plugin that frees the memory held by idle agent tabs without losing their sessions.

An idle Claude Code tab still holds the `claude` process and every MCP server it started: typically 500 MB to 1 GB per tab. With 15 to 20 tabs open that adds up to gigabytes spent on tabs you aren't using. Closing them frees the memory, but then finding the right session again is hard.

herdr-park gives you three things:

- **Park**: save the tab's agent session (session ID, cwd, title, workspace, tab label, original launch flags), then close the tab.
- **Hibernate**: stop the agent but keep the tab. The sidebar keeps a row for it, shown as `· claude · hibernated` (idle agents show `○`), and the tab is renamed `zz <label>`. Press Enter in the tab to resume.
- **Restore**: a search picker over everything you've parked or hibernated. It matches titles, workspace, repo path and the text of your own prompts. Enter resumes the exact session with `claude --resume <id>`, in the right directory, with the flags it was started with.

Tabs you close any other way are saved automatically, so an accidental close is never lost.

## Install

```bash
herdr plugin install prabhatCH/herdr-park
```

Requires herdr 0.9.0 or newer, Python 3.9+ and macOS or Linux. herdr's Claude integration must be installed (`herdr integration install claude`), because that's how herdr learns each pane's session ID.

Bind the actions in `~/.config/herdr/config.toml`, then run `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "herdr-park.restore"
description = "restore a parked or hibernated tab"

[[keys.command]]
key = "prefix+a"
type = "plugin_action"
command = "herdr-park.park"
description = "park this tab"

[[keys.command]]
key = "prefix+i"
type = "plugin_action"
command = "herdr-park.hibernate"
description = "hibernate this tab"
```

On macOS, avoid `alt+` bindings unless your terminal sends Option as Meta. Otherwise Option+letter produces dead keys (`¨`, `π`) that never reach herdr cleanly.

## Actions

| Action | What it does |
| --- | --- |
| `herdr-park.restore` | Search picker. Enter restores; hibernated tabs wake in place. ctrl-d drops an entry, ctrl-u clears, Esc quits. |
| `herdr-park.park` | Park the focused tab (asks first). |
| `herdr-park.hibernate` | Hibernate the focused tab and show how much memory was freed. |
| `herdr-park.hibernate-idle` | Hibernate every idle agent tab except the focused one. |

Run them with `herdr plugin action invoke <action>` or bind them to keys.

## CLI

The plugin script works as a CLI inside any herdr pane. Link it onto your PATH if you want it:

```text
herdr-park park [TAB_ID] [-y]        save the tab's agent sessions, then close the tab
herdr-park hibernate [TAB_ID]        stop the agents but keep the tab; Enter in the tab resumes
herdr-park park-idle [-y]            park every idle agent tab except the focused one
herdr-park hibernate-idle [-y]       hibernate every idle agent tab except the focused one
herdr-park restore [N|TEXT]          search picker (or restore entry N / the one match for TEXT)
herdr-park list                      saved and hibernated tabs
herdr-park drop N                    forget a saved entry
herdr-park import [-y]               add Claude sessions from tabs closed before herdr-park was installed
```

`import` rebuilds a list of tabs closed before you installed the plugin. herdr keeps no history of closed tabs, so it reads Claude Code's transcripts instead. It picks interactive sessions with at least two prompts whose last activity came after herdr's first start, and skips anything already open or saved. The workspace each one restores into is guessed from where tabs for the same directory live today.

## How it works

- **Park and hibernate** refuse a tab whose agent is working or waiting on an approval, and a tab whose session has no transcript yet (nothing to resume). They save the entry before closing the tab or stopping the agent, so a failure never loses a session.
- **Hibernate** sends SIGTERM to the agent. Claude Code exits and takes its MCP servers with it. The plugin waits until herdr has noticed the exit (about 2 seconds), claims the pane, and only then starts a small wait-for-Enter stub, which later execs the agent back into its session. The order matters: with the stub already running, herdr can take 20 seconds or more to notice the exit, and it ignores a claim made before then.
- **The hibernated badge:** herdr drops a pane's agent row when the agent exits. So the plugin claims the pane with `pane report-agent` (source `custom:herdr-park`, agent `hibernated`, state `unknown`, which renders as `·`), and relabels it with `pane report-metadata`. The claim is released before the agent starts again, because a stale claim outranks herdr's own detection. As a safety net, if a claimed pane is found running an agent (Claude, Codex and the others herdr knows) without the stub, the claim is dropped. It checks for an agent specifically, because the shell prompt can briefly run helpers such as `git` right after the agent exits.
- **Automatic capture:** herdr only sends `tab.closed` / `pane.closed` for closes made through its API (`herdr tab close`). Closing with a keybinding (`prefix+shift+x`, `prefix+x`) sends nothing, and the API events carry only IDs anyway. So the plugin keeps a snapshot of every agent pane (session, cwd, title, labels, launch flags), refreshed on agent detection, status changes and tab renames. On each refresh, any snapshot pane that no longer exists is saved as a parked entry, unless the session is still open elsewhere. The API events are handled too. The snapshot is reset at herdr startup, because pane IDs can change across a restart.
- **Multiple herdr sessions** share one list of saved tabs but keep separate snapshots, since pane and tab IDs repeat across sessions.
- **State** lives in the plugin state directory (`~/.local/state/herdr/plugins/herdr-park/`):
  - `parked.json`: saved entries
  - `live-<session>.json`: the snapshot for each herdr session
  - `index.json`: the search cache
  - `error.log`: failures

`index.json` holds the titles and your own prompts for each saved Claude session (never the assistant's replies), so searching stays fast. It stays on your machine; delete it any time and it rebuilds.

## Limits

- Claude Code is fully supported. Codex restores with `codex resume <id>`. herdr doesn't report a session ID for a brand-new Codex session, so the plugin finds it from Codex's session files (`$CODEX_HOME/sessions`): the one started in the pane's folder after the Codex process. It uses the ID only when exactly one file matches, then reports it to herdr. Other agents are saved, but restore only prints their session ID.
- Plain shell panes in a parked tab aren't restored.
- It depends on field names in herdr's CLI output (`agent_session`, `terminal_title_stripped`, `foreground_cwd`) and on Claude Code's transcript format. Tested with herdr 0.9.1 and Claude Code 2.1.

## License

MIT
