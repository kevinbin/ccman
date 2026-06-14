# ccman

A simple, lightweight, zero-dependency terminal TUI for managing many Claude Code agents — every session and project in one pane.

## Demo

https://github.com/user-attachments/assets/56922f30-0b6b-45f8-a01f-68e7e98bd5c9

## Features

- Lists all Claude Code projects with their sessions underneath
- Groups scheduled-task (routine) runs into their own top-level sections by name, each run tagged with its execution date
- Recognises git worktrees as `⑂ <main-repo> · <branch>`; real paths are resolved from session history so worktree projects open correctly
- Create a git worktree and open a session in it with one key (`w`); open lazygit for any project with `t`
- Every session launches with Remote Control enabled, so you can drive it from the Claude Code app
- Renaming (`e`) syncs with Claude Code's own name — running and stopped sessions alike (see [Session names](#session-storage))
- Live per-session state (⟳ responding · ● waiting · ‼ approval · ○ idle) sourced from Claude Code hooks; sessions that need you blink
- Detects running panes on startup — state survives ccman restarts
- Batch open: mark multiple sessions with Space, then open all at once
- Live search / filter across all sessions and projects
- Alternating horizontal/vertical splits keep the workspace evenly divided
- Runs as a narrow left sidebar inside tmux, or standalone

## Requirements

- Python 3.8+ (stdlib only — no extra packages)
- tmux (for sidebar mode and pane management)
- Claude Code CLI (`claude`)

## Installation

One command, straight from GitHub — no clone, no pip, no build:

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/kevinbin/ccman/master/ccman.py -o ~/.local/bin/ccman
chmod +x ~/.local/bin/ccman
```

Make sure `~/.local/bin` is on your `$PATH`. `ccman.py` is a single stdlib-only script (`#!/usr/bin/env python3`) — nothing else to install.

Prefer to track updates with git? Clone and symlink instead:

```bash
git clone https://github.com/kevinbin/ccman
ln -s "$PWD/ccman/ccman.py" ~/.local/bin/ccman
```

## Usage

```
ccman
```

## Visual Layout

```
─── Project:3--Routine:1--Session:10 ───  ← header (live counts)
 ▼ ~/workbench/myapp [main]  (3)         ← project: expanded, git branch, session count
 ⟳ Add OAuth flow                        ← running — Claude is responding (blue)
 ● Fix login bug                         ← running — waiting for input   (yellow)
 ✓ Marked session                        ← marked for batch open         (green)
   Untitled session
 ⑂ myapp · feature/oauth  (2)            ← git worktree of myapp (dim)
 ▶ ~/workbench/notes  (1)                ← project: collapsed
 ▼ ◷ nightly-backup  (4)                 ← routine group: scheduled-task runs by name (blue)
   Back up databases          06-09 02:00  ← each run tagged with its exec date
   Back up databases          06-08 02:00
─────────────────────────────────────
 ?·help  q·quit                          ← key bar (or /query▌  (N) in search)
```

## Key Bindings

### Navigation

| Key | Action |
|-----|--------|
| `j` / `k` | Move cursor down / up |
| `g` / `G` | Jump to top / bottom |
| `Space` on project | Expand / collapse |
| `Space` on session | Mark / unmark for batch open |
| `Esc` | Clear all marks |

### Actions

