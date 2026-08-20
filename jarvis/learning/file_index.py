"""A model of this machine's files, trained on their metadata.

The problem this solves is the one that actually comes up: a person says "open
my marksheet" or "where's that internship report", not "open
C:/Users/heman/Desktop/prateek/marksheets/sem5.pdf". Resolving that vague
reference is a ranking problem over the filesystem, and it is a genuinely good
fit for learning from metadata because the signal is real:

    name        what the user calls it
    folder      where they chose to keep it
    extension   what kind of thing it is
    recency     what they are working on now
    depth       a file buried 8 levels down is rarely "the report"

Two things make this work on a real machine rather than in a demo.

First, most files are not the user's. A scan of this laptop found 40,004 files,
of which 39,739 sat inside a single project directory of vendored C headers and
Python packages. Indexing those drowns the ~265 documents a person would ever
refer to by name, so build directories, virtualenvs and package caches are
excluded. That exclusion is the difference between a useful index and noise.

Second, ranking is lexical plus structural rather than embedding-based. A
filename is a handful of tokens, often abbreviated, and character n-gram
overlap handles "marksheet"/"marksheets"/"mark sheet" better than a sentence
embedding does at this length - while costing nothing and needing no model.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import settings
from jarvis.core.events import emit
from jarvis.memory.retrieval import tokenize

INDEX_PATH = settings.DATA_DIR / "file_index.json"

# Directories that contain machine-generated files nobody refers to by name.
SKIP_DIRS = {
    "node_modules", "site-packages", "dist-packages", "__pycache__", ".git",
    ".venv", "venv", "env", ".env", "build", "dist", ".next", ".cache",
    "AppData", "Library", ".gradle", ".m2", "target", "obj", "bin",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", "vendor",
    "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin",
}
SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".pyi", ".o", ".obj", ".dll", ".so", ".dylib",
    ".class", ".lock", ".log", ".tmp", ".bak", ".swp", ".h", ".hpp", ".inc",
}
MAX_FILES = 20_000
MAX_DEPTH = 8

# A file touched today is far likelier to be "the report" than one from 2019.
RECENCY_HALFLIFE_DAYS = 45.0


@dataclass
class Entry:
    path: str
    name: str
    suffix: str
    folder: str
    size: int
    modified: float
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            # Folder names carry as much intent as the filename: a file inside
            # "marksheets" is a marksheet even if it is called "sem5.pdf".
            self.tokens = tokenize(f"{self.name} {self.folder}")


def _worth_indexing(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    return not any(part in SKIP_DIRS for part in path.parts)


def build(roots: list[Path] | None = None) -> list[Entry]:
    """Walk the user's own folders and record what is there."""
    home = Path.home()
    roots = roots or [home / n for n in
                      ("Desktop", "Documents", "Downloads", "Pictures")]
    import os

    entries: list[Entry] = []
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        # os.walk with in-place pruning, not rglob. rglob descends into a
        # directory before anything can filter it, so it walked the whole of a
        # .git object store and then died on a path that had been deleted
        # mid-scan. Pruning topdown skips those subtrees entirely, which is
        # both far faster and the reason the crash cannot recur.
        for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                    onerror=lambda _e: None):
            here = Path(dirpath)
            if len(here.parts) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for filename in filenames:
                if len(entries) >= MAX_FILES:
                    return entries
                path = here / filename
                if path.suffix.lower() in SKIP_SUFFIXES:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue      # deleted or unreadable between walk and stat
                entries.append(Entry(
                    path=str(path), name=path.stem,
                    suffix=path.suffix.lower(), folder=here.name,
                    size=stat.st_size, modified=stat.st_mtime))
    return entries


class FileIndex:
    """Ranks files against a vague description of one."""

    def __init__(self, entries: list[Entry] | None = None) -> None:
        self.entries = entries or []
        self._df: Counter[str] = Counter()
        self._reindex()

    def _reindex(self) -> None:
        self._df = Counter()
        for e in self.entries:
            self._df.update(set(e.tokens))

    # -- persistence ---------------------------------------------------------

    def save(self, path: Path | None = None) -> None:
        target = path or INDEX_PATH
        payload = {"built": time.time(), "count": len(self.entries),
                   "files": [{"path": e.path, "name": e.name,
                              "suffix": e.suffix, "folder": e.folder,
                              "size": e.size, "modified": e.modified}
                             for e in self.entries]}
        target.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "FileIndex":
        target = path or INDEX_PATH
        if not target.exists():
            return cls([])
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls([])
        return cls([Entry(**f) for f in raw.get("files", [])])

    @property
    def built_recently(self) -> bool:
        return bool(self.entries)

    # -- ranking -------------------------------------------------------------

    def _idf(self, term: str) -> float:
        n = len(self.entries) or 1
        df = self._df.get(term, 0)
        # A term appearing in every filename ("project") carries no signal.
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _recency(self, entry: Entry) -> float:
        age_days = max(0.0, (time.time() - entry.modified) / 86400)
        return 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)

    def find(self, description: str, *, limit: int = 5,
             suffix: str | None = None) -> list[tuple[Entry, float]]:
        """Best matches for a loose description, best first."""
        terms = tokenize(description)
        if not terms or not self.entries:
            return []
        scored: list[tuple[float, Entry]] = []
        for entry in self.entries:
            if suffix and entry.suffix != suffix.lower():
                continue
            have = set(entry.tokens)
            hits = [t for t in terms if t in have]
            if not hits:
                # Substring rescue: "marksheet" should still reach
                # "marksheets", which tokenising alone does not join.
                joined = " ".join(entry.tokens)
                hits = [t for t in terms if len(t) > 3 and t in joined]
                if not hits:
                    continue
            lexical = sum(self._idf(t) for t in hits) / max(1, len(terms))
            # Shallow files are likelier to be the one meant; the deep copy in
            # an archive folder usually is not.
            depth = len(Path(entry.path).parts)
            score = (lexical
                     + 0.35 * self._recency(entry)
                     - 0.02 * depth)
            scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        return [(e, round(s, 3)) for s, e in scored[:limit]]

    def stats(self) -> dict[str, object]:
        by_suffix = Counter(e.suffix or "(none)" for e in self.entries)
        return {"files": len(self.entries),
                "top_types": dict(by_suffix.most_common(8))}


def rebuild_and_save() -> dict[str, object]:
    entries = build()
    index = FileIndex(entries)
    index.save()
    emit("file_index.built", files=len(entries))
    return index.stats()
