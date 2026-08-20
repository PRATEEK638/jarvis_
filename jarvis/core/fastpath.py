"""Deterministic intent shortcuts.

Some requests have exactly one correct interpretation, and a language model adds
latency, cost and a failure mode without adding accuracy. Observed on this build:
"remember that my deadline is today" was planned by llama3:8b as `create_folder`,
because an 8B model maps unfamiliar phrasing onto whichever ability it saw first.
A regular expression does not make that mistake.

So a small set of unambiguous intents is matched here and turned straight into a
plan. These runs are recorded with tier `deterministic`: no model was called, no
bytes were sent, and latency is measured in microseconds.

This is the architecture's "prefer deterministic software where it is superior"
rule applied literally. The set stays deliberately small — anything genuinely
ambiguous belongs to the planner.
"""

from __future__ import annotations

import re

from jarvis.core.contracts import Plan, Step

# Each entry: compiled pattern -> builder turning the match into a Plan.
# Patterns must be specific enough that a false positive is close to impossible.

_REMEMBER = re.compile(
    r"^\s*(?:please\s+)?(?:remember|note|keep in mind)\s+"
    r"(?:that\s+|this[:,]?\s+)?(?P<content>.{3,})$",
    re.IGNORECASE | re.DOTALL,
)

_RECALL = re.compile(
    r"^\s*(?:what do you (?:remember|know) about|do you remember|recall|"
    r"remind me (?:what|about)|what did i tell you about)\s*"
    r"(?P<query>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_OPEN_APP = re.compile(
    r"^\s*(?:please\s+)?(?:open|launch|start)\s+(?:the\s+|my\s+)?"
    r"(?P<app>[a-z0-9 .+_-]{2,40}?)\s*(?:app|application|program)?\s*$",
    re.IGNORECASE,
)

_SYSTEM_STATE = re.compile(
    r"^\s*(?:what(?:'s| is| are)?\s+)?(?:my\s+)?"
    r"(?:current\s+)?(?:cpu|ram|memory|disk|system)\s*"
    r"(?:usage|state|status|info|information|stats|statistics|"
    r"space|free space)?\s*\??\s*$",
    re.IGNORECASE,
)

_LIST_WINDOWS = re.compile(
    r"^\s*(?:what|which)\s+(?:windows|apps|applications)\s+"
    r"(?:do i have\s+)?(?:are\s+)?(?:open|running)\s*\??\s*$",
    re.IGNORECASE,
)

# -- file operations --------------------------------------------------------
# These are the highest-traffic requests and they are strongly patterned. Doing
# them deterministically keeps them instant, exact, and entirely on-device,
# instead of spending ~9 s on an 8B model that sometimes picks the wrong ability.

_POLITE = r"(?:please\s+|can you\s+|could you\s+|,?\s*)*"
_NAMED = r"(?:called|named|titled)\s+"
_PATHISH = r"[^\"'\n]+?"

_MK_FOLDER = re.compile(
    rf"^\s*{_POLITE}(?:make|create|add|new)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
    rf"(?:folder|directory|dir)\s+(?:{_NAMED})?"
    rf"[\"']?(?P<name>[^\"'\n]+?)[\"']?"
    rf"(?:\s+(?:in|at|on|inside|under)\s+[\"']?(?P<where>{_PATHISH})[\"']?)?"
    rf"\s*$",
    re.IGNORECASE,
)

_MK_FILE = re.compile(
    rf"^\s*{_POLITE}(?:make|create|add|write|put)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
    rf"(?:file|text file|note)?\s*(?:{_NAMED})?"
    rf"[\"']?(?P<name>[\w .\\/:~%-]+?\.\w{{1,6}}|[\w -]+?)[\"']?"
    rf"(?:\s+(?:in|at|into|inside|under)\s+[\"']?(?P<where>{_PATHISH})[\"']?)?"
    rf"\s+(?:that\s+)?(?:says|saying|containing|contains|with (?:the )?(?:text|content)"
    rf"|with)\s+[\"']?(?P<content>.+?)[\"']?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_WRITE_INTO = re.compile(
    rf"^\s*{_POLITE}(?:write|put|save)\s+[\"'](?P<content>.+?)[\"']\s+"
    rf"(?:in|into|to)\s+[\"']?(?P<path>{_PATHISH})[\"']?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_RENAME = re.compile(
    rf"^\s*{_POLITE}rename\s+(?:the\s+)?(?:file\s+|folder\s+)?"
    rf"[\"']?(?P<source>{_PATHISH})[\"']?\s+(?:to|as)\s+[\"']?(?P<new>[^\"'\n/\\]+?)"
    rf"[\"']?\s*$",
    re.IGNORECASE,
)

_COPY = re.compile(
    rf"^\s*{_POLITE}(?:copy|duplicate)\s+[\"']?(?P<source>{_PATHISH})[\"']?\s+"
    rf"(?:to|into|as)\s+[\"']?(?P<dest>{_PATHISH})[\"']?\s*$",
    re.IGNORECASE,
)

