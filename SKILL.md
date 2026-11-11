---
name: load-guard
description: Keep this machine from running out of memory or CPU when several agents or workers run at once. Use whenever the user says "load guard", and automatically before starting 2 or more parallel workers or subagents (Agent/Task fan-out, background CLI agents, delegated tasks) that may run builds or tests. Gives an admission gate for new workers, a queue, shared slots plus cgroup memory/CPU limits for heavy commands (lg run), a memory watchdog and a live dashboard.
---

# Load guard

Parallel agents that each start a full build or test run can push a shared machine into swap or the out-of-memory killer. This skill keeps parallel work inside a budget.

Tool: `lg` (on PATH via `~/.local/bin/lg`; otherwise `bin/lg` in this skill's folder). State and lock files are in `~/.cache/load-guard/`, shared by every session of the user on the machine, so the limits hold across sessions.

**Machine notes:** if `~/.config/load-guard.notes.md` exists, read it first. It holds what was measured on this machine (peak memory and duration of its builds and test suites, other services, CI runners). Add new measurements there, not here.

## Commands

| Command | What it does |
|---|---|
| `lg status` (`--json`) | Free memory, swap, load, memory pressure, heavy jobs, workers, ADMIT/WAIT for a new worker and for a new heavy job (with the reasons), watchdog state, dashboard URL. |
| `lg admit` | Exit 0 and `ADMIT` when a new worker may start; else exit 1 and `WAIT: <reasons>`. `--wait` blocks and re-checks every 30 s (max 90 min, then exit 75). |
| `lg worker start <name>` / `end <name>` / `touch` / `list` | Register a running worker. Registrations older than 8 h are ignored. |
| `lg run [--mem 3G] [--cpu 400%] -- <cmd…>` | Run a heavy command (tests, builds, Playwright). Waits for a free machine-wide slot and enough free memory, then runs it in its own systemd scope with MemoryMax, MemorySwapMax, CPUQuota and nice. Commands that match `LG_EXCLUSIVE_MATCH` run one at a time. Logs duration and peak memory per job. Exit 75 = stopped by the watchdog or queue timeout: wait and rerun. |
| `lg watchdog` (`--install`) | Below `LG_KILL_FREE_GB` free memory, kills the newest `lg run` job (only those) and logs it. Also writes one metrics line per minute to `~/.cache/load-guard/metrics.log`. Installed as user service `load-guard-watchdog`. |
| `lg report [hours]` | Min free memory, max swap/load/pressure, max parallel jobs/workers, watchdog actions, and per-job duration and peak memory. Use it to tune the limits. |
| `lg dashboard` (`--install [--tailscale]`, `--uninstall`, `--url`, `--demo`) | Live web page for the user (default `http://127.0.0.1:8377/`). Give the user `lg dashboard --url` when they ask how busy the machine is. |
| `lg events` (`-f`, `N`) | Event log: job start/end, worker start/end, watchdog actions. |
| `lg config` | Effective limits. |

Defaults come from the machine (memory and core count) and can be changed in `~/.config/load-guard.conf` (see `config.example.conf`), not in the script. Run `lg config` to see them.

## Tuning

During a day of parallel work, check `lg report` now and then (the dashboard shows the same numbers under "Last 24 hours", with advice). At the end, decide from it: if free memory never went under ~5 GB and the watchdog never acted, try one more worker (`LG_MAX_WORKERS`); if it went under 3 GB or the watchdog acted, lower `LG_MAX_HEAVY` to 1. Write the change to `~/.config/load-guard.conf` and the evidence to `~/.config/load-guard.notes.md`.

## Orchestrator procedure

1. Run `lg status`. If the watchdog is not running, run `lg watchdog --install`.
2. Before each worker start, run `lg admit`. On `WAIT`, keep the task in your queue; do not start it. Re-check when a worker finishes or after a few minutes (a monitor or until-loop on `lg admit`, not a tight sleep loop). Start tasks in queue order.
3. After the start, run `lg worker start <task-name>`. When the worker finishes, fails or is cancelled, run `lg worker end <task-name>`. Do this for workers of every kind (subagents, background CLI agents, delegated tasks).
4. Put the worker brief block below in every worker prompt that may build or test.
5. For long runs, watch `lg events -f`, and run `lg report 2` from time to time, so you see watchdog stops. If a worker reports exit 75, tell it to wait and rerun, or lower `LG_MAX_WORKERS` if it repeats.
6. Run your own heavy commands through `lg run` too.

Limits of the design: only commands that go through `lg run` get hard cgroup limits; the rest depends on the workers following the brief. The watchdog only kills `lg run` jobs. If memory runs low with no `lg run` job active, it logs that line: then find the offender with `ps -eo pid,rss,comm --sort=-rss | head` and stop that worker.

## Worker brief block (paste into each worker prompt)

```
Machine load rules (this machine is shared):
- Run every build, test, lint-with-typecheck or Playwright command through the load guard:
  lg run -- <command>      (example: lg run -- npm test -- --include src/app/foo)
  If lg is not on PATH, use ~/.claude/skills/load-guard/bin/lg.
- Run the narrowest test target that proves your change (one test file, class or package).
  Run each full suite at most once, at the end, and only when your change needs it.
- Use few parallel test workers (for example Playwright --workers=1, dotnet build -m:2, pytest -n 2).
- Never start dev servers, watchers or `--watch` modes and leave them running.
- If lg prints "queued", wait; do not bypass it. If a command ends with exit code 75
  ("STOPPED BY LOAD GUARD"), wait 2 minutes and run the same command again (max 2 times), then report it.
```
