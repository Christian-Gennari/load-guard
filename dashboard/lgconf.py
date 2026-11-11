#!/usr/bin/env python3
"""load guard config file: parse, validate, write, and log the changes.

The Settings view (server.py) and `lg tune --apply` share this module, so both write the
file the same way. As a command:

    python3 lgconf.py set [--source NAME] KEY=VALUE ...

Only the keys of the Settings view are accepted. On a bad key or value it prints
"KEY: message" lines to stderr, exits 2 and leaves the file unchanged. Changed keys are
printed as "KEY OLD -> NEW" and logged to the event log as "config KEY OLD -> NEW".
"""
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
LG = os.path.realpath(os.path.join(HERE, "..", "bin", "lg"))
STATE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "load-guard")
EVENTS = os.path.join(STATE, "events.log")
CONF = os.environ.get("LG_CONF") or os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "load-guard.conf")
DEMO = False  # server.py sets this for `--demo`, where nothing is read or written


def read(path, default=""):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return default


# ---- lg config (limits), re-read every 30 s or when the file changes ----
_conf = {"at": 0.0, "mtime": None, "v": {}}
_defaults = {"at": 0.0, "v": {}}


def config_mtime():
    try:
        return os.stat(CONF).st_mtime_ns
    except OSError:
        return None


def config():
    if DEMO:
        return {}
    mtime = config_mtime()
    if time.time() - _conf["at"] > 30 or mtime != _conf["mtime"]:
        try:
            out = subprocess.run([LG, "config"], capture_output=True, text=True, timeout=10).stdout
            _conf["v"] = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        except (OSError, subprocess.SubprocessError):
            pass
        _conf["at"], _conf["mtime"] = time.time(), mtime
    return _conf["v"]


def config_reset():
    _conf["at"], _conf["mtime"] = 0.0, None


def defaults():
    """`lg config` as if no config file and no LG_* variable existed: the machine's own defaults."""
    if DEMO:
        return {}
    if time.time() - _defaults["at"] > 300:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LG_")}
        env["LG_CONF"] = "/dev/null"
        try:
            out = subprocess.run([LG, "config"], capture_output=True, text=True, timeout=10, env=env).stdout
            _defaults["v"] = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        except (OSError, subprocess.SubprocessError):
            pass
        _defaults["at"] = time.time()
    return _defaults["v"]


def cfg_num(key, default):
    try:
        return float(config().get(key, default))
    except ValueError:
        return float(default)


# ---- settings: the limits people may change from the dashboard or `lg tune` ----
SETTINGS = [
    ("LG_MAX_WORKERS", "Agents (workers) at once", "int", 1, 64),
    ("LG_MIN_FREE_GB", "Free memory to admit an agent (GB)", "number", 0.5, 1024),
    ("LG_MAX_LOAD", "Load limit to admit an agent", "number", 1, 4096),
    ("LG_MAX_HEAVY", "Heavy jobs at once", "int", 1, 64),
    ("LG_MAX_HEAVY_TOTAL", "Heavy jobs + CI jobs at once", "int", 1, 64),
    ("LG_HEAVY_FREE_GB", "Free memory to start a heavy job (GB)", "number", 0.5, 1024),
    ("LG_HEAVY_MAX_LOAD", "Load limit to start a heavy job", "number", 1, 4096),
    ("LG_MAX_PSI", "Memory pressure limit (%)", "number", 1, 100),
    ("LG_KILL_FREE_GB", "Watchdog stops the newest job below (GB)", "number", 0.1, 1024),
    ("LG_JOB_MEM", "Memory limit per heavy job", "size"),
    ("LG_JOB_CPU", "CPU limit per heavy job (100% = 1 core)", "percent"),
    ("LG_EXCLUSIVE_MATCH", "Commands that run one at a time (glob)", "text"),
    ("LG_EXCLUSIVE_UNLESS", "...except when they also match (glob)", "text"),
]
SPECS = {row[0]: row for row in SETTINGS}
CONF_LINE_RE = re.compile(r"^\s*(LG_[A-Z_]+)=(.*)$")
NUMBER_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
SIZE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[KMGT]$")
PERCENT_RE = re.compile(r"^[0-9]+%$")
_save_lock = threading.Lock()


def conf_value(raw):
    """One config value: a quoted string as-is, otherwise up to a trailing ` # comment`."""
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v).rstrip()


