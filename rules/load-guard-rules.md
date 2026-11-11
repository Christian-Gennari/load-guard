## Machine load (load guard)

This machine is shared by several AI agents. The `lg` tool (load guard) keeps it from running out of memory. If `lg` is not installed on this machine, ignore this section.

- Run every build, test, type-check or browser-test command through it: `lg run -- <command>` (example: `lg run -- npm test -- --include src/app/foo`).
- If `lg` prints "queued", wait for it; never bypass it. Exit code 75 means the job was stopped for low memory or waited too long: wait 2 minutes, run the same command again (at most 2 times), then report it.
- Run the narrowest test target that proves the change. Run a full suite at most once, at the end. Use few parallel test workers.
- Never leave dev servers, watchers or `--watch` modes running.
- Before you start parallel workers or subagents, run `lg admit` and start one only on `ADMIT`. Register each with `lg worker start <name>` and remove it with `lg worker end <name>` when it ends.
