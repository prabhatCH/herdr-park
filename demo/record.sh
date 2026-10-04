#!/bin/sh
# Records docs/demo.gif and docs/demo.mp4. Needs herdr, Claude Code, node, python3 and vhs.
# Starts three short Claude conversations (a few small prompts on your account).
set -e
DEMO="$(cd "$(dirname "$0")" && pwd)"
clean() { env -u HERDR_SOCKET_PATH -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_ENV "$@"; }
# herdr's client lists every saved SSH machine and its tabs in the sidebar. The recording's client
# gets an empty state folder, so only the demo session shows.
CLIENT_STATE="$(mktemp -d)"
trap 'clean "$DEMO/teardown.sh"; rm -rf "$CLIENT_STATE"' EXIT
cd "$DEMO"
clean node setup.mjs
clean env ZDOTDIR="$DEMO/zdotdir" XDG_STATE_HOME="$CLIENT_STATE" vhs demo.tape
mv demo.gif demo.mp4 "$DEMO/../docs/"
echo "wrote docs/demo.gif and docs/demo.mp4. Check every frame before publishing."
