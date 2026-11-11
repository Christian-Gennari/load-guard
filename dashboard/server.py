#!/usr/bin/env python3
"""Live dashboard for load guard (lg). Python 3.8+, standard library only.

    python3 server.py                 serve the live page (lg dashboard --install runs this as a user service)
    python3 server.py --demo          serve synthetic data, to try the page without load guard
    options: --port N --bind ADDR     (defaults: LG_DASH_PORT / LG_DASH_BIND from the lg config, 8377 / 127.0.0.1)

GET /             the page
GET /events       Server-Sent Events: "sample" every LG_DASH_INTERVAL s, "log" for new event lines
GET /api/status   one sample as JSON
GET /api/history  ?hours=N  the watchdog's one-per-minute metrics as JSON columns
GET /api/recent   ?hours=N  finished heavy jobs and watchdog actions from the event log
GET /api/settings           the editable limits and their defaults
POST /api/settings          change or reset limits (only from localhost, demo never writes)

Samples are taken only while a browser is connected, so an unwatched dashboard costs nothing.
"""
import glob
import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import lgconf
from lgconf import (CONF, SETTINGS, SPECS, cfg_num, config, config_reset, defaults,
                    file_values, log_event, validate, write_settings)

HERE = os.path.dirname(os.path.realpath(__file__))
DEMO = "--demo" in sys.argv
lgconf.DEMO = DEMO
LG = os.path.realpath(os.path.join(HERE, "..", "bin", "lg"))
STATE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "load-guard")
JOBS, WORKERS, QUEUE = (os.path.join(STATE, d) for d in ("jobs", "workers", "queue"))
EVENTS, METRICS = os.path.join(STATE, "events.log"), os.path.join(STATE, "metrics.log")
TOKEN = secrets.token_urlsafe(24)
BIND = "127.0.0.1"
LOCAL = ("127.0.0.1", "::1", "[::1]", "localhost")
UID = os.getuid()
CG_BASE = f"/sys/fs/cgroup/user.slice/user-{UID}.slice/user@{UID}.service"
PAGE = os.sysconf("SC_PAGE_SIZE")
HOST = os.uname().nodename
JOB_RE = re.compile(r"^(\S+) slot=(\S+) mem=(\S+) cpu=(\S+) cwd=(.*?) cmd=(.*)$", re.S)


def read(path, default=""):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def num(text):
    try:
        return int(text.strip())
    except ValueError:  # "max" or missing
        return None


# ---- settings view (the config file itself lives in lgconf) ----
READONLY_NET = ("The dashboard is opened over the network. "
                "Set LG_DASH_ALLOW_WRITE=1 in load-guard.conf to allow changes.")
_save_lock = threading.Lock()


def can_write(handler):
    """Writes need localhost and no proxy in front, or LG_DASH_ALLOW_WRITE=1. Demo never writes."""
    if DEMO:
        return False
    if config().get("LG_DASH_ALLOW_WRITE") == "1":
        return True
    if BIND not in LOCAL:
        return False
    if urlparse("//" + (handler.headers.get("Host") or "")).hostname not in LOCAL:
        return False
    return not any(handler.headers.get(h) for h in ("X-Forwarded-For", "X-Forwarded-Host", "Tailscale-User-Login"))


def settings(handler):
    if DEMO:
        return demo.settings()
    writable = can_write(handler)
    values, defs, over = config(), defaults(), file_values()
    items = []
    for row in SETTINGS:
        key = row[0]
        item = {"key": key, "label": row[1], "type": row[2],
                "value": values.get(key, defs.get(key, "")), "default": defs.get(key, ""),
                "overridden": key in over}
        if len(row) > 3:
            item["min"], item["max"] = row[3], row[4]
        items.append(item)
    return {"writable": writable, "why_readonly": "" if writable else READONLY_NET,
            "token": TOKEN if writable else None, "path": CONF, "items": items}


# ---- one sample ----
_cpu_prev = None
_cg_cache = {}
_job_cpu = {}  # unit -> (cpu usage in usec, time) of the previous sample


_swap_prev = None


