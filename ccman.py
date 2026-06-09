#!/usr/bin/env python3
import curses
import glob
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_BIN = "claude --allow-dangerously-skip-permissions"
CCMAN_CONFIG_DIR = Path.home() / ".config" / "ccman"
TITLES_FILE = CCMAN_CONFIG_DIR / "titles.json"
STATE_DIR = CCMAN_CONFIG_DIR / "state"   # hook 写入的会话状态: <session_id> → "busy"|"waiting"
IDLE_AFTER = 300   # "waiting" 持续超过这么多秒视为 idle（空闲）

# ─── data ─────────────────────────────────────────────────────────────────────

def _decode_path(dir_name: str) -> str:
    return dir_name.replace("-", "/")


def _display_name(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    parts = path.lstrip("~/").split("/")
    return ("~/" + "/".join(parts[-2:])) if len(parts) > 2 else path


def _git_info(path: str) -> dict:
    """Return {'branch': str|None, 'worktree_main': str|None} for path.

    worktree_main is the main repo's basename when path is a linked worktree,
    else None. A single git call yields both the branch and the git-dir.
    """
    try:
        r = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD", "--absolute-git-dir"],
            capture_output=True, text=True, timeout=1,
        )
        if r.returncode != 0:
            return {"branch": None, "worktree_main": None}
        lines = r.stdout.splitlines()
        branch = lines[0].strip() if lines else ""
        gitdir = lines[1].strip() if len(lines) > 1 else ""
        main = None
        if "/.git/worktrees/" in gitdir:
            main = os.path.basename(gitdir.split("/.git/worktrees/")[0])
        return {"branch": branch or None, "worktree_main": main}
    except Exception:
        return {"branch": None, "worktree_main": None}


def read_session_states() -> dict:
    """Return {session_id: (state, mtime)} from hook-written state files."""
    out: dict[str, tuple[str, float]] = {}
    try:
        for f in STATE_DIR.iterdir():
            try:
                out[f.name] = (f.read_text().strip(), f.stat().st_mtime)
            except OSError:
                pass
    except (FileNotFoundError, NotADirectoryError):
        pass
    return out


