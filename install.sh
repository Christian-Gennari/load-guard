#!/usr/bin/env bash
# Install load guard for the current user: puts `lg` on PATH, makes the skill visible to your
# agent harnesses, and starts the user services.
#
#   curl -fsSL https://raw.githubusercontent.com/Christian-Gennari/load-guard/main/install.sh | bash -s -- --rules
#                                 one-line install or update: gets the repository (LG_DIR, default
#                                 ~/.local/share/load-guard, or the folder of an existing install), then runs
#                                 the installer from there with the same options
#   ./install.sh                  lg on PATH + skill links + watchdog + dashboard on http://127.0.0.1:8377/
#   ./install.sh --rules          ... and add the load rules to the global instruction file of each
#                                 agent harness found (Claude Code, Codex, Gemini CLI, OpenCode)
#   ./install.sh --tailscale      ... and publish the dashboard to your tailnet (HTTPS) with Tailscale serve
#   ./install.sh --no-dashboard   no dashboard service
#   ./install.sh --no-services    no systemd user services (lg on PATH and skill links only)
#   ./install.sh --no-skill-links do not link the skill into ~/.agents/skills and ~/.claude/skills
#   ./install.sh --uninstall      remove what this installer added (keeps this folder, the state and the config)
set -euo pipefail

# ---- one-line install: piped into bash, this script has no folder of its own.
# Get the repository (or update the existing install), then run the installer from there.
LG_REPO="${LG_REPO:-https://github.com/Christian-Gennari/load-guard}"
LG_REF="${LG_REF:-main}"
bootstrap() {
  local dir="${LG_DIR:-}" cur
  if [[ -z $dir ]]; then
    cur=$(readlink -f "${LG_BIN_DIR:-$HOME/.local/bin}/lg" 2>/dev/null || true)
    if [[ $cur == */bin/lg && -f ${cur%/bin/lg}/install.sh ]]; then dir=${cur%/bin/lg}; else dir="$HOME/.local/share/load-guard"; fi
  fi
  echo "load guard: $LG_REPO ($LG_REF) -> ${dir/#$HOME/\~}"
  if [[ -d $dir/.git ]]; then
    git -C "$dir" pull -q --ff-only || echo "  ! could not update $dir (local changes?): installing it as it is"
  elif [[ -e $dir && -n $(ls -A "$dir" 2>/dev/null) ]]; then
    echo "  ✗ $dir exists and is not a git clone: set LG_DIR to another folder" >&2; exit 1
  elif command -v git >/dev/null; then
    git clone -q --depth 1 --branch "$LG_REF" "$LG_REPO" "$dir"
  else
    mkdir -p "$dir"; curl -fsSL "$LG_REPO/archive/$LG_REF.tar.gz" | tar -xz -C "$dir" --strip-components=1
  fi
  exec bash "$dir/install.sh" "$@" </dev/null
}
_self="${BASH_SOURCE[0]:-}"
if [[ -z $_self || ! -f $_self || ! -f $(dirname "$_self")/bin/lg ]]; then bootstrap "$@"; fi

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BIN="${LG_BIN_DIR:-$HOME/.local/bin}"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
services=1 dashboard=1 tailscale="" uninstall=0 links=1 rules=0
for a in "$@"; do
  case $a in
    --no-services) services=0;;
    --no-dashboard) dashboard=0;;
    --no-skill-links) links=0;;
    --rules) rules=1;;
    --tailscale) tailscale=--tailscale;;
    --uninstall) uninstall=1;;
    -h|--help) sed -n '2,18p' "$0" | cut -c3-; exit 0;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
tilde() { printf '%s' "${1/#$HOME/\~}"; }

# ---- skill folders: the open Agent Skills format, read by several harnesses ----
#   ~/.agents/skills  Codex, Gemini CLI, Cursor, OpenCode
#   ~/.claude/skills  Claude Code (also read by Cursor and OpenCode)
skill_dirs() { echo "$HOME/.agents/skills"; [[ -d $HOME/.claude ]] && echo "$HOME/.claude/skills"; return 0; }
link_skill() {
  local dir=$1 target="$1/load-guard"
  if [[ $(readlink -f "$target" 2>/dev/null) == "$HERE" ]]; then ok "skill in $(tilde "$dir")"; return; fi
  if [[ -e $target || -L $target ]]; then warn "$(tilde "$target") exists and is another copy: left as it is"; return; fi
  mkdir -p "$dir" && ln -s "$HERE" "$target" && ok "skill linked into $(tilde "$dir")"
}
unlink_skill() {
  local target="$1/load-guard"
  [[ -L $target && $(readlink -f "$target") == "$HERE" ]] && rm -f "$target" && ok "removed the skill link in $(tilde "$1")"
  return 0
}