_MOVE = re.compile(
    rf"^\s*{_POLITE}move\s+[\"']?(?P<source>{_PATHISH})[\"']?\s+"
    rf"(?:to|into|in)\s+[\"']?(?P<dest>{_PATHISH})[\"']?\s*$",
    re.IGNORECASE,
)

_WHERE_IS = re.compile(
    r"^\s*(?:where\s+is|where's|locate|find(?:\s+me)?)\s+(?:the\s+)?(?:file\s+)?"
    r"[\"']?(?P<name>[\w .-]+?)[\"']?\s*\??\s*$",
    re.IGNORECASE,
)

# "find any file called welcome in <dir>" — a locate-by-name with a scope.
_FIND_IN = re.compile(
    rf"^\s*{_POLITE}(?:find|locate|search for|look for)\s+"
    rf"(?:any\s+|a\s+|the\s+|all\s+)?(?:files?|folders?)?\s*"
    rf"(?:{_NAMED})?[\"']?(?P<name>[\w .-]+?)[\"']?"
    rf"(?:\s+(?:in|under|inside|within)\s+[\"']?(?P<root>{_PATHISH})[\"']?)?"
    rf"\s*\??\s*$",
    re.IGNORECASE,
)

# "which file in <dir> contains buy milk" / "search <dir> for files mentioning x"
_CONTAINS_A = re.compile(
    rf"^\s*(?:which|what)\s+files?\s+(?:in|under|inside)\s+"
    rf"[\"']?(?P<root>{_PATHISH})[\"']?\s+"
    rf"(?:contains?|mentions?|has|have|includes?)\s+"
    rf"(?:the\s+)?(?:words?\s+|text\s+|phrase\s+)?[\"']?(?P<query>.+?)[\"']?"
    rf"\s*\??\s*$",
    re.IGNORECASE,
)
_CONTAINS_B = re.compile(
    rf"^\s*{_POLITE}(?:search|grep|look)\s+(?:in\s+|through\s+)?"
    rf"[\"']?(?P<root>{_PATHISH})[\"']?\s+for\s+"
    rf"(?:files?\s+)?(?:that\s+)?(?:mentioning|mention|containing|contain|with)?"
    rf"\s*(?:the\s+)?(?:words?\s+|text\s+)?[\"']?(?P<query>.+?)[\"']?"
    rf"\s*\??\s*$",
    re.IGNORECASE,
)

_MAKE_COPY_NAMED = re.compile(
    rf"^\s*{_POLITE}make\s+a\s+copy\s+of\s+[\"']?(?P<source>{_PATHISH})[\"']?\s+"
    rf"(?:{_NAMED})[\"']?(?P<new>[^\"'\n]+?)[\"']?\s*$",
    re.IGNORECASE,
)

# Words that mean the request is not a simple single file operation.
_TOO_COMPLEX = (" then ", " and then ", " after that", " every ", " all files",
                " each ", " summarise", " summarize", " analyse", " analyze")

# "create a folder called reports and move the txt files into it" is two steps,
# not a folder named "reports and move the txt files into it". A second verb
# after "and" is the reliable signal, and it does not fire on names that merely
# contain the word (e.g. "a folder called sales and marketing").
_SECOND_VERB = re.compile(
    r"and\s+(?:then\s+)?(?:move|copy|put|delete|remove|rename|open|write|"
    r"add|create|make|send|search|find|list|show)",
    re.IGNORECASE,
)

# Phrases that look like an app name but are really something else.
_NOT_APPS = {
    "file", "files", "folder", "the file", "a file", "it", "this", "that",
    "up", "over", "again", "them", "everything",
}


_HAS_EXTENSION = re.compile(r"\.\w{1,6}$")


def _looks_like_a_filename(name: str, text: str) -> bool:
    """Guard against reading a knowledge question as a file search.

    "find the capital of Japan" is not a request to locate a file. Without a
    search location to anchor it, a name is only treated as a filename when it
    carries an extension, is a single token, or the sentence said "file".
    """
    if _HAS_EXTENSION.search(name):
        return True
    if re.search(r"(file|files|folder|directory)", text, re.IGNORECASE):
        return True
    return len(name.split()) == 1


def _plan(ability: str, args: dict, why: str) -> Plan:
    return Plan(
        steps=[Step(n=1, ability=ability, args=args, why=why)],
        reasoning=f"Matched a deterministic intent rule; no model call needed "
                  f"({why}).",
    )


