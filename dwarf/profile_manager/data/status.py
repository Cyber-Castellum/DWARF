"""Auto-derived project status snapshot for /learn/status.

Each extractor reads from main's tree via `git ls-tree` / `git show`,
falling back to the worktree filesystem when git is unavailable.
Falling back sets a module-level marker that the view surfaces as an
explicit caveat — readers know the displayed state may not be the
canonical main view.

Reading from main means the page reflects Dwarf-the-project state, not
the dashboard-rebuild branch's in-flight work. The recent-commits
extractor uses `git log main` for the same reason.

No hand-curated lists. No fabrication. needs_source preserved verbatim
when present in source documents.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# Slice-16: dashboard scaffolding filter for /learn/status's "recent main
# commits" section. Two regex patterns capture (a) bare 'plan:' slice docs
# and (b) any (dashboard)-scoped Conventional-Commits type. Future-proof
# against new types like fix(dashboard) or chore(dashboard).
_SCAFFOLDING_PATTERNS = (
    re.compile(r"^plan:"),
    re.compile(r"^[a-z]+\(dashboard\):"),
)


def _is_scaffolding(message: str) -> bool:
    """Return True if the commit message matches any dashboard-scaffolding
    pattern (slice planning docs or any (dashboard)-scoped commit type).
    Module-private; tested via the public recent_main_commits and via
    direct example pinning in tests/test_status_data.py."""
    return any(pat.match(message) for pat in _SCAFFOLDING_PATTERNS)


def _project_root() -> Path:
    # data/status.py -> data/ -> profile_manager/ -> dwarf/ -> repo
    return Path(__file__).resolve().parents[3]


# Module-level source marker. The view reads this after all extractors have
# run for a single render and surfaces a caveat when worktree-filesystem was
# used. Reset at the start of each request via reset_data_source().
_DATA_SOURCE = {"used_filesystem_fallback": False}


def reset_data_source() -> None:
    """Reset the data-source marker. Called by the view at the start of a render."""
    _DATA_SOURCE["used_filesystem_fallback"] = False


def data_source_used_filesystem_fallback() -> bool:
    """Return True if any extractor in this render fell back to worktree filesystem."""
    return _DATA_SOURCE["used_filesystem_fallback"]


def _mark_fallback() -> None:
    _DATA_SOURCE["used_filesystem_fallback"] = True


def _git_show(relative_path: str) -> str | None:
    """Read a file's content from main's tree via `git show main:<path>`.

    Returns None on git failure (caller decides whether to fall back).
    """
    try:
        result = subprocess.run(
            ["git", "show", f"main:{relative_path}"],
            capture_output=True, text=True, check=False,
            cwd=str(_project_root()), timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_ls_tree(relative_dir: str) -> list[str] | None:
    """List filenames in a directory of main's tree via `git ls-tree`.

    Returns relative paths from repo root (not just basenames). None on
    git failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "--name-only", "main", f"{relative_dir.rstrip('/')}/"],
            capture_output=True, text=True, check=False,
            cwd=str(_project_root()), timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.splitlines() if p.strip()]


def _read_main_or_fallback(relative_path: str) -> str | None:
    """Read main:<path>, falling back to worktree filesystem on git failure."""
    content = _git_show(relative_path)
    if content is not None:
        return content
    fs_path = _project_root() / relative_path
    try:
        text = fs_path.read_text(encoding="utf-8")
        _mark_fallback()
        return text
    except OSError:
        return None


def _list_main_or_fallback(relative_dir: str) -> list[str]:
    """List files in main:<dir>, falling back to worktree filesystem on git failure.

    Returns a list of paths relative to repo root.
    """
    paths = _git_ls_tree(relative_dir)
    if paths is not None:
        # `git ls-tree` returns directories with trailing slashes; filter to files.
        return [p for p in paths if not p.endswith("/")]
    fs_dir = _project_root() / relative_dir
    if not fs_dir.is_dir():
        return []
    out = []
    for entry in sorted(fs_dir.iterdir()):
        if entry.is_file():
            out.append(str(entry.relative_to(_project_root())))
    _mark_fallback()
    return out


def latest_currentstatus_path() -> str | None:
    """Return the highest-numbered currentstatus-*.md file path on main, or None.

    Path is relative to repo root. Reads from main; falls back to worktree
    filesystem if git is unavailable.
    """
    # Top-level files: ls-tree the repo root's tree.
    try:
        result = subprocess.run(
            ["git", "ls-tree", "--name-only", "main"],
            capture_output=True, text=True, check=False,
            cwd=str(_project_root()), timeout=10,
        )
        if result.returncode == 0:
            paths = [
                p for p in result.stdout.splitlines()
                if p.startswith("currentstatus-") and p.endswith(".md")
            ]
            if paths:
                return sorted(paths)[-1]
            return None
    except (OSError, subprocess.SubprocessError):
        pass
    # Filesystem fallback.
    repo = _project_root()
    matches = sorted(repo.glob("currentstatus-*.md"))
    if matches:
        _mark_fallback()
        return str(matches[-1].relative_to(repo))
    return None


def _parse_h1_from_text(text: str) -> str | None:
    """Return the first H1 (`# Heading`) line content from a markdown blob, or None."""
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return None


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?")


def _normalize_markdown_inline(text: str) -> str:
    """Replace `[label](url)` with `label`; strip leading `> ` blockquote markers."""
    text = _MD_LINK_RE.sub(r"\1", text)
    return text


