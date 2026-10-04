# Demo recording

`./record.sh` re-records `docs/demo.gif` (README) and `docs/demo.mp4` (for posts) with
[VHS](https://github.com/charmbracelet/vhs) (`brew install vhs`).

It never shows your real tabs:

- **Separate session:** it runs in a separate herdr session (`demo`) with three Claude tabs on an ordinary-looking demo project, `storefront-api` (a shop's backend).
- **Outside your home folder:** the project lives in `/Users/Shared/code/storefront-api` on macOS (`/tmp/code/storefront-api` elsewhere), because the restore picker shows full paths.
- **Saved entries set aside:** your saved entries are moved aside for the recording and put back afterwards, since the picker is shared across sessions.
- **No other machines:** herdr's sidebar lists saved SSH machines and their tabs, so the recording's herdr client runs with an empty state folder and sees none.
- **Anonymous prompt:** panes use a minimal prompt (`storefront-api ❯`), and VHS records only its own terminal.
- **No home path:** herdr-park types its own absolute path into a hibernated pane (the wake command), so the demo runs a copy from `/Users/Shared/code/bin/` instead of this checkout.

The script starts three short Claude conversations (a few tiny prompts on your account). Claude's banner shows your plan name
("Claude Pro"). Check the frames before publishing:

```sh
for t in 1 6 14 19 27; do ffmpeg -v error -ss $t -i ../docs/demo.mp4 -frames:v 1 frame-$t.png; done
```
