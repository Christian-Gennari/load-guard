"""Synthetic data for `server.py --demo`: a busy build machine, so the page can be tried
(or photographed) without load guard installed and without showing real jobs or paths."""
import math
import time

GB = 1 << 30
T0 = time.time()
TOTAL, SWAP = 16 * GB, 8 * GB
LIMITS = {"min_free_gb": 4.0, "heavy_free_gb": 4.0, "kill_free_gb": 1.5, "max_load": 16.0, "heavy_max_load": 12.0,
          "max_psi": 5.0, "max_heavy": 2.0, "max_workers": 3.0, "max_heavy_total": 3.0, "job_mem": "4G", "job_cpu": "400%"}
COMMANDS = [
    ("npm test -- --watch=false --include src/app/reader", "~/code/webapp/frontend", 210, 3.4),
    ("dotnet test --filter Payments", "~/code/webapp/backend", 95, 2.1),
    ("npm run build", "~/code/webapp/frontend", 40, 1.8),
    ("npx playwright test e2e/checkout.spec.ts --workers=1", "~/code/webapp/frontend", 150, 1.2),
    ("cargo test -p parser", "~/code/tools/parser", 70, 1.6),
    ("dotnet build -m:2", "~/code/webapp/backend", 45, 2.3),
    ("pytest tests/unit -x", "~/code/ml-pipeline", 120, 0.9),
]


def wave(t, period, phase=0.0):
    return math.sin(2 * math.pi * (t / period) + phase)


def free_gb(t):
    return 7.2 + 1.1 * wave(t, 1800) + 0.6 * wave(t, 420, 1) - 0.9 * max(0, wave(t, 5400, 2))


def load(t):
    return max(0.3, 5.0 + 2.2 * wave(t, 900, .5) + 1.4 * wave(t, 240, 2) + 2.5 * max(0, wave(t, 7200, 1)))


def heavy(t):
    return 2 if wave(t, 600) > -.2 else 1 if wave(t, 600) > -.8 else 0


def sample():
    t = time.time()
    jobs = []
    for slot, (cmd, cwd, _, peak), started in (("1", COMMANDS[0], T0 - 95), ("2", COMMANDS[3], T0 - 32)):
        now = min(peak, peak * (0.55 + 0.25 * wave(t, 37, int(slot)) + 0.15))
        jobs.append({"unit": "lg-demo-" + slot, "slot": slot, "mem_limit": 4 * GB, "cpu": "400%", "cwd": cwd, "cmd": cmd,
                     "started": int(started), "mem_now": int(now * GB), "mem_peak": int(peak * 0.92 * GB),
                     "cores": round(2.6 + 0.9 * wave(t, 13, int(slot)), 2)})
    workers = [{"name": "api-refactor", "task": "Move the payments API to the new client", "since": int(T0 - 2900), "stale": False},
               {"name": "docs-update", "task": "Update the setup guide for the new CLI flags", "since": int(T0 - 610), "stale": False}]
    queue = [{"pid": 1, "since": int(T0 - 18), "kind": "heavy", "why": "all 2 heavy slots busy",
              "cwd": "~/code/webapp/backend", "cmd": "dotnet build -m:2"}]
    top = [("node", 2.9, 24), ("dotnet", 2.2, 3), ("chrome", 1.4, 18), ("python3", 0.9, 9),
           ("postgres", 0.6, 12), ("code", 0.5, 7), ("dockerd", 0.2, 1), ("redis-server", 0.1, 1)]
    f = free_gb(t)
    return {
        "t": t, "host": "build-box", "cores": 12,
        "mem": {"total": TOTAL, "free": int(f * GB), "swap_total": SWAP, "swap_used": int((1.3 + .1 * wave(t, 900)) * GB)},
        "load": [round(load(t), 2), round(load(t - 150), 2), round(load(t - 600), 2)],
        "cpu_pct": round(48 + 14 * wave(t, 23) + 6 * wave(t, 7), 1), "psi": round(max(0, .4 + .5 * wave(t, 60)), 2),
        "swap_rate": round(max(0, .3 * wave(t, 45)), 2),
        "lg": {"free_gb": round(f, 1), "heavy": 2, "max_heavy": 2, "workers": 2, "max_workers": 3, "runners": 0,
               "max_heavy_total": 3, "admit_worker": True, "worker_wait": "", "admit_heavy": False,
               "heavy_wait": "all 2 heavy slots busy"},
        "watchdog": True, "limits": LIMITS, "jobs": jobs, "queue": queue, "workers": workers,
        "top": [{"name": n, "rss": int(g * GB), "count": c} for n, g, c in top],
    }


def history(hours):
    now = time.time()
    cols = {k: [] for k in ("t", "free", "swap", "load", "psi", "heavy", "workers", "runners")}
    t = now - hours * 3600
    while t < now:
        cols["t"].append(t)
        cols["free"].append(round(free_gb(t), 1))
        cols["swap"].append(1.3)
        cols["load"].append(round(load(t), 2))
        cols["psi"].append(round(max(0, .4 + .5 * wave(t, 60)), 2))
        cols["heavy"].append(heavy(t))
        cols["workers"].append(2 if wave(t, 3000) > -.5 else 1)
        cols["runners"].append(1 if wave(t, 2700, 1) > .7 else 0)
        t += 60
    return cols


def recent(hours):
    now, out, t, i = time.time(), [], time.time() - hours * 3600, 0
    while t < now - 120:
        cmd, cwd, dur, peak = COMMANDS[i % len(COMMANDS)]
        d = int(dur * (0.8 + 0.4 * ((i * 37) % 10) / 10))
        rc = 1 if i % 11 == 5 else 0
        out.append({"unit": f"lg-demo-{i}", "end": t + d, "dur": d, "rc": rc, "cmd": cmd, "cwd": cwd,
                    "peak": int(peak * (0.85 + 0.15 * ((i * 13) % 10) / 10) * GB), "stopped": False})
        t += d + 600 + (i * 211) % 1500
        i += 1
    return {"jobs": out[-300:], "watchdog": []}


def log_lines():
    lines = []
    for j in [j for j in recent(24)["jobs"] if j["end"] > time.time() - 5400]:
        ts = lambda x: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x))
        lines.append(f"{ts(j['end'] - j['dur'])} start {j['unit']} slot=1 cwd={j['cwd']} cmd={j['cmd']}")
        lines.append(f"{ts(j['end'])} end {j['unit']} rc={j['rc']} dur={j['dur']}s peak={j['peak'] >> 20}MB cmd={j['cmd']}")
    ts = lambda x: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x))
    lines += [f"{ts(T0 - 2905)} admit ADMIT for=api-refactor",
              f"{ts(T0 - 2900)} worker start api-refactor task=Move the payments API to the new client",
              f"{ts(T0 - 1500)} admit WAIT for=ui-tests why=free memory 3.6 GB < 4 GB",
              f"{ts(T0 - 615)} admit ADMIT for=docs-update",
              f"{ts(T0 - 610)} worker start docs-update task=Update the setup guide for the new CLI flags",
              f"{ts(T0 - 95)} start lg-demo-1 slot=1 cwd=~/code/webapp/frontend cmd={COMMANDS[0][0]}",
              f"{ts(T0 - 32)} start lg-demo-2 slot=2 cwd=~/code/webapp/frontend cmd={COMMANDS[3][0]}"]
    return sorted(lines)
