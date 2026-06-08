# ccman

A terminal TUI for managing Claude Code sessions and projects.

## Features

- Lists all Claude Code projects with their sessions underneath
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

```bash
ln -s "$PWD/ccman.py" ~/.local/bin/ccman
```

`ccman.py` is directly executable (`#!/usr/bin/env python3`). No build step needed.

## Usage

```
ccman
```

## Visual Layout

```
──── 2 projects─7 sessions─2 open ────   ← header (live counts)
 ▼ ~/workbench/myapp [main]  (3)         ← project: expanded, git branch, session count
 ⟳ Add OAuth flow                        ← running — Claude is thinking  (blue)
 ● Fix login bug                         ← running — waiting for input   (magenta)
 ✓ Marked session                        ← marked for batch open         (green)
   Untitled session
 ▶ ~/workbench/notes  (1)                ← project: collapsed
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
| `n` | New session in current project |
| `N` | New project (prompts for path, Tab autocompletes directories) |
| `d` | Delete selected session; on a missing-path project, delete the project record |
| `K` | Kill tmux pane of the selected running session |
| `Q` | Kill all running sessions |
| `e` | Rename session (set custom title) |
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
| `●` | Waiting for your input — blinks (magenta) |
| `‼` | Waiting for tool-permission approval — blinks (yellow) |
| `○` | Idle — waiting more than 5 minutes (dim) |
| `✓` | Session is marked for batch open (green) |
| `⚠` | Project directory no longer exists on disk — `d` to delete the record |
| `▼` / `▶` | Project is expanded / collapsed |

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

Custom titles are stored in `~/.config/ccman/titles.json`.
