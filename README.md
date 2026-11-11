<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
    <img src="docs/logo-light.svg" alt="load guard" height="72">
  </picture>
</p>

<p align="center"><b>A load-guard skill for AI agents, and a live window for the humans who watch them.</b></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2a78d6" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%C2%B7%20systemd-2a78d6" alt="Linux with systemd">
  <a href="https://agentskills.io"><img src="https://img.shields.io/badge/Agent%20Skills-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Gemini%20%C2%B7%20OpenCode%20%C2%B7%20Cursor-2a78d6" alt="Agent Skills"></a>
</p>

Give your orchestrating agent the job of load guard. Before it starts another worker, it asks; when the machine is busy, it waits. Its builds and tests go through one small command that keeps them inside a budget. And you can watch all of it live: what the agents do, what they decided and why, and how the machine copes.

- **For the agent:** `lg admit` (start or wait, with the reason), `lg worker start <name> "<task>"`, and `lg run -- <cmd>` for every build or test.
- **For you:** one page with the verdict, the agents and their tasks, the running and waiting jobs, and a feed of the agents' decisions. Simple view by default, Detailed view for tuning.
- **Safety nets:** each job runs with memory and CPU limits, and a watchdog stops the newest job when memory runs out.
- **Any agent harness:** Claude Code, Codex, Gemini CLI, OpenCode, Cursor, or a script.

Linux, per user, no root, no dependencies beyond a standard install.

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
lg admit --for api-refactor                          # may another agent start? (ADMIT or WAIT + reason)
lg worker start api-refactor "Move the payments API"  # register it, with its task, for the dashboard
lg run -- npm test                                    # run a build or test inside the limits
lg worker end api-refactor                            # it is done
lg status                                             # the same verdict as the dashboard, in the terminal
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