def file_values():
    """The keys the config file sets, last occurrence wins. Comments do not count."""
    values = {}
    for line in read(CONF).splitlines():
        m = CONF_LINE_RE.match(line)
        if m:
            values[m[1]] = conf_value(m[2])
    return values


def validate(key, value):
    row = SPECS[key]
    kind = row[2]
    if kind == "int":
        lo, hi = row[3], row[4]
        if not re.match(r"^[0-9]+$", value):
            return f"Use a whole number from {lo} to {hi}."
        if not lo <= int(value) <= hi:
            return f"Use a whole number from {lo} to {hi}."
    elif kind == "number":
        lo, hi = row[3], row[4]
        if not NUMBER_RE.match(value):
            return f"Use a number from {lo} to {hi}."
        if not lo <= float(value) <= hi:
            return f"Use a number from {lo} to {hi}."
    elif kind == "size":
        if not SIZE_RE.match(value):
            return "Use a size like 4G or 512M."
    elif kind == "percent":
        if not PERCENT_RE.match(value):
            return "Use a percentage like 400%."
        if not 10 <= int(value[:-1]) <= 100000:
            return "Use a percentage from 10 to 100000."
    else:
        if len(value) > 200:
            return "Use 200 characters or fewer."
        if any(c in value for c in ("\n", "\r", "'")):
            return "Use plain text without quotes or line breaks."
    return None


def quote_value(value):
    return "'" + value + "'" if re.search(r"[^A-Za-z0-9._%-]", value) else value


def write_settings(sets, resets=(), source="lg"):
    """Rewrite only the lines that belong to the changed keys; every other line stays as it was."""
    try:
        with open(CONF, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.readlines()
        mode = os.stat(CONF).st_mode & 0o777
    except OSError:
        lines, mode = [], 0o644

    def hits(key):
        return [i for i, line in enumerate(lines)
                if CONF_LINE_RE.match(line) and CONF_LINE_RE.match(line)[1] == key]

    appended = False
    for key, value in sets.items():
        new = f"{key}={quote_value(value)}"
        found = hits(key)
        if found:
            last = found[-1]
            lines[last] = new + lines[last][len(lines[last].rstrip("\r\n")):]
            for i in reversed(found[:-1]):
                del lines[i]
        else:
            if not appended:
                if lines and not lines[-1].strip():
                    pass
                elif lines:
                    if not lines[-1].endswith("\n"):
                        lines[-1] += "\n"
                    lines.append("\n")
                lines.append(f"# set by {source}\n")
                appended = True
            lines.append(new + "\n")
    for key in resets:
        lines = [line for line in lines if not (CONF_LINE_RE.match(line) and CONF_LINE_RE.match(line)[1] == key)]

    parent = os.path.dirname(os.path.abspath(CONF))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{CONF}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.writelines(lines)
        os.chmod(tmp, mode)
        os.replace(tmp, CONF)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def log_event(text):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(EVENTS, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def set_values(sets, source="lg"):
    """Write validated values, log and print each key that changed. Returns the changed keys."""
    before = dict(config())
    with _save_lock:
        write_settings(sets, source=source)
    config_reset()
    after = config()
    changed = []
    for key, _ in sets.items():
        old, new = before.get(key, ""), after.get(key, "")
        if new != old:
            changed.append(key)
            log_event(f"config {key} {old} -> {new}")
            print(f"{key} {old} -> {new}")
    return changed


def usage(message):
    print(message, file=sys.stderr)
    print("usage: lgconf.py set [--source NAME] KEY=VALUE ...", file=sys.stderr)
    return 2


def main(argv):
    if not argv or argv[0] != "set":
        return usage("expected: set [--source NAME] KEY=VALUE ...")
    source, pairs, args = "lg", [], argv[1:]
    while args:
        a = args.pop(0)
        if a == "--source":
            if not args:
                return usage("--source needs a name")
            source = args.pop(0)
        elif a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif a.startswith("-"):
            return usage(f"unknown option: {a}")
        else:
            pairs.append(a)
    if not pairs:
        return usage("no settings given")

    sets, errors = {}, []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            errors.append((pair, "Use KEY=VALUE."))
        elif key not in SPECS:
            errors.append((key, "Unknown setting."))
        else:
            message = validate(key, value)
            if message:
                errors.append((key, message))
            else:
                sets[key] = value
    if errors:
        for key, message in errors:
            print(f"{key}: {message}", file=sys.stderr)
        return 2
    try:
        set_values(sets, source=source)
    except OSError as e:
        print(f"{CONF}: {e.strerror or e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
