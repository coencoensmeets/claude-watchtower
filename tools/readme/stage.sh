#!/usr/bin/env bash
# Stand up the README's demo machine: made-up sessions under /home/sam and a
# panel on port 8790 that sees only them.
#
#   tools/readme/stage.sh            # runs until Ctrl-C, then cleans up
#
# It runs in a private user and mount namespace, so /home/sam exists only in
# there and your own /home, sessions and pins are out of sight. Needs
# unprivileged user namespaces (unshare -rm) and Node 22.6+ on PATH or in
# NODE_BIN. Then run tools/readme/capture.mjs against http://127.0.0.1:8790.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
NODE_DIR="$(dirname "$(command -v "${NODE_BIN:-node}")")"
WORK="${WORK:-/tmp/wt-readme}"
mkdir -p "$WORK/repo" "$WORK/node"

if [[ "${1:-}" != "--inside" ]]; then
  exec unshare -rm --propagation private "$0" --inside "$REPO" "$NODE_DIR"
fi
REPO="$2"; NODE_DIR="$3"

# Keep the repository and Node reachable, then put an empty /home on top.
mount --bind "$REPO" "$WORK/repo"
mount --bind "$NODE_DIR" "$WORK/node"
mount -t tmpfs none /home
mkdir -p /home/sam
# A stand-in `claude` that does nothing, so Settings -> Claude skills has
# something to find. Nothing in the capture turns a switch that would run it.
mkdir -p "$WORK/bin"
printf '#!/bin/sh\nexit 0\n' > "$WORK/bin/claude"; chmod +x "$WORK/bin/claude"
export PATH="$WORK/bin:$WORK/node:$PATH"
cd "$WORK/repo"

DEMO_HOME=/home/sam python3 tools/readme/demo.py "$WORK/demo" > "$WORK/demo.log" 2>&1 &
DEMO=$!
for _ in $(seq 50); do grep -q "demo sessions" "$WORK/demo.log" 2>/dev/null && break; sleep 0.2; done

HOME=/home/sam CLAUDE_WATCHTOWER_SESSION_DIR="$WORK/demo/sessions" \
  python3 server.py --local --port 8790 --no-build > "$WORK/panel.log" 2>&1 &
PANEL=$!
trap 'kill $PANEL $DEMO 2>/dev/null; wait 2>/dev/null; echo "demo down"' EXIT INT TERM
for _ in $(seq 50); do curl -sf 127.0.0.1:8790/api/state >/dev/null && break; sleep 0.2; done

# Three of the skills switched on, the way the switches link them.
mkdir -p /home/sam/.claude/skills
for k in watchtower watchtower-preview watchtower-style; do ln -sfn "$WORK/repo/skills/$k" "/home/sam/.claude/skills/$k"; done

# What the roastery session made, shown in its Preview tab, and one starred.
mkdir -p /home/sam/coffee-roastery/design
cp docs/design/styleguide.html /home/sam/coffee-roastery/design/index.html
SID=$(python3 -c "import json;print([p['sid'] for p in json.load(open('$WORK/demo/plan.json')) if 'coffee' in p['cwd']][0])")
post() { curl -sf -XPOST "127.0.0.1:8790$1" -H 'content-type: application/json' -d "$2" >/dev/null; }
post /api/previews "{\"sessionId\":\"$SID\",\"target\":\"/home/sam/coffee-roastery/design/index.html\",\"title\":\"Roastery design system\"}"
post /api/previews "{\"sessionId\":\"$SID\",\"target\":\"/home/sam/coffee-roastery/site/index.html\",\"title\":\"Order beans\"}"
PID=$(curl -s 127.0.0.1:8790/api/state | python3 -c "import json,sys;print(json.load(sys.stdin)['previews']['$SID'][0]['id'])")
post /api/previews/star "{\"sessionId\":\"$SID\",\"id\":\"$PID\"}"

echo "demo panel ready on http://127.0.0.1:8790"
wait $PANEL