def match(objective: str) -> Plan | None:
    """Return a plan when the request has one unambiguous reading, else None."""
    text = objective.strip()
    if not text:
        return None

    m = _RECALL.match(text)          # checked before _REMEMBER: "remind me what"
    if m:
        query = m.group("query").strip(" ?.")
        return _plan("recall", {"query": query or text},
                     "explicit request to retrieve a stored fact")

    m = _REMEMBER.match(text)
    if m:
        content = m.group("content").strip(" .")
        if content:
            return _plan("remember", {"content": content},
                         "explicit request to store a fact")

    m = _SYSTEM_STATE.match(text)
    if m:
        return _plan("system_state", {}, "direct query of live machine state")

    m = _LIST_WINDOWS.match(text)
    if m:
        return _plan("list_windows", {}, "direct query of open windows")

    m = _OPEN_APP.match(text)
    if m:
        app = m.group("app").strip().lower()
        if app and app not in _NOT_APPS and len(app.split()) <= 3:
            return _plan("open_app", {"name": app},
                         "unambiguous application launch")

    return _match_file_op(text)


def _join(where: str | None, name: str) -> str:
    """Combine an optional location with a name into one path argument."""
    name = name.strip().strip("\"'")
    if not where:
        return name
    where = where.strip().strip("\"'").rstrip("/\\")
    # "on my desktop" / "in my documents" -> the shorthand LocalOS understands
    lowered = where.lower()
    for word in ("desktop", "documents", "downloads", "pictures"):
        if lowered in (word, f"my {word}", f"the {word}"):
            return f"{word}/{name}"
    return f"{where}/{name}"


_NON_LOCATIONS = {
    "the web", "web", "the internet", "internet", "online", "google",
    "the net", "everywhere", "anywhere",
}
_KNOWN_FOLDERS = ("desktop", "documents", "downloads", "pictures", "videos",
                  "music", "home")


def _looks_like_location(root: str) -> bool:
    """Is this a filesystem place, or something like 'the web'?"""
    low = root.strip().strip("\"'").lower()
    if not low or low in _NON_LOCATIONS:
        return False
    if any(sep in low for sep in ("/", "\\", ":")):
        return True
    if low.startswith("~") or low.startswith("%"):
        return True
    return any(low == f or low.endswith(f" {f}") or low.startswith(f"{f} ")
               for f in _KNOWN_FOLDERS)


def _match_file_op(text: str) -> Plan | None:
    """Deterministic handling of the common single-step file operations."""
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in _TOO_COMPLEX):
        return None      # multi-step or analytical: the planner owns it
    if _SECOND_VERB.search(text):
        return None      # a second action after "and" means a multi-step plan

    m = _MAKE_COPY_NAMED.match(text)
    if m:
        source = m.group("source").strip()
        new = m.group("new").strip()
        parent = source.rsplit("/", 1)[0] if "/" in source else \
            (source.rsplit("\\", 1)[0] if "\\" in source else "")
        dest = f"{parent}/{new}" if parent else new
        return _plan("copy_path", {"source": source, "destination": dest},
                     "copy with an explicit new name")

    m = _RENAME.match(text)
    if m:
        return _plan("rename_path",
                     {"source": m.group("source").strip(),
                      "new_name": m.group("new").strip()},
                     "explicit rename")

    m = _COPY.match(text)
    if m:
        return _plan("copy_path",
                     {"source": m.group("source").strip(),
                      "destination": m.group("dest").strip()},
                     "explicit copy")

    m = _MOVE.match(text)
    if m:
        return _plan("move_path",
                     {"source": m.group("source").strip(),
                      "destination": m.group("dest").strip()},
                     "explicit move")

    m = _WRITE_INTO.match(text)
    if m:
        return _plan("create_file",
                     {"path": m.group("path").strip(),
                      "content": m.group("content")},
                     "write literal content to a named path")

    m = _MK_FILE.match(text)
    if m:
        name = m.group("name").strip()
        # Require something file-like: an extension, or an explicit location.
        if "." in name or m.group("where"):
            return _plan("create_file",
                         {"path": _join(m.group("where"), name),
                          "content": m.group("content").strip()},
                         "create a file with given content")

    m = _MK_FOLDER.match(text)
    if m:
        name = m.group("name").strip()
        if name and len(name) < 120:
            return _plan("create_folder",
                         {"path": _join(m.group("where"), name)},
                         "create a folder")

    for pattern in (_CONTAINS_A, _CONTAINS_B):
        m = pattern.match(text)
        if m:
            query = m.group("query").strip()
            root = m.group("root").strip()
            # "search the web for X" fits this shape but is not a file search.
            if query and _looks_like_location(root):
                return _plan("search_in_files", {"query": query, "root": root},
                             "search file contents in a named location")

    m = _FIND_IN.match(text)
    if m:
        name = m.group("name").strip()
        root = (m.group("root") or "").strip()
        if name and len(name.split()) <= 4 and root:
            return _plan("find_files", {"name": name, "root": root},
                         "locate a file by name within a named location")

    m = _WHERE_IS.match(text)
    if m:
        name = m.group("name").strip()
        if name and len(name.split()) <= 4 and _looks_like_a_filename(name, text):
            return _plan("find_files", {"name": name},
                         "locate a file by name")

    return None