| Key | Action |
|-----|--------|
| `Enter` / `o` | Open session (or switch focus if already running); opens all marked sessions if any are marked |
| `n` | New session in current project (not available on routine groups) |
| `N` | New project (prompts for path, Tab autocompletes directories) |
| `w` | New git worktree + session for the current project (`.claude/worktrees/<name>`, blank = random; branches off origin's default branch, falling back to local `HEAD`) |
| `t` | Open lazygit in a new pane for the current project |
| `d` | Delete selected session; on a missing-path project, delete the project record |
| `K` | Kill tmux pane of the selected running session |
| `Q` | Kill all running sessions |
| `e` | Rename session — syncs Claude Code's name (running → `/rename`; stopped → writes the records directly) |
| `i` | Show full info popup (ID, path, timestamps, turn count) |
| `?` | In-app help |
| `q` | Quit |

### Search

| Key | Action |
|-----|--------|
| `/` | Enter search mode |
| type | Filter and highlight matching sessions |
| `Tab` | Jump to next match |
| `Shift+Tab` | Jump to previous match |
| `Enter` | Open selected match and exit search |
| `Esc` | Exit search, restore full list |

### Mouse

| Action | Effect |
|--------|--------|
| Click | Select item |
| Scroll wheel | Navigate list (3 rows per tick) |

## Indicators

| Symbol | Meaning |
|--------|---------|
| `⟳` | Claude is responding (blue) |
| `●` | Waiting for your input — blinks (yellow) |
| `‼` | Waiting for tool-permission approval — blinks (yellow) |
| `○` | Idle — waiting more than 5 minutes (green) |
| `✓` | Session is marked for batch open (green) |
| `⚠` | Project directory no longer exists on disk — `d` to delete the record |
| `▼` / `▶` | Project is expanded / collapsed |
| `◷` | Routine group — scheduled-task runs grouped by name (blue); each run shows its exec date on the right |
| `⑂` | Git worktree — shown as `⑂ <main-repo> · <branch>` (dim) |

State comes from Claude Code hooks (see below), not pane-scraping. Sessions without
hooks configured still appear — shown as `●` while running.

## Tmux Pane Splitting

Each new session opened from ccman creates a new tmux pane. Splits alternate between horizontal and vertical to keep the workspace evenly divided:

- 1st session: splits the main area horizontally
- 2nd: splits the largest pane vertically
- 3rd: splits the largest pane horizontally → 2×2 grid
- And so on…

## Accurate Status via Hooks

ccman reads each session's state from files written by Claude Code hooks, instead of
guessing from terminal output. Wire the hooks once in `~/.claude/settings.json`, pointing
every event at `ccman hook` (use the absolute path to your `ccman.py`):

```json
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "PreToolUse":       [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "PermissionRequest":[{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "Notification":     [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "/path/to/ccman.py hook"}]}]
  }
}
```

`ccman hook` reads the event JSON on stdin and writes `~/.config/ccman/state/<session_id>`:

| Hook event | State written |
|-----------|---------------|
| `UserPromptSubmit`, `PreToolUse` | `busy` → `⟳` |
| `PermissionRequest`, `Notification` (`notification_type` = permission) | `approval` → `‼` (blinks) |
| `Stop`, `SessionStart`, other `Notification` | `waiting` → `●` (blinks) |
| `SessionEnd` | file removed |

A `waiting` session whose state file is untouched for over 5 minutes is shown as idle (`○`).
Sessions needing your attention (`●` / `‼`) pulse between bright and dim so they stand out.

## Session Storage

Sessions are read from `~/.claude/projects/`. Each project directory corresponds to a filesystem path (slashes encoded as dashes), containing `.jsonl` files — one per session.

A session title is taken from its first real prompt; injected scaffolding (the local-command caveat, `<command-name>` wrappers, skill boilerplate) is skipped, so sessions started with a slash command like `/clear` still show what you actually asked. Sessions whose first message is a scheduled-task run are pulled out of the directory they ran in and regrouped under a top-level routine section by task name; each run resumes in its original working directory.

### Session names

Renaming a session with `e` keeps ccman and Claude Code in sync, because it writes the
same `custom-title` + `agent-name` records that Claude Code's own `/rename` produces:

- **Running session** — ccman sends `/rename <name>` to its tmux pane.
- **Stopped session** — ccman appends those records straight to the session's `.jsonl`, byte-for-byte as Claude Code writes them. No pane is opened and no model turn is spent; the name shows up in Claude Code's `/resume` picker and the app.

ccman reads this name back (it takes priority over the auto-generated title), so a `/rename`
you run inside Claude Code also shows up in ccman. Legacy ccman-only titles in
`~/.config/ccman/titles.json` are still honoured as an override.

## License

[MIT](LICENSE) © 2026 kevinbin
