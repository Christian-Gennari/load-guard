<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
    <img src="docs/logo-light.svg" alt="load guard" height="72">
  </picture>
</p>

<p align="center"><b>Run many coding agents on one Linux machine without running out of memory.</b></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2a78d6" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%C2%B7%20systemd-2a78d6" alt="Linux with systemd">
  <a href="https://agentskills.io"><img src="https://img.shields.io/badge/Agent%20Skills-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Gemini%20%C2%B7%20OpenCode%20%C2%B7%20Cursor-2a78d6" alt="Agent Skills"></a>
</p>

Parallel AI agents each like to start a full build or test suite. Three at once, and the machine swaps or the OOM killer strikes. **load guard** puts them in line:

- **`lg run -- <cmd>`** waits for a free slot and enough memory, then runs the command in a systemd scope with memory, swap and CPU limits.
- **`lg admit`** tells an orchestrator if it may start another agent.
- **A watchdog** stops the newest job when memory runs critically low (exit 75: retry later).
- **A live dashboard** shows what runs, what waits and why.
- **Any agent harness** – a skill and short rules teach Claude Code, Codex, Gemini CLI, OpenCode and Cursor to use it.

Per user, no root, no dependencies beyond a standard Linux install.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/dashboard-light.png">
  <img src="docs/dashboard-dark.png" alt="The load guard dashboard">
</picture>

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Christian-Gennari/load-guard/main/install.sh | bash -s -- --rules
```

Run the same command again to update. It clones to `~/.local/share/load-guard` (set `LG_DIR` to change it), puts `lg` on PATH, links the skill for your agents, and starts the watchdog and the dashboard on <http://127.0.0.1:8377/>.

| Option | Effect |
|---|---|
| `--rules` | Add the load rules to the global instruction file of each agent harness found. Recommended. |
| `--tailscale` | Publish the dashboard to your tailnet over HTTPS. |
| `--no-dashboard` / `--no-services` / `--no-skill-links` | Skip that part. |
| `--uninstall` | Remove everything the installer added. Your config and state stay. |

**Needs:** Linux with systemd user services and cgroup v2 (current Ubuntu, Debian, Fedora, Arch; WSL2 with systemd), Bash 4, Python 3.8. On a server, run `sudo loginctl enable-linger $USER` so the services keep running after you log out.

**Try the dashboard only:** `python3 dashboard/server.py --demo` shows synthetic data.

## Use

```bash
lg status                        # memory, load, jobs, workers, ADMIT/WAIT
lg run -- npm test               # run a heavy command inside the limits
lg run --mem 6G -- cargo build   # with a larger memory limit
lg admit                         # may another agent start?
lg worker start|end <name>       # register an agent
lg report 24                     # numbers to tune the limits
```

## Agent harnesses

`lg` is a plain shell command, so every agent shares the same limits. The installer sets up two things:

- **The skill** (`SKILL.md`, [Agent Skills](https://agentskills.io) format), linked into `~/.agents/skills` (Codex, Gemini CLI, Cursor, OpenCode) and `~/.claude/skills` (Claude Code). Agents load it when they fan out work.
- **The rules** (`--rules`), ten always-on lines, so a worker that never loads the skill still uses `lg run`. They go into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, and `~/.config/opencode/AGENTS.md` if it exists (otherwise OpenCode reads `CLAUDE.md`). For Cursor or any other tool, paste the output of `lg rules`.

## Configuration

Defaults come from the machine's memory and cores; `lg config` shows them. Override them in `~/.config/load-guard.conf` (every key: [`config.example.conf`](config.example.conf)). For example, to let only one full test suite run at a time:

```bash
LG_EXCLUSIVE_MATCH='*dotnet test*'
LG_EXCLUSIVE_UNLESS='*--filter*'
```

The dashboard's "Last 24 hours" panel says when the limits are too tight or too loose. It has no login: keep it on `127.0.0.1` or use `--tailscale`.

## License

MIT. Bundled fonts: [Inter](dashboard/fonts/LICENSE-Inter.txt) and [JetBrains Mono](dashboard/fonts/LICENSE-JetBrainsMono.txt), SIL Open Font License 1.1.
