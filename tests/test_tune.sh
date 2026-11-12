#!/usr/bin/env bash
# Scaled defaults and `lg tune`: defaults table, not-enough-data, suggestions, --apply, lgconf errors.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

export LG_CONF=$tmp/load-guard.conf
export XDG_CACHE_HOME=$tmp/cache
state=$XDG_CACHE_HOME/load-guard
mkdir -p "$state"
lg=$root/bin/lg

fail() { echo "$*" >&2; exit 1; }
reset_state() { rm -rf "$state"; mkdir -p "$state"; }

# 1. defaults scale with RAM and cores; a config value still wins
: >"$LG_CONF"
check_defaults() {  # ram cores expected-heavy expected-workers
  local ram=$1 cores=$2 want_h=$3 want_w=$4 out h w
  out=$(LG_TEST_RAM_GB=$ram LG_TEST_CORES=$cores "$lg" config)
  h=$(sed -n 's/^LG_MAX_HEAVY=//p' <<<"$out")
  w=$(sed -n 's/^LG_MAX_WORKERS=//p' <<<"$out")
  [[ $h == "$want_h" && $w == "$want_w" ]] \
    || fail "defaults for ${ram} GB / ${cores} cores: heavy=$h workers=$w, want heavy=$want_h workers=$want_w"
}
check_defaults 8 4 1 2
check_defaults 14 12 2 3
check_defaults 64 16 4 5
check_defaults 128 64 8 8
printf 'LG_MAX_HEAVY=5\n' >"$LG_CONF"
out=$("$lg" config)
grep -qx 'LG_MAX_HEAVY=5' <<<"$out" || fail "a config value did not win over the default: $out"

write_metrics() {  # count min-free heavy workers runners
  local n=$1 low=$2 heavy=$3 workers=$4 runners=$5 now i free
  now=$(date +%s)
  : >"$state/metrics.log"
  for ((i = n - 1; i >= 0; i--)); do
    free=8.0
    (( i == n / 2 )) && free=$low
    printf '%s %s 0 1.0 0 %s %s %s\n' "$((now - i * 60))" "$free" "$heavy" "$workers" "$runners" >>"$state/metrics.log"
  done
}

write_jobs() {  # count peak-mb rc [cmd] — appends; reset_state empties the log
  local n=$1 peak=$2 rc=$3 cmd=${4:-test} ts i
  ts=$(date '+%F %T')
  for ((i = 1; i <= n; i++)); do
    printf '%s end lg-1000-%s rc=%s dur=10s peak=%sMB cmd=%s\n' "$ts" "$i" "$rc" "$peak" "$cmd" >>"$state/events.log"
  done
}

row() { awk -v k="$1" '$1 == k {print $2, $3}' <<<"$2"; }

# 2. fewer than 60 samples: no advice yet, exit 0
reset_state
: >"$LG_CONF"
write_metrics 30 9 0 0 0
out=$("$lg" tune)
grep -q 'Not enough data yet' <<<"$out" || fail "30 samples: expected 'Not enough data yet', got: $out"

# 3. low free memory and 6 finished jobs: lower heavy, raise the job limit to 1.5G
reset_state
cat >"$LG_CONF" <<'EOF'
# keep this line
LG_MAX_HEAVY=3
LG_JOB_MEM=4G
EOF
cp "$LG_CONF" "$tmp/before3.conf"
write_metrics 120 2.5 2 3 0
write_jobs 6 1000 0
out=$("$lg" tune)
[[ $(row LG_MAX_HEAVY "$out") == "3 2" ]] || fail "expected LG_MAX_HEAVY 3 -> 2, got: $out"
[[ $(row LG_JOB_MEM "$out") == "4G 1.5G" ]] || fail "expected LG_JOB_MEM 4G -> 1.5G, got: $out"
grep -q 'Run lg tune --apply to write these to' <<<"$out" || fail "missing the --apply hint: $out"
cmp -s "$LG_CONF" "$tmp/before3.conf" || fail "tune without --apply changed the config file"

# 4. --apply writes both, logs the events and keeps the comment line
out=$("$lg" tune --apply)
cfg=$("$lg" config)
grep -qx 'LG_MAX_HEAVY=2' <<<"$cfg" || fail "apply did not set LG_MAX_HEAVY=2: $cfg"
grep -qx 'LG_JOB_MEM=1.5G' <<<"$cfg" || fail "apply did not set LG_JOB_MEM=1.5G: $cfg"
grep -qx '# keep this line' "$LG_CONF" || fail "apply lost the comment line"
grep -q 'config LG_MAX_HEAVY' "$state/events.log" || fail "missing the LG_MAX_HEAVY event: $(cat "$state/events.log")"
grep -q 'config LG_JOB_MEM' "$state/events.log" || fail "missing the LG_JOB_MEM event: $(cat "$state/events.log")"

