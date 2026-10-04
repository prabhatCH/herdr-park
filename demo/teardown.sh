#!/bin/sh
# Stops the demo session, removes the demo project and its Claude transcripts, and puts your saved
# entries back (keeping anything the plugin saved from your real session in the meantime).
DEMO="$(cd "$(dirname "$0")" && pwd)"
STATE="${HERDR_PARK_STATE:-$HOME/.local/state/herdr/plugins/herdr-park}"
PROJECT="${DEMO_PROJECT:-$( [ "$(uname)" = Darwin ] && echo /Users/Shared/code/storefront-api || echo /tmp/code/storefront-api )}"
TRANSCRIPTS="$HOME/.claude/projects/$(echo "$PROJECT" | tr '/' '-')"
herdr session stop demo >/dev/null 2>&1; sleep 1; herdr session delete demo >/dev/null 2>&1
python3 - "$DEMO/.state-aside" "$STATE" "$TRANSCRIPTS" "$PROJECT" <<'PY'
import glob, json, os, sys
aside, state, transcripts, project = sys.argv[1:5]
# Every Claude session the demo created (their transcripts live in the demo project's folder).
demo_ids = {os.path.basename(f)[:-6] for f in glob.glob(os.path.join(transcripts, "*.jsonl"))}
def is_demo(v):
    s = json.dumps(v)
    return project in s or any(i in s for i in demo_ids)
for name in ("parked.json", "index.json"):
    src, dst = os.path.join(aside, name), os.path.join(state, name)
    if not os.path.exists(src):
        if os.path.exists(dst) and name == "parked.json":
            kept = [e for e in json.load(open(dst)) if not is_demo(e)]
            json.dump(kept, open(dst, "w"))
        continue
    original = json.load(open(src))
    current = json.load(open(dst)) if os.path.exists(dst) else ([] if isinstance(original, list) else {})
    if isinstance(original, list):
        merged = original + [e for e in current if not is_demo(e) and e not in original]
    else:
        merged = {**{k: v for k, v in current.items() if k not in demo_ids and not is_demo(v)}, **original}
    json.dump(merged, open(dst, "w"))
    os.remove(src)
    print(f"restored {name}: {len(merged)} entries")
PY
rm -rf "$PROJECT" "$(dirname "$PROJECT")/bin" "$TRANSCRIPTS"
rmdir "$(dirname "$PROJECT")" 2>/dev/null
echo "teardown done"