# ---- global instruction files: rules that every agent follows, also workers that load no skill ----
rule_files() {
  local claude="$HOME/.claude/CLAUDE.md" oc="$HOME/.config/opencode"
  [[ -d $HOME/.claude ]] && echo "$claude"
  [[ -d $CODEX_DIR ]] && echo "$CODEX_DIR/AGENTS.md"
  [[ -d $HOME/.gemini ]] && echo "$HOME/.gemini/GEMINI.md"
  # OpenCode reads ~/.claude/CLAUDE.md only while ~/.config/opencode/AGENTS.md does not exist.
  # Creating that file would hide the Claude rules from OpenCode, so use the Claude file then.
  if [[ -d $oc ]]; then
    if [[ -f $oc/AGENTS.md || ! -d $HOME/.claude ]]; then echo "$oc/AGENTS.md"; fi
  fi
  return 0
}
block() {  # set|remove <file>: the block between the markers, replaced in place or appended
  python3 - "$1" "$2" "$HERE/rules/load-guard-rules.md" <<'PY'
import os, re, sys
mode, path, src = sys.argv[1:4]
begin, end = "<!-- load-guard:begin (managed by load-guard/install.sh) -->", "<!-- load-guard:end -->"
text = open(path).read() if os.path.exists(path) else ""
pat = re.compile(r"\n*" + re.escape(begin.split(" (")[0]) + r".*?" + re.escape(end) + r"\n?", re.S)
had = bool(pat.search(text))
text = pat.sub("\n", text).rstrip("\n")
if mode == "set":
    rules = open(src).read().strip()
    text = (text + "\n\n" if text else "") + f"{begin}\n{rules}\n{end}\n"
elif not had:
    sys.exit(3)
else:
    if not text.strip():   # the installer created this file: remove it again
        os.remove(path)
        sys.exit(0)
    text = text + "\n"
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".lg-tmp"
open(tmp, "w").write(text)
os.replace(tmp, path)
PY
}

# A service belongs to this install only when its unit file runs a program from this folder.
ours() { local f="$HOME/.config/systemd/user/$1.service"; [[ -f $f ]] && grep -qF "$HERE/" "$f"; }

if (( uninstall )); then
  if (( services )); then
    if ours load-guard-dashboard; then "$HERE/bin/lg" dashboard --uninstall >/dev/null 2>&1 || true; ok "removed the dashboard service"; fi
    if ours load-guard-watchdog; then
      systemctl --user disable --now load-guard-watchdog.service >/dev/null 2>&1 || true
      rm -f "$HOME/.config/systemd/user/load-guard-watchdog.service"; systemctl --user daemon-reload >/dev/null 2>&1 || true
      ok "removed the watchdog service"
    fi
  fi
  while read -r d; do unlink_skill "$d"; done < <(skill_dirs)
  for f in "$HOME/.claude/CLAUDE.md" "$CODEX_DIR/AGENTS.md" "$HOME/.gemini/GEMINI.md" "$HOME/.config/opencode/AGENTS.md"; do
    [[ -f $f ]] && block remove "$f" 2>/dev/null && ok "removed the load rules from $(tilde "$f")"
  done
  if [[ -L $BIN/lg && $(readlink -f "$BIN/lg") == "$HERE/bin/lg" ]]; then rm -f "$BIN/lg"; ok "removed $(tilde "$BIN")/lg"; fi
  ok "done (state in ~/.cache/load-guard and config in ~/.config/load-guard.conf are kept)"
  exit 0
fi

