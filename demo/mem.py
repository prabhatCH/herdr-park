#!/usr/bin/env python3
import json, subprocess, time, sys
def herdr(*a):
    return json.loads(subprocess.run(["herdr", *a], capture_output=True, text=True).stdout or "{}").get("result", {})
def snapshot():
    running, rss = 0, 0
    for p in herdr("pane", "list").get("panes", []):
        if p.get("agent") not in ("claude", "codex"):
            continue
        procs = herdr("pane", "process-info", "--pane", p["pane_id"]).get("process_info", {}).get("foreground_processes", [])
        pids = [str(x["pid"]) for x in procs if x.get("pid") and x.get("name") not in ("zsh", "bash", "Python", "python3")]
        if not pids:
            continue
        out = subprocess.run(["ps", "-o", "rss=", "-p", ",".join(pids)], capture_output=True, text=True).stdout
        kb = sum(int(v) for v in out.split() if v.isdigit())
        if kb:
            running += 1
            rss += kb
    return running, rss / 1024 / 1024
while True:
    n, gb = snapshot()
    bar = "█" * int(round(gb * 20))
    sys.stdout.write("\033[H\033[2J\n  \033[1magent memory\033[0m\n\n")
    sys.stdout.write(f"  \033[1;38;5;{214 if gb > 0.05 else 114}m{gb:5.2f} GB\033[0m   \033[38;5;245m{n} agent{'s' if n != 1 else ''} running\033[0m\n\n")
    sys.stdout.write(f"  \033[38;5;{214 if gb > 0.05 else 114}m{bar}\033[0m\n")
    sys.stdout.flush()
    time.sleep(1)
