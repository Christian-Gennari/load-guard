#!/usr/bin/env bash
# Install load guard for the current user: puts `lg` on PATH and starts the user services.
#
#   ./install.sh                  lg on PATH + watchdog + dashboard on http://127.0.0.1:8377/
#   ./install.sh --tailscale      ... and publish the dashboard to your tailnet (HTTPS) with Tailscale serve
#   ./install.sh --no-dashboard   lg on PATH + watchdog only
#   ./install.sh --no-services    lg on PATH only (no systemd user services)
#   ./install.sh --uninstall      stop and remove the services and the PATH link (keeps this folder and ~/.cache/load-guard)
#
# For use as a Claude Code skill, clone this repository to ~/.claude/skills/load-guard first.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BIN="${LG_BIN_DIR:-$HOME/.local/bin}"
services=1 dashboard=1 tailscale="" uninstall=0
for a in "$@"; do
  case $a in
    --no-services) services=0;;
    --no-dashboard) dashboard=0;;
    --tailscale) tailscale=--tailscale;;
    --uninstall) uninstall=1;;
    -h|--help) sed -n '2,10p' "$0" | cut -c3-; exit 0;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

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
  if [[ -L $BIN/lg && $(readlink -f "$BIN/lg") == "$HERE/bin/lg" ]]; then rm -f "$BIN/lg"; ok "removed $BIN/lg"; fi
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
ln -sfn "$HERE/bin/lg" "$BIN/lg"; ok "$BIN/lg -> $HERE/bin/lg"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH; add it to your shell profile";; esac
conf="${XDG_CONFIG_HOME:-$HOME/.config}/load-guard.conf"
if [[ ! -f $conf ]]; then
  mkdir -p "$(dirname "$conf")"; cp "$HERE/config.example.conf" "$conf"; ok "created $conf (all lines commented: defaults apply)"
else ok "kept $conf"; fi

if (( services )); then
  "$HERE/bin/lg" watchdog --install >/dev/null && ok "watchdog service (load-guard-watchdog)"
  if (( dashboard )); then
    url=$("$HERE/bin/lg" dashboard --install $tailscale | sed -n 's/^dashboard: //p')
    ok "dashboard service (load-guard-dashboard): ${url:-see lg dashboard --url}"
  fi
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
    warn "the services stop when you log out. To keep them running: sudo loginctl enable-linger $USER"
  fi
fi

echo; "$HERE/bin/lg" status
echo; echo "Done. Next: edit $conf if the defaults do not fit (lg config shows them), and run heavy commands as: lg run -- <command>"