def _parse_lead_paragraphs(text: str, max_paragraphs: int = 3, max_chars: int = 600) -> str:
    """Return the first up-to-N paragraphs after the H1, joined with line breaks.

    Stops on: max_paragraphs reached, a new `## ` heading after at least one
    paragraph collected, or end of file. `## ` headings before the first
    paragraph are skipped (a common pattern: H1 followed by `## Scope` then body).

    Markdown inline links `[text](url)` are normalized to `text` so the URL
    noise doesn't leak into the rendered body. Blockquote markers (`> `) are
    stripped so the quoted text reads as prose.
    """
    in_body = False
    paragraphs: list[list[str]] = [[]]
    for line in text.splitlines():
        if not in_body:
            if line.startswith("# ") and not line.startswith("## "):
                in_body = True
            continue
        stripped = line.strip()
        if not stripped:
            if paragraphs[-1]:
                if len(paragraphs) >= max_paragraphs:
                    break
                paragraphs.append([])
            continue
        if stripped.startswith("##"):
            if paragraphs[-1] or len(paragraphs) > 1 or any(p for p in paragraphs):
                break
            continue
        # Strip blockquote marker if present.
        stripped = _MD_BLOCKQUOTE_RE.sub("", stripped)
        paragraphs[-1].append(stripped)
    chunks = [" ".join(lines).strip() for lines in paragraphs if lines]
    out = "\n\n".join(chunks)
    out = _normalize_markdown_inline(out)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    return out


def current_phase_summary() -> dict:
    """Return a stub for the 'Current phase' section."""
    repo = _project_root()
    rel_path = latest_currentstatus_path()
    if rel_path is None:
        return {
            "title": "",
            "last_modified": "",
            "anchor_path": "",
            "needs_source": True,
        }
    content = _read_main_or_fallback(rel_path)
    if content is None:
        return {
            "title": "",
            "last_modified": "",
            "anchor_path": rel_path,
            "needs_source": True,
        }
    title = _parse_h1_from_text(content) or rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # last_modified comes from filesystem mtime when available; otherwise from
    # the parsed H1 (which by convention contains the date) is acceptable but
    # less precise. Try filesystem first.
    fs_path = repo / rel_path
    last_modified = ""
    try:
        mtime = datetime.fromtimestamp(fs_path.stat().st_mtime, tz=timezone.utc)
        last_modified = mtime.strftime("%Y-%m-%d")
    except OSError:
        # H1 fallback: extract trailing date from "Current Status - YYYY-MM-DD".
        m = re.search(r"\d{4}-\d{2}-\d{2}", title)
        if m:
            last_modified = m.group(0)
    return {
        "title": title,
        "last_modified": last_modified,
        "anchor_path": rel_path,
        "needs_source": False,
    }


def recent_main_commits(limit: int = 20) -> list[dict]:
    """Return up to `limit` recent commits on main, with dashboard scaffolding
    (plan: documents and (dashboard)-scoped commits) filtered out.

    Over-fetches max(limit*5, 50) rows from git log so the filtered window
    almost always fills `limit` even when a slice has just landed and dumped
    several scaffolding commits in a row. The cap protects small `limit`
    callers (e.g., limit=2 would otherwise fetch only 10 rows).

    git log queries `main` explicitly; no filesystem fallback because the
    git history itself is the source of truth and there is no equivalent
    file-on-disk representation.
    """
    repo = _project_root()
    fetch_n = max(int(limit) * 5, 50)
    try:
        result = subprocess.run(
            [
                "git", "log", "main",
                "--pretty=format:%h%x09%s%x09%ad",
                "--date=short",
                f"-n{fetch_n}",
            ],
            capture_output=True, text=True, check=False, cwd=str(repo),
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        if _is_scaffolding(parts[1]):
            continue
        rows.append({
            "hash": parts[0],
            "message": parts[1],
            "date": parts[2],
        })
        if len(rows) >= int(limit):
            break
    return rows


def candidate_findings() -> list[dict]:
    """Return open candidate findings from main:agent/research/*-candidate.md.

    Reads file list and content from main's tree; falls back to worktree
    filesystem on git failure. The fallback marker is set when used so the
    view can surface a caveat.
    """
    paths = _list_main_or_fallback("agent/research")
    candidate_paths = sorted(p for p in paths if p.endswith("-candidate.md"))
    out: list[dict] = []
    for path in candidate_paths:
        content = _read_main_or_fallback(path)
        if content is None:
            continue
        title = _parse_h1_from_text(content) or _slug_from_path(path).replace("-", " ").title()
        summary = _parse_lead_paragraphs(content)
        out.append({
            "slug": _slug_from_path(path),
            "title": title,
            "status": "open",
            "summary": summary,
            "anchor_path": path,
        })
    return out


def _slug_from_path(path: str) -> str:
    """Extract slug (filename without extension) from a relative path."""
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0]


_WAITING_ON_RE = re.compile(r"^\s*-\s+\*\*Waiting on:\*\*\s+(.+?)\s*$")
_SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")


def open_carry_overs() -> list[dict]:
    """Return open carry-overs parsed from the latest currentstatus."""
    rel_path = latest_currentstatus_path()
    if rel_path is None:
        return []
    text = _read_main_or_fallback(rel_path)
    if text is None:
        return []
    section = "(unsectioned)"
    out: list[dict] = []
    for line in text.splitlines():
        sec = _SECTION_HEADER_RE.match(line)
        if sec:
            section = sec.group(1).strip()
            continue
        m = _WAITING_ON_RE.match(line)
        if m:
            text_only = m.group(1).strip()
            if text_only.startswith("No "):
                continue
            out.append({
                "section": section,
                "text": text_only,
                "anchor_path": rel_path,
            })
    return out