echo "Checking requirements"
missing=0
[[ $(uname -s) == Linux ]] && ok "Linux" || { fail "Linux is required"; missing=1; }
for c in bash awk flock pgrep systemctl systemd-run python3; do
  command -v "$c" >/dev/null && ok "$c" || { fail "$c not found"; missing=1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null && ok "Python 3.8+" || { fail "Python 3.8 or newer is required"; missing=1; }
if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then ok "cgroup v2"; else fail "cgroup v2 is required for the job limits (unified hierarchy)"; missing=1; fi
if (( services )); then
  if systemctl --user is-system-running >/dev/null 2>&1 || [[ $(systemctl --user is-system-running 2>/dev/null) == degraded ]]; then ok "systemd user manager"
  else fail "no systemd user manager (log in through a normal session, or use --no-services)"; missing=1; fi
  ctl=$(cat "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control" 2>/dev/null || true)
  [[ $ctl == *memory* ]] && ok "memory controller delegated to the user" || warn "the memory controller is not delegated to your user: MemoryMax may not apply (see the README)"
fi
[[ -r /proc/pressure/memory ]] && ok "memory pressure (PSI)" || warn "/proc/pressure/memory is not readable: the pressure check is off"
(( missing )) && { echo; fail "fix the items above, then run ./install.sh again"; exit 1; }

echo; echo "Installing"
chmod +x "$HERE/bin/lg" "$HERE/install.sh"
mkdir -p "$BIN"
if [[ -e $BIN/lg && ! -L $BIN/lg ]]; then fail "$BIN/lg exists and is not a link; remove it first"; exit 1; fi
ln -sfn "$HERE/bin/lg" "$BIN/lg"; ok "$(tilde "$BIN")/lg -> $(tilde "$HERE")/bin/lg"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH; add it to your shell profile (agents need lg on PATH)";; esac
conf="${XDG_CONFIG_HOME:-$HOME/.config}/load-guard.conf"
if [[ ! -f $conf ]]; then
  mkdir -p "$(dirname "$conf")"; cp "$HERE/config.example.conf" "$conf"; ok "created $(tilde "$conf") (all lines commented: defaults apply)"
else ok "kept $(tilde "$conf")"; fi

cfg=$("$HERE/bin/lg" config)
conf_get() { printf '%s\n' "$cfg" | sed -n "s/^$1=//p"; }
ram_gb=$(awk '/^MemTotal:/{printf "%.0f", $2/1048576}' /proc/meminfo)
echo "Limits for this machine ($ram_gb GB RAM, $(nproc) cores; change them in the dashboard Settings or $(tilde "$conf"))"
echo "  agents at once $(conf_get LG_MAX_WORKERS) · heavy jobs at once $(conf_get LG_MAX_HEAVY) · free memory to start $(conf_get LG_MIN_FREE_GB) GB"
echo "  per heavy job $(conf_get LG_JOB_MEM) memory, $(conf_get LG_JOB_CPU) CPU · watchdog acts below $(conf_get LG_KILL_FREE_GB) GB free"
echo "  After a day of normal use, run: lg tune"

if (( links )); then
  echo; echo "Agent harnesses"
  while read -r d; do link_skill "$d"; done < <(skill_dirs)
  if (( rules )); then
    while read -r f; do block set "$f" && ok "load rules in $(tilde "$f")"; done < <(rule_files)
    [[ -d $HOME/.config/opencode && ! -f $HOME/.config/opencode/AGENTS.md && -d $HOME/.claude ]] \
      && ok "OpenCode reads the rules from ~/.claude/CLAUDE.md"
    [[ -d $HOME/.cursor ]] && warn "Cursor keeps user rules in its settings: paste the output of 'lg rules' into Settings > Rules"
  else
    echo "  Skills load when the agent decides they are relevant. To make every agent (also workers) follow"
    echo "  the load rules, run ./install.sh --rules, or paste the output of 'lg rules' into your AGENTS.md."
  fi
fi

if (( services )); then
  echo; echo "Services"
  "$HERE/bin/lg" watchdog --install >/dev/null && systemctl --user restart load-guard-watchdog.service && ok "watchdog service (load-guard-watchdog)"
  if (( dashboard )); then
    url=$("$HERE/bin/lg" dashboard --install $tailscale | sed -n 's/^dashboard: //p')
    ok "dashboard service (load-guard-dashboard): ${url:-see lg dashboard --url}"
  fi
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
    warn "the services stop when you log out. To keep them running: sudo loginctl enable-linger $USER"
  fi
fi

echo; "$HERE/bin/lg" status
echo; echo "Done. Edit $(tilde "$conf") if the defaults do not fit (lg config shows them). Heavy commands: lg run -- <command>"