def swap_rate():
    """MB/s swapped in + out since the last sample: real memory trouble, unlike swap used."""
    global _swap_prev
    v = dict(line.split() for line in read("/proc/vmstat").splitlines() if line.startswith(("pswpin ", "pswpout ")))
    now, pages = time.time(), int(v.get("pswpin", 0)) + int(v.get("pswpout", 0))
    prev, _swap_prev = _swap_prev, (now, pages)
    if not prev or now <= prev[0]:
        return None
    return round((pages - prev[1]) * PAGE / 1048576 / (now - prev[0]), 2)


def cpu_pct():
    global _cpu_prev
    parts = [int(x) for x in read("/proc/stat").split("\n", 1)[0].split()[1:9]]
    idle, total = parts[3] + parts[4], sum(parts)
    prev, _cpu_prev = _cpu_prev, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def cgroup_dir(unit):
    d = _cg_cache.get(unit)
    if d and os.path.isdir(d):
        return d
    d = os.path.join(CG_BASE, "app.slice", unit + ".scope")
    if not os.path.isdir(d):
        hits = glob.glob(os.path.join(CG_BASE, "**", unit + ".scope"), recursive=True)
        d = hits[0] if hits else None
    if d:
        _cg_cache[unit] = d
    return d


def jobs():
    out = []
    for path in sorted(glob.glob(os.path.join(JOBS, "*.job"))):
        unit = os.path.basename(path)[:-4]
        m = JOB_RE.match(read(path).strip())
        cg = cgroup_dir(unit)
        if not m or not cg:  # finished; lg removes the file on its next check
            continue
        started = re.match(r"lg-(\d+)-", unit)
        usage, now, cores = re.search(r"usage_usec (\d+)", read(cg + "/cpu.stat")), time.time(), None
        if usage:
            prev, _job_cpu[unit] = _job_cpu.get(unit), (int(usage[1]), now)
            if prev and now > prev[1]:
                cores = round((int(usage[1]) - prev[0]) / 1e6 / (now - prev[1]), 2)
        out.append({
            "unit": unit, "slot": m[2], "mem_limit": num(read(cg + "/memory.max")), "cpu": m[4],
            "cwd": m[5], "cmd": m[6], "started": int(started[1]) if started else None,
            "mem_now": num(read(cg + "/memory.current")), "mem_peak": num(read(cg + "/memory.peak")),
            "cores": cores,
        })
    for unit in [u for u in _cg_cache if not any(j["unit"] == u for j in out)]:
        del _cg_cache[unit]
        _job_cpu.pop(unit, None)
    return out


def queued():
    out = []
    for path in glob.glob(os.path.join(QUEUE, "*.q")):
        pid = os.path.basename(path)[:-2]
        if "/lg\0" not in read(f"/proc/{pid}/cmdline"):  # the waiting lg process is gone
            continue
        f = read(path).rstrip("\n").split("\t", 4)
        if len(f) == 5:
            out.append({"pid": int(pid), "since": int(f[0]), "kind": f[1], "why": f[2], "cwd": f[3], "cmd": f[4]})
    return sorted(out, key=lambda q: q["since"])


def workers():
    stale = cfg_num("LG_STALE_HOURS", 8) * 3600
    out = []
    for path in glob.glob(os.path.join(WORKERS, "*.w")):
        try:
            since = os.path.getmtime(path)
        except OSError:
            continue
        out.append({"name": os.path.basename(path)[:-2], "task": read(path).strip().split("\n")[0], "since": int(since),
                    "stale": time.time() - since > stale})
    return sorted(out, key=lambda w: w["since"])


def processes():
    """Resident memory per program name (top 8), and whether the watchdog runs."""
    groups, watchdog = {}, False
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        comm = read(f"/proc/{pid}/comm").strip()
        statm = read(f"/proc/{pid}/statm").split()
        if not comm or len(statm) < 2:
            continue
        name = comm
        if comm == "MainThread":  # node names its main thread; the program file is the better name
            try:
                name = os.path.basename(os.readlink(f"/proc/{pid}/exe")).replace(" (deleted)", "")
            except OSError:
                pass
        g = groups.setdefault(name, [0, 0])
        g[0] += int(statm[1]) * PAGE
        g[1] += 1
        if comm in ("bash", "lg") and "/lg\0watchdog" in read(f"/proc/{pid}/cmdline"):
            watchdog = True
    top = sorted(groups.items(), key=lambda kv: -kv[1][0])[:8]
    return [{"name": k, "rss": v[0], "count": v[1]} for k, v in top], watchdog


