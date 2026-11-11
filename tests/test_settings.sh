#!/usr/bin/env bash
# Settings view end to end: GET, token, Origin, file edits, events, mtime re-read, demo.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
tmp=$(mktemp -d)
srv=""
demo=""
cleanup() {
  [[ -n $srv ]] && kill "$srv" 2>/dev/null || true
  [[ -n $demo ]] && kill "$demo" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT

export LG_CONF=$tmp/load-guard.conf
export XDG_CACHE_HOME=$tmp/cache
mkdir -p "$XDG_CACHE_HOME"
cat >"$LG_CONF" <<'EOF'
# my settings
FOO=bar
LG_MAX_WORKERS=3
EOF

wait_for() {  # url
  for _ in $(seq 1 100); do
    curl -fsS "$1" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  echo "server did not answer: $1" >&2
  cat "$tmp/server.log" >&2 || true
  return 1
}

post() {  # url body [extra curl args...]
  local url=$1 body=$2
  shift 2
  curl -sS -o "$tmp/post.json" -w '%{http_code}' -X POST "$url" \
    -H 'Content-Type: application/json' -H "X-LG-Token: $tok" "$@" --data "$body"
}

python3 "$root/dashboard/server.py" --port 18377 --bind 127.0.0.1 >"$tmp/server.log" 2>&1 &
srv=$!
wait_for http://127.0.0.1:18377/api/settings

# 1. GET: writable, token, 13 items, LG_MAX_WORKERS overridden
curl -fsS http://127.0.0.1:18377/api/settings >"$tmp/settings.json"
python3 - "$tmp/settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["writable"] is True, d["writable"]
assert isinstance(d["token"], str) and d["token"], d["token"]
assert len(d["items"]) == 13, len(d["items"])
by = {i["key"]: i for i in d["items"]}
assert by["LG_MAX_WORKERS"]["overridden"] is True, by["LG_MAX_WORKERS"]
assert by["LG_MAX_WORKERS"]["value"] == "3", by["LG_MAX_WORKERS"]
PY
tok=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$tmp/settings.json")

# 2. no token -> 403; evil Origin -> 403
body='{"set":{"LG_MAX_WORKERS":"5"},"reset":[]}'
code=$(curl -sS -o "$tmp/post.json" -w '%{http_code}' -X POST http://127.0.0.1:18377/api/settings \
  -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:18377' --data "$body")
[ "$code" = 403 ] || { echo "no token: expected 403, got $code"; exit 1; }
code=$(post http://127.0.0.1:18377/api/settings "$body" -H 'Origin: http://evil.example')
[ "$code" = 403 ] || { echo "evil origin: expected 403, got $code"; exit 1; }

# 3. set LG_MAX_WORKERS=5: the rest of the file survives, lg sees it, events.log records it
code=$(post http://127.0.0.1:18377/api/settings "$body" -H 'Origin: http://127.0.0.1:18377')
[ "$code" = 200 ] || { echo "set: expected 200, got $code"; cat "$tmp/post.json"; exit 1; }
grep -qx '# my settings' "$LG_CONF" || { echo "comment line lost"; cat "$LG_CONF"; exit 1; }
grep -qx 'FOO=bar' "$LG_CONF" || { echo "unknown key lost"; cat "$LG_CONF"; exit 1; }
cfg=$("$root/bin/lg" config)
grep -qx 'LG_MAX_WORKERS=5' <<<"$cfg" || { echo "lg config shows the wrong value"; cat <<<"$cfg"; exit 1; }
grep -q 'config LG_MAX_WORKERS 3 -> 5' "$XDG_CACHE_HOME/load-guard/events.log" || { echo "missing events.log line"; cat "$XDG_CACHE_HOME/load-guard/events.log"; exit 1; }

# 4. an invalid value: 400 with that key, file byte-identical
cp "$LG_CONF" "$tmp/before.conf"
code=$(post http://127.0.0.1:18377/api/settings '{"set":{"LG_MAX_WORKERS":"abc"},"reset":[]}' -H 'Origin: http://127.0.0.1:18377')
[ "$code" = 400 ] || { echo "invalid value: expected 400, got $code"; exit 1; }
python3 - "$tmp/post.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert "LG_MAX_WORKERS" in d.get("errors", {}), d
PY
cmp -s "$LG_CONF" "$tmp/before.conf" || { echo "file changed after a rejected value"; exit 1; }

# 5. a glob is written single-quoted and lg reads it back
code=$(post http://127.0.0.1:18377/api/settings '{"set":{"LG_EXCLUSIVE_MATCH":"*npm test*"},"reset":[]}' -H 'Origin: http://127.0.0.1:18377')
[ "$code" = 200 ] || { echo "glob set: expected 200, got $code"; cat "$tmp/post.json"; exit 1; }
grep -qx "LG_EXCLUSIVE_MATCH='\*npm test\*'" "$LG_CONF" || { echo "glob not quoted"; cat "$LG_CONF"; exit 1; }
cfg=$("$root/bin/lg" config)
grep -Fqx 'LG_EXCLUSIVE_MATCH=*npm test*' <<<"$cfg" || { echo "lg config lost the glob"; cat <<<"$cfg"; exit 1; }

# 6. reset removes the line and GET reports overridden false
code=$(post http://127.0.0.1:18377/api/settings '{"set":{},"reset":["LG_MAX_WORKERS"]}' -H 'Origin: http://127.0.0.1:18377')
[ "$code" = 200 ] || { echo "reset: expected 200, got $code"; cat "$tmp/post.json"; exit 1; }
! grep -q '^[[:space:]]*LG_MAX_WORKERS=' "$LG_CONF" || { echo "reset left the line"; cat "$LG_CONF"; exit 1; }
curl -fsS http://127.0.0.1:18377/api/settings >"$tmp/settings.json"
python3 - "$tmp/settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
by = {i["key"]: i for i in d["items"]}
assert by["LG_MAX_WORKERS"]["overridden"] is False, by["LG_MAX_WORKERS"]
assert by["LG_MAX_WORKERS"]["value"] == by["LG_MAX_WORKERS"]["default"], by["LG_MAX_WORKERS"]
PY

# 7. a hand edit is seen within 5 s
printf 'LG_MAX_HEAVY=1\n' >>"$LG_CONF"
seen=0
for _ in $(seq 1 50); do
  curl -fsS http://127.0.0.1:18377/api/settings >"$tmp/settings.json"
  if python3 - "$tmp/settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
by = {i["key"]: i for i in d["items"]}
assert by["LG_MAX_HEAVY"]["value"] == "1", by["LG_MAX_HEAVY"]
PY
  then seen=1; break; fi
  sleep 0.1
done
[ "$seen" = 1 ] || { echo "hand edit not seen within 5 s"; exit 1; }

# 8. a proxy header makes it read-only, and the old token is refused
curl -fsS -H 'X-Forwarded-For: 1.2.3.4' http://127.0.0.1:18377/api/settings >"$tmp/settings.json"
python3 - "$tmp/settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["writable"] is False, d["writable"]
assert d["token"] is None, d["token"]
PY
code=$(post http://127.0.0.1:18377/api/settings '{"set":{"LG_MAX_WORKERS":"5"},"reset":[]}' \
  -H 'Origin: http://127.0.0.1:18377' -H 'X-Forwarded-For: 1.2.3.4')
[ "$code" = 403 ] || { echo "forwarded request: expected 403, got $code"; exit 1; }

# 9. demo: read-only, no writes, the real config unchanged
cp "$LG_CONF" "$tmp/before-demo.conf"
python3 "$root/dashboard/server.py" --demo --port 18378 --bind 127.0.0.1 >"$tmp/demo.log" 2>&1 &
demo=$!
wait_for http://127.0.0.1:18378/api/settings
curl -fsS http://127.0.0.1:18378/api/settings >"$tmp/demo-settings.json"
python3 - "$tmp/demo-settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["writable"] is False, d["writable"]
assert d["token"] is None, d["token"]
assert "Demo data" in d["why_readonly"], d["why_readonly"]
assert len(d["items"]) == 13, len(d["items"])
PY
code=$(curl -sS -o "$tmp/post.json" -w '%{http_code}' -X POST http://127.0.0.1:18378/api/settings \
  -H 'Content-Type: application/json' -H 'X-LG-Token: whatever' -H 'Origin: http://127.0.0.1:18378' \
  --data '{"set":{"LG_MAX_WORKERS":"5"},"reset":[]}')
[ "$code" = 403 ] || { echo "demo POST: expected 403, got $code"; exit 1; }
cmp -s "$LG_CONF" "$tmp/before-demo.conf" || { echo "demo wrote the config file"; exit 1; }

echo "ALL OK"
