# ccman

A terminal TUI for managing Claude Code sessions and projects.

## Features

- Lists all Claude Code projects with their sessions underneath
- Shows running sessions (● indicator) and lets you jump to or kill their tmux panes
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
╔══════════════════════════════════════════╗
║  ccman  [2 projects · 7 sessions]  F:here ║   ← header
╠══════════════════════════════════════════╣
║  ~/workbench/myapp                [main] ║   ← project (git branch)
║  ●  Fix login bug           2 h ago      ║   ← session running in a pane
║  →  Add OAuth flow          1 d ago      ║   ← last opened by ccman
║  ✎  Custom title            3 d ago      ║   ← session with custom title
║     Untitled session        5 d ago      ║
║                                          ║
║  ~/workbench/notes                       ║
║     Meeting notes           1 w ago      ║
╠══════════════════════════════════════════╣
║  Fix login bug · 8eaf2b1c-…             ║   ← preview (session ID)
╠══════════════════════════════════════════╣
║  /·find  n·new  Enter·open  K·kill  q   ║   ← key bar
╚══════════════════════════════════════════╝
```

## Key Bindings

### Navigation

| Key | Action |
|-----|--------|
| `j` / `k` / `↑` / `↓` | Move cursor |
| `g` / `G` | Jump to top / bottom |
| `Space` / `l` / `→` | Expand project |
| `h` / `←` | Collapse project / move to parent |
| `1` – `9` | Jump to nth project |

### Actions

| Key | Action |
|-----|--------|
| `Enter` / `o` | Open session (resume with `claude -r`) |
| `n` | New session in current project |
| `N` | New project (prompts for path) |
| `d` | Delete session (asks y/N) |
| `K` | Kill tmux pane of running session (asks y/N) |
| `r` | Reload all projects |
| `/` | Search sessions and projects |
| `e` | Rename session (custom title) |
| `E` | Clear custom title (restore AI-generated title) |
| `y` | Copy session ID to clipboard |
| `i` | Show full info popup |
| `F` | Toggle focus — stay in ccman or jump to new pane |
| `q` | Quit |

### Mouse

| Action | Effect |
|--------|--------|
| Click once | Select item |
| Click again / double-click | Open session |
| Scroll wheel | Navigate list |

## Indicators

| Symbol | Meaning |
|--------|---------|
| `●` | Session is running in a tmux pane — `Enter` switches focus to it |
| `→` | Last session opened by ccman |
| `✎` | Session has a custom title |

## Tmux Pane Splitting

Each new session opened from ccman creates a new tmux pane. Splits alternate between horizontal and vertical to keep the workspace evenly divided:

- 1st session: splits the main area horizontally
- 2nd: splits the largest pane vertically
- 3rd: splits the largest pane horizontally → 2×2 grid
- And so on…

The `F` key controls whether focus returns to ccman after opening a session (`here`) or jumps to the new pane (`pane`).

## Session Storage

Sessions are read from `~/.claude/projects/`. Each project directory corresponds to a filesystem path (slashes encoded as dashes), containing `.jsonl` files — one per session.

Custom titles are stored in `~/.config/ccman/titles.json`.