_lg = {"at": 0.0, "sig": None, "v": None}


def lg_status(sig):
    """`lg status --json` is the one source of the ADMIT/WAIT verdicts, but it costs ~160 ms of CPU
    in forks. Ask again only every 6 s, or at once when the jobs, workers or queue change."""
    if sig != _lg["sig"] or time.time() - _lg["at"] > 6:
        try:
            out = subprocess.run([LG, "status", "--json"], capture_output=True, text=True, timeout=15).stdout
            _lg["v"] = json.loads(out)
        except (OSError, subprocess.SubprocessError, ValueError):
            _lg["v"] = None
        _lg["at"], _lg["sig"] = time.time(), sig
    return _lg["v"]


def sample():
    mem = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            mem[k] = int(v.split()[0]) * 1024
    psi = re.search(r"some avg10=([\d.]+)", read("/proc/pressure/memory"))
    j, q, w = jobs(), queued(), workers()
    lg = lg_status(([x["unit"] for x in j], [x["pid"] for x in q], [x["name"] for x in w]))
    top, watchdog = processes()
    return {
        "t": time.time(), "host": HOST, "cores": os.cpu_count(),
        "mem": {"total": mem.get("MemTotal", 0), "free": mem.get("MemAvailable", 0),
                "swap_total": mem.get("SwapTotal", 0),
                "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)},
        "load": [float(x) for x in read("/proc/loadavg").split()[:3]],
        "cpu_pct": cpu_pct(), "psi": float(psi[1]) if psi else 0.0, "swap_rate": swap_rate(),
        "lg": lg, "watchdog": watchdog,
        "limits": {
            "min_free_gb": cfg_num("LG_MIN_FREE_GB", 4), "heavy_free_gb": cfg_num("LG_HEAVY_FREE_GB", 4),
            "kill_free_gb": cfg_num("LG_KILL_FREE_GB", 1.5), "max_load": cfg_num("LG_MAX_LOAD", 16),
            "heavy_max_load": cfg_num("LG_HEAVY_MAX_LOAD", 12), "max_psi": cfg_num("LG_MAX_PSI", 5),
            "max_heavy": cfg_num("LG_MAX_HEAVY", 2), "max_workers": cfg_num("LG_MAX_WORKERS", 3),
            "max_heavy_total": cfg_num("LG_MAX_HEAVY_TOTAL", 3),
            "job_mem": config().get("LG_JOB_MEM", "4G"), "job_cpu": config().get("LG_JOB_CPU", "400%"),
        },
        "jobs": j, "queue": q, "workers": w, "top": top,
    }


def history(hours):
    cols = {k: [] for k in ("t", "free", "swap", "load", "psi", "heavy", "workers", "runners")}
    since = time.time() - hours * 3600
    try:
        size = os.path.getsize(METRICS)
        want = int(hours * 60 * 64) + 4096  # one line per minute, under 64 bytes each
        with open(METRICS, "rb") as f:
            if size > want:
                f.seek(size - want)
                f.readline()
            for line in f:
                p = line.split()
                if len(p) < 7 or float(p[0]) < since:
                    continue
                vals = [float(x) for x in p[:8]] + [0.0] * (8 - len(p))
                for k, v in zip(cols, vals):
                    cols[k].append(v)
    except (OSError, ValueError):
        pass
    return cols


EV_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$")
START_RE = re.compile(r"^start (\S+) slot=\S+ cwd=(.*?) cmd=(.*)$")
END_RE = re.compile(r"^end (\S+) rc=(-?\d+) dur=(\d+)s peak=(\S+)(?: cmd=(.*))?")


def recent(hours):
    """Finished heavy jobs and watchdog actions in the last N hours, from the event log."""
    since, started, done, dog = time.time() - hours * 3600, {}, [], []
    try:
        with open(EVENTS, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 2 * 1024 * 1024))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        m = EV_RE.match(line)
        if not m:
            continue
        text = m[2]
        s = START_RE.match(text)
        if s:
            started[s[1]] = (s[2], s[3])
            continue
        if not (text.startswith("end ") or text.startswith("WATCHDOG")):
            continue
        try:
            t = time.mktime(time.strptime(m[1], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if t < since:
            continue
        e = END_RE.match(text)
        if e:
            cwd, cmd = started.pop(e[1], ("", ""))
            peak = re.match(r"(\d+)MB$", e[4])
            done.append({"unit": e[1], "end": t, "dur": int(e[3]), "rc": int(e[2]), "cmd": e[5] or cmd, "cwd": cwd,
                         "peak": int(peak[1]) * 1048576 if peak else None,
                         "stopped": "stopped by watchdog" in text})
        elif text.startswith("WATCHDOG stopped"):
            dog.append({"t": t, "kind": "stop", "text": text[9:]})
        elif text.startswith("WATCHDOG low"):
            dog.append({"t": t, "kind": "low", "text": text[9:]})
    return {"jobs": done[-300:], "watchdog": dog[-300:]}


def log_tail(n=80):
    try:
        with open(EVENTS, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - 64 * 1024))
            return f.read().decode(errors="replace").splitlines()[-n:], end
    except OSError:
        return [], 0


# ---- hub: one sampler thread, many SSE clients ----
class Hub:
    def __init__(self):
        self.clients, self.cond = set(), threading.Condition()
        self.last, self.log_pos = None, 0

    def add(self):
        q = queue.Queue(maxsize=64)
        lines, end = (demo.log_lines(), 0) if DEMO else log_tail()
        q.put(("log", {"lines": lines, "reset": True}))
        with self.cond:
            if not self.clients:
                self.log_pos = end
            elif self.last and time.time() - self.last["t"] < 10:
                q.put(("sample", self.last))
            self.clients.add(q)
            self.cond.notify()
        return q

    def remove(self, q):
        with self.cond:
            self.clients.discard(q)

    def send(self, kind, data):
        with self.cond:
            for q in list(self.clients):
                try:
                    q.put_nowait((kind, data))
                except queue.Full:  # a stuck client: drop it, the browser reconnects
                    self.clients.discard(q)

    def new_log_lines(self):
        if DEMO:
            return []
        try:
            size = os.path.getsize(EVENTS)
            if size < self.log_pos:
                self.log_pos = 0
            if size == self.log_pos:
                return []
            with open(EVENTS, "rb") as f:
                f.seek(self.log_pos)
                data = f.read()
            data = data[: data.rfind(b"\n") + 1]  # whole lines only
            self.log_pos += len(data)
            return data.decode(errors="replace").splitlines()
        except OSError:
            return []

    def run(self):
        while True:
            with self.cond:
                while not self.clients:
                    self.cond.wait()
            t0 = time.time()
            try:
                self.last = demo.sample() if DEMO else sample()
                self.send("sample", self.last)
                lines = self.new_log_lines()
                if lines:
                    self.send("log", {"lines": lines, "reset": False})
            except Exception as e:  # keep the sampler alive
                print("sample failed:", repr(e), flush=True)
            time.sleep(max(0.5, cfg_num("LG_DASH_INTERVAL", 2) - (time.time() - t0)))


HUB = Hub()
FONTS = {"/fonts/inter.woff2": "inter.woff2", "/fonts/jetbrains-mono.woff2": "jetbrains-mono.woff2"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype, code=200, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, message):
        self.reply(json.dumps({"error": message}).encode(), "application/json", code)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.reply(f.read(), "text/html; charset=utf-8")
        elif url.path in FONTS:
            with open(os.path.join(HERE, "fonts", FONTS[url.path]), "rb") as f:
                self.reply(f.read(), "font/woff2", cache="public, max-age=604800")
        elif url.path == "/api/status":
            self.reply(json.dumps(demo.sample() if DEMO else sample()).encode(), "application/json")
        elif url.path == "/api/settings":
            self.reply(json.dumps(settings(self)).encode(), "application/json")
        elif url.path in ("/api/history", "/api/recent"):
            try:
                hours = min(48.0, max(0.1, float(parse_qs(url.query).get("hours", ["24"])[0])))
            except ValueError:
                hours = 24.0
            src = demo if DEMO else sys.modules[__name__]
            data = src.history(hours) if url.path == "/api/history" else src.recent(hours)
            self.reply(json.dumps(data).encode(), "application/json")
        elif url.path == "/events":
            self.stream()
        else:
            self.reply(b"not found\n", "text/plain", 404)

    def do_POST(self):
        if urlparse(self.path).path == "/api/settings":
            self.post_settings()
        else:
            self.reply(b"not found\n", "text/plain", 404)

    def post_settings(self):
        if DEMO:
            return self.err(403, "Demo data: changes are not saved.")
        if not can_write(self):
            return self.err(403, READONLY_NET)
        token = (self.headers.get("X-LG-Token") or "").encode()
        if not hmac.compare_digest(token, TOKEN.encode()):
            return self.err(403, "The page is out of date. Reload the dashboard and try again.")
        origin, host = self.headers.get("Origin"), (self.headers.get("Host") or "").lower()
        if not origin or urlparse(origin).netloc.lower() != host:
            return self.err(403, "The request came from another page.")

        try:
            size = int(self.headers.get("Content-Length") or "")
        except ValueError:
            size = 0
        if size <= 0:
            return self.err(400, "The request is empty.")
        if size > 8192:
            return self.err(400, "The request is too large.")
        try:
            body = json.loads(self.rfile.read(size))
        except (OSError, ValueError):
            return self.err(400, "The request is not valid JSON.")
        if not isinstance(body, dict):
            return self.err(400, "The request is not valid.")

        sets_in, resets_in = body.get("set") or {}, body.get("reset") or []
        if not isinstance(sets_in, dict) or not isinstance(resets_in, list):
            return self.err(400, "The request is not valid.")
        errors, sets, resets = {}, {}, []
        for key, value in sets_in.items():
            if key not in SPECS:
                errors[str(key)] = "Unknown setting."
            elif not isinstance(value, str):
                errors[str(key)] = "Use text."
            else:
                message = validate(key, value)
                if message:
                    errors[key] = message
                else:
                    sets[key] = value
        for key in resets_in:
            if not isinstance(key, str) or key not in SPECS:
                errors[str(key)] = "Unknown setting."
            else:
                resets.append(key)
        for key in set(sets) & set(resets):
            errors[key] = "Choose a value or a reset, not both."
        if errors:
            return self.reply(json.dumps({"errors": errors}).encode(), "application/json", 400)

        def target(key):
            if key in sets:
                return sets[key]
            if key in resets:
                return defaults().get(key, config().get(key, ""))
            return config().get(key, defaults().get(key, ""))
        try:
            heavy, total = int(target("LG_MAX_HEAVY")), int(target("LG_MAX_HEAVY_TOTAL"))
        except (TypeError, ValueError):
            heavy = total = None
        if heavy is not None and total is not None and total < heavy:
            errors["LG_MAX_HEAVY_TOTAL"] = f"Must be at least the number of heavy jobs ({heavy})."
            return self.reply(json.dumps({"errors": errors}).encode(), "application/json", 400)

        before = dict(config())
        try:
            with _save_lock:
                write_settings(sets, resets, "the dashboard")
        except OSError:
            return self.err(500, "Could not write the config file.")
        config_reset()
        after, defs = config(), defaults()
        for key in list(sets) + resets:
            old = before.get(key, "")
            new = defs.get(key, "") if key in resets else after.get(key, "")
            if new != old:
                log_event(f"config {key} {old} -> {new}")
        self.reply(json.dumps(settings(self)).encode(), "application/json")

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = HUB.add()
        try:
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            HUB.remove(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def arg(name, default):
    if name in sys.argv[:-1]:
        return sys.argv[sys.argv.index(name) + 1]
    return default


if __name__ == "__main__":
    if DEMO:
        sys.path.insert(0, HERE)
        import demo  # noqa: E402
    BIND = arg("--bind", config().get("LG_DASH_BIND", "127.0.0.1"))
    port = int(arg("--port", int(cfg_num("LG_DASH_PORT", 8377))))
    threading.Thread(target=HUB.run, daemon=True).start()
    print(f"load guard dashboard{' (demo data)' if DEMO else ''} on http://{BIND}:{port}/", flush=True)
    Server((BIND, port), Handler).serve_forever()