def load_custom_titles() -> dict:
    try:
        return json.loads(TITLES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_custom_title(session_id: str, title: str):
    CCMAN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    titles = load_custom_titles()
    if title:
        titles[session_id] = title
    else:
        titles.pop(session_id, None)
    TITLES_FILE.write_text(json.dumps(titles, indent=2, ensure_ascii=False))


def _session_info(jsonl: Path, custom_titles: dict | None = None) -> dict:
    ai_title = fallback = first_ts = None
    try:
        with open(jsonl, "r", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if first_ts is None and "timestamp" in d:
                    first_ts = d["timestamp"]
                if d.get("type") == "ai-title":
                    ai_title = d.get("aiTitle", "")
                    break
                if fallback is None and d.get("type") == "user":
                    c = d.get("message", {}).get("content", "")
                    if isinstance(c, list):
                        for blk in c:
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                fallback = blk["text"]
                                break
                    elif isinstance(c, str):
                        fallback = c
    except (IOError, OSError):
        pass

    sid = jsonl.stem
    auto_title = (ai_title or fallback or "(no title)").split("\n")[0].strip()
    custom = (custom_titles or {}).get(sid)
    title = custom if custom else auto_title

    st = jsonl.stat()
    mtime = st.st_mtime
    if first_ts:
        try:
            birthtime = datetime.fromisoformat(first_ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            birthtime = getattr(st, "st_birthtime", mtime)
    else:
        birthtime = getattr(st, "st_birthtime", mtime)

    dt, now = datetime.fromtimestamp(birthtime), datetime.now()
    if dt.date() == now.date():
        ts = dt.strftime("%H:%M")
    elif (now - dt).days < 7:
        ts = dt.strftime("%a")
    else:
        ts = dt.strftime("%m/%d")
    return {"id": sid, "file": jsonl, "title": title, "auto_title": auto_title,
            "custom": bool(custom), "time": ts, "mtime": mtime, "birthtime": birthtime}


def _read_cwd(jsonl: Path) -> str | None:
    """Return the real working directory recorded in a session's jsonl.

    The project dir name encodes the path lossily (/, ., - all collapse to -),
    so the cwd field is the only reliable source of the true path.
    """
    try:
        with open(jsonl, "r", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                cwd = d.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
    except (IOError, OSError):
        pass
    return None


def load_projects() -> list:
    if not CLAUDE_PROJECTS_DIR.exists():
        return []
    custom_titles = load_custom_titles()
    projects = []
    for d in CLAUDE_PROJECTS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        sessions = []
        for f in d.glob("*.jsonl"):
            try:
                sessions.append(_session_info(f, custom_titles))
            except Exception:
                pass
        sessions.sort(key=lambda s: s["birthtime"], reverse=True)
        path = next((c for s in sessions if (c := _read_cwd(s["file"]))), None) or _decode_path(d.name)
        info = _git_info(path)
        projects.append({
            "dir_name": d.name,
            "path": path,
            "display": _display_name(path),
            "branch": info["branch"],
            "worktree_main": info["worktree_main"],
            "sessions": sessions,
            "expanded": True,
            "last_active": sessions[0]["mtime"] if sessions else 0,
            "path_exists": os.path.isdir(path),
        })
    return projects

# ─── tmux ─────────────────────────────────────────────────────────────────────

def _in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux_my_pane_id() -> str:
    """Return the tmux pane ID of the ccman process itself.

    $TMUX_PANE is set by tmux in each pane's environment and names the pane
    this process runs in. We must NOT rely on `display-message -p '#{pane_id}'`,
    which returns the *active* pane — wrong whenever focus is on another pane
    (e.g. a session pane), which would resize the wrong pane.
    """
    pane = os.environ.get("TMUX_PANE", "")
    if pane:
        return pane
    r = subprocess.run(["tmux", "display-message", "-p", "#{pane_id}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _tmux_session_panes(my_id: str) -> list[dict]:
    """Return all panes in this window except the ccman pane."""
    r = subprocess.run(
        ["tmux", "list-panes", "-F", "#{pane_id} #{pane_width} #{pane_height}"],
        capture_output=True, text=True,
    )
    panes = []
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] != my_id:
            panes.append({"id": parts[0], "w": int(parts[1]), "h": int(parts[2])})
    return panes


def count_session_panes() -> int:
    """Count open session panes (non-ccman) in the current tmux window."""
    if not _in_tmux():
        return 0
    return len(_tmux_session_panes(_tmux_my_pane_id()))


_UUID_RE = re.compile(
    r"(?:^|/)claude\b.*?(?:\s-r|\s--resume)\s+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def get_running_sessions() -> dict[str, str]:
    """Return {session_id: pane_id} for every 'claude -r <id>' running in tmux.

    Walks the process tree upward from each matching claude process until it
    finds the tmux pane that owns it.
    """
    if not _in_tmux():
        return {}
    try:
        # All processes: pid  ppid  full-args
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,args="],
                           capture_output=True, text=True, timeout=3)

        pid_to_parent: dict[str, str] = {}
        pid_to_session: dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, ppid = parts[0], parts[1]
            pid_to_parent[pid] = ppid
            if len(parts) == 3:
                m = _UUID_RE.search(parts[2])
                if m:
                    pid_to_session[pid] = m.group(1)

        if not pid_to_session:
            return {}

        # Pane pids from tmux (across all sessions)
        r2 = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_by_pid: dict[str, str] = {}
        for line in r2.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                pane_by_pid[parts[1]] = parts[0]

        # Walk up the tree: claude → shell → tmux pane
        result: dict[str, str] = {}
        for claude_pid, session_id in pid_to_session.items():
            pid = claude_pid
            for _ in range(8):
                if pid in pane_by_pid:
                    result[session_id] = pane_by_pid[pid]
                    break
                pid = pid_to_parent.get(pid, "")
                if not pid or pid in ("0", "1"):
                    break
        return result
    except Exception:
        return {}


def _tmux_open(cmd: str, cwd: str | None = None) -> tuple[str | None, str | None]:
    """Split the largest pane along its longer axis, evenly dividing the space.

    Returns (error_message, new_pane_id). On success error_message is None.
    """
    my_id = _tmux_my_pane_id()
    if not my_id:
        return "tmux display-message failed", None

    session_panes = _tmux_session_panes(my_id)

    first_split = not session_panes

    if session_panes:
        biggest = max(session_panes, key=lambda p: p["w"] * p["h"])
        target_id = biggest["id"]
        direction = "-h" if biggest["w"] >= biggest["h"] else "-v"
    else:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#{window_width} #{window_height}"],
            capture_output=True, text=True,
        )
        try:
            ww, wh = map(int, r.stdout.strip().split())
        except Exception:
            ww, wh = 200, 50
        target_id = None
        direction = "-h" if ww >= wh * 2 else "-v"

    args = ["tmux", "split-window", direction, "-P", "-F", "#{pane_id}"]
    if target_id:
        args += ["-t", target_id]
    if cwd and os.path.isdir(cwd):
        args += ["-c", cwd]
    args.append(cmd)

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        return result.stderr.strip(), None

    new_pane_id = result.stdout.strip() or None

    # On the first split, shrink ccman to 20% so the session pane gets 80%
    if first_split:
        flag = "-x" if direction == "-h" else "-y"
        subprocess.run(["tmux", "resize-pane", "-t", my_id, flag, "20%"],
                       capture_output=True)

    subprocess.run(["tmux", "select-pane", "-t", my_id], capture_output=True)
    return None, new_pane_id


def open_session(proj: dict, sess: dict) -> tuple[str, str | None]:
    """Returns (status_message, new_pane_id)."""
    cmd = f"{CLAUDE_BIN} -r {sess['id']}"
    if _in_tmux():
        err, pane_id = _tmux_open(cmd, proj["path"])
        return (f"Error: {err}" if err else f"Resumed → {sess['title'][:35]}"), pane_id
    return f"Not in tmux. Run: cd {proj['path']!r} && {cmd}", None


def new_session(proj: dict) -> tuple[str, str | None]:
    """Returns (status_message, new_pane_id)."""
    if _in_tmux():
        err, pane_id = _tmux_open(CLAUDE_BIN, proj["path"])
        return (f"Error: {err}" if err else f"New session → {proj['display']}"), pane_id
    return f"Not in tmux. Run: cd {proj['path']!r} && {CLAUDE_BIN}", None

# ─── UI ───────────────────────────────────────────────────────────────────────

C_BORDER = 1
C_PROJ   = 2
C_SESS   = 3
C_SEL    = 4
C_DIM    = 5
C_OK     = 6
C_MARK   = 7   # green – "✓" marked-for-open indicator
C_MATCH  = 9   # red      – search match highlight
C_THINK  = 10  # blue     – "⟳" thinking indicator
C_WARN   = 11  # yellow   – "‼" awaiting-permission marker


class App:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.projects: list = []
        self.all_flat: list = []
        self.flat: list = []
        self.running_sessions: dict[str, str] = {}   # session_id → pane_id
        self.session_states: dict[str, tuple[str, float]] = {}  # session_id → (state, mtime)
        self._pending_panes: dict[str, tuple[str, float]] = {}  # pane_id → (proj_dir_name, created_at)
        self.marked: set[str] = set()                # session IDs marked for batch open
        self._prev_other = 0                          # 非 ccman pane 数；会话关闭后据此把自己缩回 20%
        self.sel: int = 0
        self.offset: int = 0
        self.status: str = ""
        self.search_mode: bool = False
        self.search_q: str = ""
        self.search_match_indices: list[int] = []

        self._init_colors()
        self._init_mouse()
        self.reload()

    # ── init ──────────────────────────────────────────────────────────────

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        bg = -1
        curses.init_pair(C_BORDER, curses.COLOR_CYAN,    bg)
        curses.init_pair(C_PROJ,   curses.COLOR_CYAN,   bg)
        curses.init_pair(C_SESS,   curses.COLOR_WHITE,  bg)
        curses.init_pair(C_SEL,    curses.COLOR_BLACK,  curses.COLOR_YELLOW)
        curses.init_pair(C_DIM,    curses.COLOR_YELLOW, bg)
        curses.init_pair(C_OK,     curses.COLOR_GREEN,  bg)
        curses.init_pair(C_MARK,   curses.COLOR_GREEN,    bg)
        curses.init_pair(C_MATCH,  curses.COLOR_YELLOW,   bg)
        curses.init_pair(C_THINK,  curses.COLOR_BLUE,     bg)
        curses.init_pair(C_WARN,   curses.COLOR_YELLOW,   bg)

    def _init_mouse(self):
        try:
            curses.mousemask(
                curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION
            )
        except Exception:
            pass  # some terminals don't support mouse – that's fine

    # ── data ──────────────────────────────────────────────────────────────

    def reload(self):
        sel_id = self._cur_id()
        self.projects = load_projects()
        detected = get_running_sessions()
        # Read session IDs stored in tmux pane options (survives ccman restart)
        r_opt = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}=#{@ccman_sid}"],
            capture_output=True, text=True,
        )
        for line in r_opt.stdout.strip().splitlines():
            eq = line.find("=")
            if eq < 0:
                continue
            pane_id, sid = line[:eq], line[eq + 1:]
            if sid and sid not in detected:
                detected[sid] = pane_id
        # Preserve manually-tracked associations for panes still alive
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                           capture_output=True, text=True)
        alive_panes = set(r.stdout.split())
        for sid, pane_id in self.running_sessions.items():
            if pane_id in alive_panes and sid not in detected:
                detected[sid] = pane_id
        self.running_sessions = detected
        # Write session IDs into tmux pane options so they survive ccman restart
        for sid, pane_id in self.running_sessions.items():
            subprocess.run(
                ["tmux", "set-option", "-p", "-t", pane_id, "@ccman_sid", sid],
                capture_output=True,
            )
        self._rebuild_all()
        self._associate_pending_panes()
        self._restore_sel(sel_id)
        n = sum(len(p["sessions"]) for p in self.projects)
        panes = count_session_panes()
        run = len(self.running_sessions)
        pane_s = f" · {panes} pane{'s' if panes != 1 else ''}" if panes else ""
        run_s  = f" ({run} running)" if run else ""
        self.status = f"{len(self.projects)} projects · {n} sessions{pane_s}{run_s}"

    def _associate_pending_panes(self):
        if not self._pending_panes:
            return
        # Remove panes that are no longer alive
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                           capture_output=True, text=True)
        alive = set(r.stdout.split())
        for pid in [p for p in list(self._pending_panes) if p not in alive]:
            del self._pending_panes[pid]
        if not self._pending_panes:
            return
        proj_map = {p["dir_name"]: p for p in self.projects}
        already_running_panes = set(self.running_sessions.values())
        # Process oldest panes first so each matches the earliest available session
        sorted_pending = sorted(self._pending_panes.items(), key=lambda x: x[1][1])
        for pane_id, (dir_name, created_at) in sorted_pending:
            if pane_id in already_running_panes:
                del self._pending_panes[pane_id]
                continue
            proj = proj_map.get(dir_name)
            if not proj:
                continue
            candidates = [s for s in proj["sessions"]
                          if s["birthtime"] >= created_at - 1
                          and s["id"] not in self.running_sessions]
            if candidates:
                best = min(candidates, key=lambda s: s["birthtime"])
                self.running_sessions[best["id"]] = pane_id
                del self._pending_panes[pane_id]

    def _fast_refresh(self):
        """200ms fast path: refresh hook-written states and drop dead panes."""
        self.session_states = read_session_states()
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                           capture_output=True, text=True)
        alive = set(r.stdout.split())
        dead = [sid for sid, pid in self.running_sessions.items() if pid not in alive]
        for sid in dead:
            self.running_sessions.pop(sid, None)
        if dead:
            self._rebuild_all()
        # 会话 pane 关闭后，tmux 会把空间塞给 ccman；缩回 20% 维持 sidebar 宽度
        other = count_session_panes()
        if 0 < other < self._prev_other:
            self._keep_width()
        self._prev_other = other

    def _keep_width(self):
        """Re-shrink ccman to 20% after a session pane closed, so it doesn't
        expand into the freed space. No-op when ccman is the sole pane."""
        my_id = _tmux_my_pane_id()
        if not my_id:
            return
        r = subprocess.run(["tmux", "display-message", "-p", "#{window_width} #{window_height}"],
                           capture_output=True, text=True)
        try:
            ww, wh = map(int, r.stdout.split())
        except ValueError:
            return
        flag = "-x" if ww >= wh * 2 else "-y"
        subprocess.run(["tmux", "resize-pane", "-t", my_id, flag, "20%"], capture_output=True)

    def _rebuild_all(self):
        self.all_flat = []
        for p in self.projects:
            self.all_flat.append(("project", p, None))
            if p["expanded"]:
                for s in p["sessions"]:
                    self.all_flat.append(("session", p, s))
        self._apply_filter()

    def _apply_filter(self):
        self.flat = self.all_flat
        q = self.search_q.lower()
        if q:
            self.search_match_indices = [
                i for i, (kind, proj, sess) in enumerate(self.flat)
                if (kind == "session" and q in sess["title"].lower()) or
                   (kind == "project" and (q in proj["display"].lower() or q in proj["path"].lower()))
            ]
        else:
            self.search_match_indices = []
        self.sel = max(0, min(self.sel, max(0, len(self.flat) - 1)))

    def _cur_id(self) -> str | None:
        cur = self._cur()
        if not cur:
            return None
        return cur[2]["id"] if cur[0] == "session" else cur[1]["dir_name"]

    def _restore_sel(self, sel_id: str | None):
        if not sel_id:
            return
        for i, item in enumerate(self.flat):
            iid = item[2]["id"] if item[0] == "session" else item[1]["dir_name"]
            if iid == sel_id:
                self.sel = i
                return

    # ── navigation ────────────────────────────────────────────────────────

    def _content_h(self) -> int:
        h, _ = self.scr.getmaxyx()
        return max(1, h - 2)   # header + keys

    def _goto(self, idx: int):
        ch = self._content_h()
        self.sel = max(0, min(idx, len(self.flat) - 1))
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + ch:
            self.offset = self.sel - ch + 1

    def _cur(self):
        return self.flat[self.sel] if self.flat else None

    # ── drawing ───────────────────────────────────────────────────────────

    def draw(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        blink = int(time.time() * 1.5) % 2 == 0   # ~0.67s 脉动相位，用于 waiting/approval

        # header — live stats
        n_sess = sum(len(p["sessions"]) for p in self.projects)
        n_run = len(self.running_sessions)
        open_s = f"─{n_run} open" if n_run else ""
        self._hline(0, w, f" {len(self.projects)} projects─{n_sess} sessions{open_s} ", C_OK)

        # list
        ch = self._content_h()
        has_scroll = len(self.flat) > ch
        col_w = w - 2 if has_scroll else w  # reserve 1 col for scrollbar

        match_set = set(self.search_match_indices) if self.search_mode and self.search_q else set()

        for i, (kind, proj, sess) in enumerate(self.flat[self.offset: self.offset + ch]):
            y, idx = i + 1, i + self.offset
            sel = idx == self.sel
            is_match = idx in match_set

            if kind == "project":
                missing = not proj.get("path_exists", True)
                wt = proj.get("worktree_main")
                arrow = "⚠" if missing else ("▼" if proj["expanded"] else "▶")
                branch = f" [{proj['branch']}]" if proj.get("branch") else ""
                n = len(proj["sessions"])
                if wt and not missing:
                    label = f" ⑂ {wt} · {proj['branch'] or '?'}  ({n})"
                else:
                    label = f" {arrow} {proj['display']}{branch}  ({n})"
                if sel:
                    attr = curses.color_pair(C_SEL) | curses.A_BOLD
                elif is_match:
                    attr = curses.color_pair(C_MATCH) | curses.A_BOLD
                elif missing or wt:
                    attr = curses.color_pair(C_DIM) | curses.A_BOLD
                else:
                    attr = curses.color_pair(C_PROJ) | curses.A_BOLD
                self._row(y, col_w, label, attr)
                # draw branch in a different colour when not selected
                if not sel and not missing and not wt and not match_set and branch and proj.get("branch"):
                    bx = 3 + len(proj["display"]) + 1   # " ▼ <display>" + space
                    try:
                        self.scr.addstr(y, bx, branch, curses.color_pair(C_DIM) | curses.A_BOLD)
                    except curses.error:
                        pass
            else:
                is_running = sess["id"] in self.running_sessions
                is_marked  = sess["id"] in self.marked
                if is_running:
                    st, mt = self.session_states.get(sess["id"], ("", 0.0))
                    pulse = curses.A_BOLD if blink else curses.A_DIM
                    if st == "busy":
                        prefix, pfx_attr = "⟳ ", curses.color_pair(C_THINK) | curses.A_BOLD
                    elif st == "approval":
                        prefix, pfx_attr = "‼ ", curses.color_pair(C_WARN) | pulse
                    elif st == "waiting" and time.time() - mt > IDLE_AFTER:
                        prefix, pfx_attr = "○ ", curses.color_pair(C_MARK) | curses.A_BOLD
                    elif st == "waiting":
                        prefix, pfx_attr = "● ", curses.color_pair(C_WARN) | pulse
                    else:
                        prefix, pfx_attr = "● ", curses.color_pair(C_WARN) | curses.A_BOLD
                elif is_marked:
                    prefix, pfx_attr = "✓ ", curses.color_pair(C_MARK) | curses.A_BOLD
                else:
                    prefix, pfx_attr = "  ", 0

                label = f"{prefix}{sess['title'][:col_w - len(prefix) - 1]}"
                if sel:
                    row_attr = curses.color_pair(C_SEL)
                elif is_match:
                    row_attr = curses.color_pair(C_MATCH) | curses.A_BOLD
                else:
                    row_attr = curses.color_pair(C_SESS)
                self._row(y, col_w, label, row_attr)

                # Overlay the colored prefix on top
                if prefix.strip():
                    try:
                        self.scr.addstr(y, 0, prefix, pfx_attr)
                    except curses.error:
                        pass

        # scrollbar
        if has_scroll:
            self._draw_scrollbar(ch, w)

        # key bar / search bar — adaptive width
        if self.search_mode:
            n_matches = len(self.search_match_indices)
            bar = f" /{self.search_q}▌  ({n_matches})"
            try:
                self.scr.addstr(h - 1, 0, bar.ljust(w - 1)[:w - 1], curses.color_pair(C_SEL))
            except curses.error:
                pass
        else:
            try:
                self.scr.addstr(h - 1, 0, " ?·help  q·quit"[:w - 1], curses.color_pair(C_DIM))
            except curses.error:
                pass

        self.scr.refresh()

    def _draw_scrollbar(self, ch: int, w: int):
        """Draw a proportional scrollbar in the rightmost column."""
        total = len(self.flat)
        if total <= ch:
            return
        thumb_h = max(1, ch * ch // total)
        max_off = total - ch
        thumb_top = 1 + (self.offset * (ch - thumb_h) // max(1, max_off))
        for row in range(1, ch + 1):
            char = "▐" if thumb_top <= row < thumb_top + thumb_h else "╎"
            try:
                self.scr.addstr(row, w - 2, char, curses.color_pair(C_DIM))
            except curses.error:
                pass

    def _hline(self, y: int, w: int, label: str, color: int):
        line = "─" * (w - 1)
        pad = max(0, (w - len(label)) // 2)
        line = line[:pad] + label + line[pad + len(label):]
        try:
            self.scr.addstr(y, 0, line[:w - 1], curses.color_pair(color) | curses.A_BOLD)
        except curses.error:
            pass

    def _row(self, y: int, w: int, text: str, attr: int):
        try:
            self.scr.addstr(y, 0, text.ljust(w - 1)[:w - 1], attr)
        except curses.error:
            pass

    # ── main loop ─────────────────────────────────────────────────────────

    def run(self):
        curses.curs_set(0)
        self.scr.keypad(True)
        self.scr.timeout(200)

        _tick = 0
        while True:
            self.draw()
            key = self.scr.getch()

            if key == -1:
                _tick += 1
                if _tick % 25 == 0:  # full reload every ~5s
                    self.reload()
                else:
                    self._fast_refresh()
                continue

            # ── search mode ────────────────────────────────────────────────
            if self.search_mode:
                if key == 27:
                    self.search_mode = False
                    self.search_q = ""
                    self._apply_filter()
                elif key in (curses.KEY_ENTER, 10, 13):
                    self._do_open()
                    self.search_mode = False
                    self.search_q = ""
                    self._apply_filter()
                elif key == 9:  # Tab — next match
                    if self.search_match_indices:
                        nxt = next((i for i in self.search_match_indices if i > self.sel),
                                   self.search_match_indices[0])
                        self._goto(nxt)
                elif key == curses.KEY_BTAB:  # Shift+Tab — prev match
                    if self.search_match_indices:
                        prv = next((i for i in reversed(self.search_match_indices) if i < self.sel),
                                   self.search_match_indices[-1])
                        self._goto(prv)
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    self.search_q = self.search_q[:-1]
                    self._apply_filter()
                    if self.search_match_indices:
                        self._goto(self.search_match_indices[0])
                elif 32 <= key < 127:
                    self.search_q += chr(key)
                    self._apply_filter()
                    if self.search_match_indices:
                        self._goto(self.search_match_indices[0])
                continue

            # ── normal mode ────────────────────────────────────────────────
            if key == curses.KEY_RESIZE:
                self._goto(self.sel)
            elif key in (ord("q"),):
                break
            elif key == 27:
                if self.marked:
                    self.marked.clear()
                    self.status = "Marks cleared"
            elif key == ord("k"):
                self._goto(self.sel - 1)
            elif key == ord("j"):
                self._goto(self.sel + 1)
            elif key == ord("g"):
                self._goto(0)
            elif key == ord("G"):
                self._goto(len(self.flat) - 1)
            elif key == ord(" "):
                cur = self._cur()
                if cur and cur[0] == "session":
                    sid = cur[2]["id"]
                    if sid in self.marked:
                        self.marked.discard(sid)
                    else:
                        self.marked.add(sid)
                    self._goto(self.sel + 1)
                elif cur and cur[0] == "project":
                    cur[1]["expanded"] = not cur[1]["expanded"]
                    self._rebuild_all()
            elif key in (curses.KEY_ENTER, 10, 13, ord("o")):
                if self.marked:
                    self.status = self._do_open_marked()
                else:
                    self.status = self._do_open(jump=True)
            elif key == ord("n"):
                self.status = self._do_new_session()
            elif key == ord("N"):
                self._prompt_project()
            elif key == ord("d"):
                self._delete_cur()
            elif key == ord("/"):
                self.search_mode = True
                self.search_q = ""
                self._apply_filter()
            elif key == ord("e"):
                self._edit_title()
            elif key == ord("i"):
                self._show_info()
            elif key == ord("?"):
                self._show_help()
            elif key == ord("K"):
                self._kill_pane()
            elif key == ord("Q"):
                self._kill_all_panes()
            elif key == curses.KEY_MOUSE:
                self._handle_mouse()

    # ── mouse ─────────────────────────────────────────────────────────────

    def _handle_mouse(self):
        try:
            _, _mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return

        ch = self._content_h()

        # scroll wheel (button 4 = up, button 5 = down)
        if bstate & curses.BUTTON4_PRESSED:
            self._goto(self.sel - 3)
            return
        if bstate & getattr(curses, "BUTTON5_PRESSED", 0):
            self._goto(self.sel + 3)
            return

        # clicks in the list area (rows 1 .. ch)
        if not (1 <= my <= ch):
            return

        idx = (my - 1) + self.offset
        if not (0 <= idx < len(self.flat)):
            return

        clicked = bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED |
                             curses.BUTTON1_DOUBLE_CLICKED)
        if not clicked:
            return

        self._goto(idx)

    # ── actions ───────────────────────────────────────────────────────────

    def _do_open(self, jump: bool = False) -> str:
        cur = self._cur()
        if not cur:
            return "Nothing selected"
        kind, proj, sess = cur

        if not proj.get("path_exists", True):
            return f"Path does not exist: {proj['path']}"

        # If the session is already live in a pane, switch focus there
        if kind == "session" and sess["id"] in self.running_sessions:
            pane_id = self.running_sessions[sess["id"]]
            if jump:
                time.sleep(0.05)
                subprocess.run(["tmux", "select-pane", "-t", pane_id], capture_output=True)
            return f"Switched → {sess['title'][:35]}"

        if kind == "project":
            sessions = proj["sessions"]
            if sessions:
                top = sessions[0]
                if top["id"] in self.running_sessions:
                    pane_id = self.running_sessions[top["id"]]
                    if jump:
                        time.sleep(0.05)
                        subprocess.run(["tmux", "select-pane", "-t", pane_id], capture_output=True)
                    return f"Switched → {top['title'][:35]}"
                msg, pane_id = open_session(proj, top)
                sid = top["id"]
            else:
                msg, pane_id = new_session(proj)
                sid = None
                if pane_id:
                    self._pending_panes[pane_id] = (proj["dir_name"], time.time())
        else:
            msg, pane_id = open_session(proj, sess)
            sid = sess["id"]

        if pane_id and sid:
            # verify the pane is still alive before marking (command may exit instantly)
            r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                               capture_output=True, text=True)
            if pane_id in r.stdout.split():
                self.running_sessions[sid] = pane_id

        if pane_id and jump and _in_tmux():
            time.sleep(0.05)
            subprocess.run(["tmux", "select-pane", "-t", pane_id], capture_output=True)

        panes = count_session_panes()
        pane_s = f" · {panes} pane{'s' if panes != 1 else ''}" if panes else ""
        return f"{msg}{pane_s}"

    def _do_open_marked(self) -> str:
        # Build a flat lookup: session_id → (proj, sess)
        sess_map: dict[str, tuple] = {}
        for proj in self.projects:
            for sess in proj["sessions"]:
                sess_map[sess["id"]] = (proj, sess)

        opened, skipped = 0, 0
        for sid in list(self.marked):
            if sid not in sess_map:
                continue
            proj, sess = sess_map[sid]
            if sid in self.running_sessions:
                skipped += 1
                continue
            _, pane_id = open_session(proj, sess)
            if pane_id:
                self.running_sessions[sid] = pane_id
            opened += 1

        self.marked.clear()
        panes = count_session_panes()
        pane_s = f" · {panes} pane{'s' if panes != 1 else ''}" if panes else ""
        parts = []
        if opened:
            parts.append(f"Opened {opened}")
        if skipped:
            parts.append(f"{skipped} already running")
        return (", ".join(parts) or "Nothing opened") + pane_s

    def _do_new_session(self) -> str:
        cur = self._cur()
        if not cur:
            return "Nothing selected"
        if not cur[1].get("path_exists", True):
            return f"Path does not exist: {cur[1]['path']}"
        msg, pane_id = new_session(cur[1])
        if pane_id:
            self._pending_panes[pane_id] = (cur[1]["dir_name"], time.time())
            if _in_tmux():
                time.sleep(0.05)
                subprocess.run(["tmux", "select-pane", "-t", pane_id], capture_output=True)
        panes = count_session_panes()
        pane_s = f" · {panes} pane{'s' if panes != 1 else ''}" if panes else ""
        return f"{msg}{pane_s}"

    def _readline(self, prompt: str, complete_dirs: bool = False) -> str | None:
        """Read a line from the bottom bar. Returns None if ESC was pressed."""
        h, w = self.scr.getmaxyx()
        buf = ""
        _comp_stem: str | None = None
        _completions: list[str] = []
        _comp_idx: int = 0
        curses.curs_set(1)

        def _get_completions(stem: str) -> list[str]:
            expanded = os.path.expanduser(stem)
            home = str(Path.home())
            results = []
            for p in glob.glob(expanded + "*"):
                if os.path.isdir(p):
                    c = p + "/"
                    if stem.startswith("~") and c.startswith(home):
                        c = "~" + c[len(home):]
                    results.append(c)
            return sorted(results)

        while True:
            display = (prompt + buf + " " * w)[:w - 1]
            try:
                self.scr.addstr(h - 1, 0, display, curses.color_pair(C_SEL))
                self.scr.move(h - 1, min(len(prompt) + len(buf), w - 2))
                self.scr.refresh()
            except curses.error:
                pass
            try:
                ch = self.scr.getch()
            except curses.error:
                continue
            if ch == 27:
                curses.curs_set(0)
                return None
            if ch in (curses.KEY_ENTER, 10, 13):
                curses.curs_set(0)
                return buf.strip()
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
                _comp_stem = None
                _completions = []
            elif complete_dirs and ch in (9, curses.KEY_BTAB):  # Tab / Shift+Tab
                forward = ch == 9
                if _comp_stem is None:
                    _comp_stem = buf
                    _completions = _get_completions(buf)
                    _comp_idx = 0 if forward else len(_completions) - 1
                else:
                    if forward:
                        _comp_idx = (_comp_idx + 1) % len(_completions) if _completions else 0
                    else:
                        _comp_idx = (_comp_idx - 1) % len(_completions) if _completions else 0
                if _completions:
                    buf = _completions[_comp_idx]
            elif 32 <= ch < 127:
                buf += chr(ch)
                _comp_stem = None
                _completions = []

    def _prompt_project(self):
        raw = self._readline(" Dir: ", complete_dirs=True)
        if raw is None:
            self.status = "Cancelled"
            return
        if not raw:
            self.status = "No path entered"
            return
        try:
            path = os.path.normpath(os.path.expanduser(raw))
        except Exception:
            self.status = "Invalid path"
            return
        if not os.path.isdir(path):
            h, w = self.scr.getmaxyx()
            msg = f" Dir not found, create? [y/N] "
            try:
                self.scr.addstr(h - 1, 0, (msg + " " * w)[:w - 1], curses.color_pair(C_SEL))
            except curses.error:
                pass
            self.scr.refresh()
            self.scr.timeout(-1)
            ch = self.scr.getch()
            self.scr.timeout(200)
            if ch != ord("y"):
                self.status = "Cancelled"
                return
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                self.status = f"mkdir failed: {e}"
                return
        try:
            msg, pane_id = new_session({"path": path, "display": os.path.basename(path)})
            if pane_id:
                self._pending_panes[pane_id] = (path.replace("/", "-"), time.time())
                if _in_tmux():
                    time.sleep(0.05)
                    subprocess.run(["tmux", "select-pane", "-t", pane_id], capture_output=True)
            self.status = msg
            self.reload()
        except Exception as e:
            self.status = f"Error: {e}"

    def _delete_cur(self):
        cur = self._cur()
        if not cur:
            self.status = "Nothing selected"
            return

        if cur[0] == "project":
            proj = cur[1]
            if proj.get("path_exists", True):
                self.status = "Select a session to delete"
                return
            h, w = self.scr.getmaxyx()
            msg = f" Delete project record '{proj['display']}'? [y/N] "
            try:
                self.scr.addstr(h - 1, 0, (msg + " " * w)[:w - 1], curses.color_pair(C_SEL))
            except curses.error:
                pass
            self.scr.refresh()
            self.scr.timeout(-1)
            ch = self.scr.getch()
            self.scr.timeout(200)
            if ch == ord("y"):
                try:
                    shutil.rmtree(CLAUDE_PROJECTS_DIR / proj["dir_name"])
                    self.status = "Project record deleted"
                except OSError as e:
                    self.status = f"Delete failed: {e}"
                self.reload()
            else:
                self.status = "Cancelled"
            return

        sess = cur[2]
        h, w = self.scr.getmaxyx()
        msg = f" Delete '{sess['title'][:35]}'? [y/N] "
        try:
            self.scr.addstr(h - 1, 0, (msg + " " * w)[:w - 1], curses.color_pair(C_SEL))
        except curses.error:
            pass
        self.scr.refresh()
        self.scr.timeout(-1)
        ch = self.scr.getch()
        self.scr.timeout(200)
        if ch == ord("y"):
            try:
                sess["file"].unlink()
                self.status = "Session deleted"
            except OSError as e:
                self.status = f"Delete failed: {e}"
            self.reload()
        else:
            self.status = "Cancelled"

    def _kill_pane(self):
        cur = self._cur()
        if not cur or cur[0] != "session":
            self.status = "Select a running session to kill"
            return
        sess = cur[2]
        pane_id = self.running_sessions.get(sess["id"])
        if not pane_id:
            self.status = "Session is not running in a pane"
            return
        h, w = self.scr.getmaxyx()
        msg = f" Kill? '{sess['title'][:35]}'"
        try:
            self.scr.addstr(h - 1, 0, (msg + " " * w)[:w - 1], curses.color_pair(C_SEL))
        except curses.error:
            pass
        self.scr.refresh()
        self.scr.timeout(-1)
        ch = self.scr.getch()
        self.scr.timeout(200)
        if ch in (ord("y"), ord("Y"), curses.KEY_ENTER, 10, 13):
            ret = subprocess.run(["tmux", "kill-pane", "-t", pane_id], capture_output=True)
            if ret.returncode == 0:
                self.running_sessions.pop(sess["id"], None)
                self.status = f"Killed pane {pane_id}"
            else:
                self.status = f"kill-pane failed: {ret.stderr.decode().strip()}"
        else:
            self.status = "Cancelled"

    def _kill_all_panes(self):
        if not self.running_sessions:
            self.status = "No running sessions"
            return
        n = len(self.running_sessions)
        h, w = self.scr.getmaxyx()
        msg = f" Kill all {n} running session{'s' if n != 1 else ''}? [y/N] "
        try:
            self.scr.addstr(h - 1, 0, (msg + " " * w)[:w - 1], curses.color_pair(C_SEL))
        except curses.error:
            pass
        self.scr.refresh()
        self.scr.timeout(-1)
        ch = self.scr.getch()
        self.scr.timeout(200)
        if ch not in (ord("y"), ord("Y")):
            self.status = "Cancelled"
            return
        killed, failed = 0, 0
        for sid, pane_id in list(self.running_sessions.items()):
            ret = subprocess.run(["tmux", "kill-pane", "-t", pane_id], capture_output=True)
            if ret.returncode == 0:
                self.running_sessions.pop(sid, None)
                killed += 1
            else:
                failed += 1
        self.status = f"Killed {killed}" + (f", {failed} failed" if failed else "")

    def _edit_title(self):
        cur = self._cur()
        if not cur or cur[0] != "session":
            self.status = "Select a session to rename"
            return
        sess = cur[2]
        new_title = self._readline(" New title: ")
        if new_title is None:
            self.status = "Cancelled"
        elif new_title:
            try:
                save_custom_title(sess["id"], new_title)
                sess["title"] = new_title
                sess["custom"] = True
                self.status = f"Renamed → {new_title[:40]}"
            except Exception as e:
                self.status = f"Error: {e}"
        else:
            self.status = "Cancelled (empty input)"

    def _show_popup(self, rows: list[str]):
        """Render a centered box with rows, wait for any key."""
        h, w = self.scr.getmaxyx()
        box_w = min(w - 2, max((len(r) for r in rows), default=20) + 4)
        box_h = len(rows) + 3
        by = max(1, (h - box_h) // 2)
        bx = max(0, (w - box_w) // 2)
        attr = curses.color_pair(C_SEL)
        try:
            self.scr.addstr(by, bx, ("┌" + "─" * (box_w - 2) + "┐")[:w - bx - 1], attr)
            for i, row in enumerate(rows):
                self.scr.addstr(by + 1 + i, bx,
                                ("│ " + row.ljust(box_w - 3) + "│")[:w - bx - 1], attr)
            self.scr.addstr(by + len(rows) + 1, bx,
                            ("└" + "─" * (box_w - 2) + "┘")[:w - bx - 1], attr)
            self.scr.addstr(by + len(rows) + 2, bx,
                            "  any key to close  ".center(box_w)[:w - bx - 1],
                            curses.color_pair(C_DIM))
        except curses.error:
            pass
        self.scr.refresh()
        self.scr.timeout(-1)
        self.scr.getch()
        self.scr.timeout(200)

    def _show_help(self):
        self._show_popup([
            "Navigation",
            "  j / k        move cursor",
            "  g / G        top / bottom",
            "  Space        on project: expand/collapse",
            "               on session: mark/unmark (batch open)",
            "  Esc          clear all marks",
            "",
            "Sessions",
            "  Enter / o    open or resume session",
            "  n            new session",
            "  N            new project  (Esc cancels)",
            "  d            delete session  (or ghost project)",
            "  K            kill tmux pane  (Enter/y confirms)",
            "  Q            kill all running sessions  (y confirms)",
            "  e            rename session  (Esc cancels)",
            "  i            session info",
            "",
            "Search  (/)",
            "  type         filter & highlight matches",
            "  Tab          jump to next match",
            "  Shift+Tab    jump to prev match",
            "  Enter        open selected & exit search",
            "  Esc          exit search",
            "",
            "Other",
            "  q            quit",
            "  ?            this help",
            "",
            "Indicators",
            "  ⟳  Claude is responding              (blue)",
            "  ●  waiting for your input  (blinks)  (yellow)",
            "  ‼  waiting for permission  (blinks)  (yellow)",
            "  ○  idle — waiting > 5 min            (green)",
        ])

    def _show_info(self):
        """Show a popup with full details of the selected item."""
        cur = self._cur()
        if not cur:
            return
        h, w = self.scr.getmaxyx()

        if cur[0] == "session":
            sess, proj = cur[2], cur[1]
            from datetime import datetime as _dt
            created  = _dt.fromtimestamp(sess["birthtime"]).strftime("%Y-%m-%d %H:%M")
            modified = _dt.fromtimestamp(sess["mtime"]).strftime("%Y-%m-%d %H:%M")
            turns = 0
            try:
                with open(sess["file"], "r", errors="ignore") as f:
                    for raw in f:
                        try:
                            if json.loads(raw.strip()).get("type") == "user":
                                turns += 1
                        except json.JSONDecodeError:
                            pass
            except (IOError, OSError):
                pass
            lines = [
                ("Title",    sess["title"]),
                ("ID",       sess["id"]),
                ("Path",     proj["path"]),
                ("Created",  created),
                ("Modified", modified),
                ("Turns",    str(turns)),
            ]
        else:
            proj = cur[1]
            lines = [
                ("Project",  proj["display"]),
                ("Path",     proj["path"]),
                ("Sessions", str(len(proj["sessions"]))),
            ]

        rows: list[str] = []
        key_w = max(len(k) for k, _ in lines) + 2
        val_w = max(w - key_w - 6, 10)
        for k, v in lines:
            while v:
                rows.append(f"{k.ljust(key_w)}{v[:val_w]}")
                v = v[val_w:]
                k = ""
        self._show_popup(rows)


# ─── entry ────────────────────────────────────────────────────────────────────

_HELP = """\
ccman – Claude Code session manager

Usage:  ccman [--help]

Navigation:
  j / k        move cursor              g / G     top / bottom
  Space        on project: expand/collapse
               on session: mark/unmark (batch open with Enter)
  Esc          clear all marks

Actions:
  Enter / o    open or resume session
  n            new session in current project
  N            new project (prompts for path)
  d            delete session  (or ghost project if path missing)
  K            kill tmux pane of running session  (Enter/y confirms)
  e            rename session (custom title)
  i            show full info popup
  q            quit
  ?            in-app help

Search  (/):
  type         highlight matching sessions in full list
  Tab          jump to next match
  Shift+Tab    jump to previous match
  Enter        open selected session and exit search
  Esc          exit search

Legend:
  ●  session is running in a tmux pane  (Enter switches focus to it)
  ⚠  project directory no longer exists on disk  (d to delete record)

Mouse:
  click once to select, click again (or double-click) to open
  scroll wheel navigates the list

Sessions are read from  ~/.claude/projects/
Sessions are sorted by creation time (newest first).
Projects are sorted by most recently modified session.
"""


def _run_hook():
    """`ccman hook` — read a Claude Code hook event on stdin, write session state.

    Wired into ~/.claude/settings.json hooks; never run by hand.
    """
    import sys
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    sid = d.get("session_id")
    if not sid:
        return
    event = d.get("hook_event_name", "")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = STATE_DIR / sid
    if event == "SessionEnd":
        f.unlink(missing_ok=True)
    elif event in ("UserPromptSubmit", "PreToolUse"):
        f.write_text("busy")
    elif event == "PermissionRequest" or (event == "Notification" and "permission" in d.get("notification_type", "").lower()):
        f.write_text("approval")
    else:   # Stop, SessionStart, non-permission Notifications
        f.write_text("waiting")


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        _run_hook()
        return
    if "-h" in sys.argv or "--help" in sys.argv:
        print(_HELP)
        return
    try:
        curses.wrapper(lambda s: App(s).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