# 5. plenty of free memory with 2 heavy jobs of 3 slots: one more worker
reset_state
printf 'LG_MAX_WORKERS=3\nLG_MAX_HEAVY=3\n' >"$LG_CONF"
write_metrics 120 8 2 3 0
: >"$state/events.log"
out=$(LG_TEST_RAM_GB=32 LG_TEST_CORES=12 "$lg" tune)
[[ $(row LG_MAX_WORKERS "$out") == "3 4" ]] || fail "expected LG_MAX_WORKERS 3 -> 4, got: $out"
[[ $(grep -c '^LG_' <<<"$out") == 1 ]] || fail "expected only the worker suggestion, got: $out"

# 7. every heavy slot often in use while memory sat free: one more slot, up to cores / 4,
#    and the memory per job shrinks so that 3 slots still leave LG_MIN_FREE_GB free
export LG_TEST_RAM_GB=15
reset_state
printf 'LG_MAX_HEAVY=2\nLG_HEAVY_FREE_GB=4\nLG_MIN_FREE_GB=4\nLG_JOB_MEM=5G\n' >"$LG_CONF"
write_metrics 120 8 2 3 0
write_jobs 6 1000 0
out=$(LG_TEST_CORES=12 "$lg" tune)
[[ $(row LG_MAX_HEAVY "$out") == "2 3" ]] || fail "expected LG_MAX_HEAVY 2 -> 3, got: $out"
[[ $(row LG_JOB_MEM "$out") == "5G 1.5G" ]] || fail "expected LG_JOB_MEM 5G -> 1.5G, got: $out"
[[ $(grep -c '^LG_' <<<"$out") == 2 ]] || fail "expected the slot and memory suggestions only, got: $out"
reset_state
write_metrics 120 8 2 3 0
write_jobs 6 3000 0  # most jobs need 4G (3000 MB + 30 %), but 3 slots give 3.5G each: no third slot
out=$(LG_TEST_CORES=12 "$lg" tune)
[[ -z $(row LG_MAX_HEAVY "$out") ]] || fail "jobs too big for a third slot: expected no slot change, got: $out"
grep -q 'would give each job only 3.5G' <<<"$out" || fail "expected a note on the slot share, got: $out"
reset_state
write_metrics 120 8 2 3 0
out=$(LG_TEST_CORES=8 "$lg" tune)  # 8 cores: 2 slots is the cap
[[ -z $(row LG_MAX_HEAVY "$out") ]] || fail "8 cores: expected no slot change at the 2-slot cap, got: $out"
printf 'LG_MAX_HEAVY=2\nLG_HEAVY_FREE_GB=7\n' >"$LG_CONF"  # 8 GB free is not 2 GB above the start floor
out=$(LG_TEST_CORES=12 "$lg" tune)
[[ -z $(row LG_MAX_HEAVY "$out") ]] || fail "too little room above LG_HEAVY_FREE_GB: expected no slot change, got: $out"

# 8. the suggestions fit together: 3 slots x LG_JOB_MEM within RAM - LG_MIN_FREE_GB, and a full
#    test suite (LG_EXCLUSIVE_MATCH) gets its own larger limit that takes several slots
reset_state
cat >"$LG_CONF" <<'EOF'
LG_MAX_HEAVY=3
LG_MIN_FREE_GB=4
LG_JOB_MEM=5G
LG_EXCLUSIVE_MATCH='*dotnet test*'
LG_EXCLUSIVE_UNLESS='*--filter*'
EOF
write_metrics 120 6 1 3 0
write_jobs 10 4096 0 'npm test'
write_jobs 3 4346 0 'dotnet test App.Tests.csproj'
write_jobs 3 1000 0 'dotnet test App.Tests.csproj --filter Foo'
out=$(LG_TEST_CORES=12 "$lg" tune)
[[ $(row LG_JOB_MEM "$out") == "5G 3.5G" ]] || fail "expected LG_JOB_MEM 5G -> 3.5G (11 GB / 3 slots), got: $out"
[[ $(row LG_EXCLUSIVE_MEM "$out") == "5G 6G" ]] || fail "expected LG_EXCLUSIVE_MEM 5G -> 6G, got: $out"
grep -q 'takes 2 of 3 slots' <<<"$out" || fail "expected 'takes 2 of 3 slots', got: $out"
grep -q '^Note: 10 of 13 jobs peaked near or above 3.5G' <<<"$out" || fail "expected a note on big jobs, got: $out"
"$lg" tune --apply >/dev/null
out=$(LG_TEST_CORES=12 "$lg" tune)  # applied once: the plan is stable
grep -q 'No change' <<<"$out" || fail "second tune after --apply: expected no change, got: $out"
unset LG_TEST_RAM_GB

# 6. an invalid value: exit 2 and the file is byte-identical
cp "$LG_CONF" "$tmp/before6.conf"
rc=0
python3 "$root/dashboard/lgconf.py" set LG_MAX_WORKERS=abc >"$tmp/out6" 2>"$tmp/err6" || rc=$?
[[ $rc == 2 ]] || fail "lgconf.py on an invalid value: expected exit 2, got $rc"
cmp -s "$LG_CONF" "$tmp/before6.conf" || fail "lgconf.py changed the file after a rejected value"

echo "ALL OK"
