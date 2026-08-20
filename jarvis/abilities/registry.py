"""Ability registry.

Every capability JARVIS has is declared here as an Ability and bound to the
environment that executes it. Adding a new capability is one entry plus a handler
in that environment — the orchestrator, router and planner need no changes. That
is the whole extensibility claim, made concrete.
"""

from __future__ import annotations

from jarvis.core.contracts import Ability, Category, Risk

ABILITIES: list[Ability] = [
    # -- system state ------------------------------------------------------
    Ability(
        id="system_state", category=Category.SYSTEM, environment="local_os",
        objective="Report live CPU, RAM, disk and process usage of this machine.",
        params={}, risk=Risk.LOW, verification="result_only",
        failure_modes=["counters momentarily unavailable"],
    ),
    Ability(
        id="list_processes", category=Category.SYSTEM, environment="local_os",
        objective="List the processes using the most memory right now.",
        params={"top": "how many processes to list (default 12)"},
        risk=Risk.LOW, verification="result_only",
    ),

    # -- category 1: file operations ---------------------------------------
    Ability(
        id="create_folder", category=Category.FILE_OPS, environment="local_os",
        objective="Create a folder (including parent folders) at a given path.",
        params={"path": "folder path to create"}, required=["path"],
        risk=Risk.LOW, verification="dir_exists",
        failure_modes=["permission denied", "invalid path characters"],
        rollback="the folder can be moved aside; deletion is never performed",
    ),
    Ability(
        id="create_file", category=Category.FILE_OPS, environment="local_os",
        objective="Create or overwrite a text file with the given content.",
        params={"path": "file path to write",
                "content": "text to write into the file",
                "append": "true to append instead of overwrite"},
        required=["path"], risk=Risk.MEDIUM, verification="file_exists_with_content",
        failure_modes=["path inside a protected system root", "permission denied"],
        rollback="none automatic; an existing file is overwritten, so this is gated",
    ),
    Ability(
        id="read_file", category=Category.FILE_OPS, environment="local_os",
        objective="Read the text content of a file.",
        params={"path": "file to read", "max_chars": "truncate after N characters"},
        required=["path"], risk=Risk.LOW, verification="result_only",
        failure_modes=["file not found", "binary file returns replacement chars"],
    ),
    Ability(
        id="list_dir", category=Category.FILE_OPS, environment="local_os",
        objective="List the files and folders inside a directory.",
        params={"path": "directory to list (defaults to home)"},
        risk=Risk.LOW, verification="result_only",
    ),
    Ability(
        id="copy_path", category=Category.FILE_OPS, environment="local_os",
        objective="Copy a file or folder to another location.",
        params={"source": "path to copy", "destination": "target path or folder"},
        required=["source", "destination"], risk=Risk.MEDIUM,
        verification="path_moved", failure_modes=["source missing", "target exists"],
        rollback="the copy can be moved aside",
    ),
    Ability(
        id="move_path", category=Category.FILE_OPS, environment="local_os",
        objective="Move a file or folder to another location.",
        params={"source": "path to move", "destination": "target path or folder"},
        required=["source", "destination"], risk=Risk.MEDIUM,
        verification="path_moved", failure_modes=["source missing", "cross-device move"],
        rollback="move it back to the original path",
    ),
    Ability(
        id="rename_path", category=Category.FILE_OPS, environment="local_os",
        objective="Rename a file or folder in place.",
        params={"source": "existing path", "new_name": "new name (not a full path)"},
        required=["source", "new_name"], risk=Risk.MEDIUM,
        verification="path_moved", rollback="rename back to the original name",
    ),

    # -- category 2: file search -------------------------------------------
    Ability(
        id="find_files", category=Category.FILE_SEARCH, environment="local_os",
        objective="Find files whose name contains the given text.",
        params={"name": "full or partial filename to look for",
                "root": "directory to search under (defaults to user folders)"},
        required=["name"], risk=Risk.LOW, verification="result_only",
        failure_modes=["scan cap reached on very large trees"],
    ),
    Ability(
        id="search_in_files", category=Category.FILE_SEARCH, environment="local_os",
        objective="Find text files that contain a given phrase inside them.",
        params={"text": "phrase to search for inside files",
                "root": "directory to search under (defaults to user folders)"},
        required=["text"], risk=Risk.LOW, verification="result_only",
        failure_modes=["text formats only", "scan cap on very large trees"],
    ),

    # -- category 3: application launch and control ------------------------
    Ability(
        id="open_app", category=Category.APP_CONTROL, environment="local_os",
        objective="Launch an application by name and confirm it started.",
        params={"name": "application name, e.g. notepad, calculator, chrome"},
        required=["name"], risk=Risk.LOW, verification="process_running",
        failure_modes=["app not installed", "Store apps that do not expose a process"],
    ),
    Ability(
        id="run_command", category=Category.APP_CONTROL, environment="local_os",
        objective="Run a PowerShell command and return its real exit code and output.",
        params={"command": "the command to run", "timeout": "seconds before giving up"},
        required=["command"], risk=Risk.HIGH, verification="exit_code",
        failure_modes=["destructive commands are refused by guardrails", "timeout"],
    ),

    # -- category 5: GUI automation ----------------------------------------
    Ability(
        id="list_windows", category=Category.GUI_AUTOMATION, environment="windows_gui",
        objective="List the titles of currently open windows.",
        params={}, risk=Risk.LOW, verification="result_only",
    ),
    Ability(
        id="focus_window", category=Category.GUI_AUTOMATION, environment="windows_gui",
        objective="Bring a window to the foreground and confirm it is in front.",
        params={"title": "window title or a distinctive part of it"},
        required=["title"], risk=Risk.LOW, verification="foreground_title",
        failure_modes=["window not open", "OS refuses focus change"],
    ),
    Ability(
        id="read_ui", category=Category.GUI_AUTOMATION, environment="windows_gui",
        objective="Read the accessibility control tree of a window: named buttons, "
                  "fields and menu items with their positions and enabled state.",
        params={"window": "window title or part of it"}, required=["window"],
        risk=Risk.LOW, verification="result_only",
        failure_modes=["control tree capped on very large applications"],
    ),
    Ability(
        id="click_ui", category=Category.GUI_AUTOMATION, environment="windows_gui",
        objective="Activate a named on-screen control (button, menu item, list item).",
        params={"window": "window title or part of it",
                "control": "visible name of the control to activate"},
        required=["window", "control"], risk=Risk.MEDIUM,
        verification="control_invoked",
        failure_modes=["control name not present", "control disabled"],
    ),
    Ability(
        id="type_text", category=Category.GUI_AUTOMATION, environment="windows_gui",
        objective="Type text into a window, refusing unless that window is "
                  "verified to be in the foreground first.",
        params={"window": "target window title or part of it",
                "text": "text to type",
                "control": "optional named field inside the window",
                "press_enter": "true to press Enter afterwards"},
        required=["window", "text"], risk=Risk.MEDIUM,
        verification="foreground_confirmed_before_typing",
        failure_modes=["wrong window in foreground -> refused",
                       "app ignores synthetic keystrokes"],
    ),

    # -- category 6: web / information retrieval ---------------------------
    Ability(
        id="web_search", category=Category.WEB_INFO, environment="web",
        objective="Search the web and return ranked results with URLs.",
        params={"query": "search query", "limit": "how many results"},
        required=["query"], risk=Risk.LOW, verification="results_returned",
        failure_modes=["no network", "search endpoint rate limiting"],
    ),
    Ability(
        id="fetch_page", category=Category.WEB_INFO, environment="web",
        objective="Fetch a web page and extract its readable text.",
        params={"url": "page URL"}, required=["url"], risk=Risk.LOW,
        verification="page_text_extracted",
        failure_modes=["JavaScript-rendered page yields little text", "HTTP error"],
    ),
    Ability(
        id="research", category=Category.WEB_INFO, environment="web",
        objective="Answer a question needing current information: search, read the "
                  "top pages, and synthesise an answer citing its sources.",
        params={"query": "the question to research"}, required=["query"],
        risk=Risk.LOW, verification="results_returned",
        failure_modes=["no network -> answers from model knowledge with a caveat"],
    ),

    # -- memory -------------------------------------------------------------
    Ability(
        id="remember", category=Category.MEMORY, environment="memory",
        objective="Store a fact durably so it can be recalled in a later session.",
        params={"content": "the fact to remember"}, required=["content"],
        risk=Risk.LOW, verification="stored_and_readable",
        rollback="the fact can be forgotten by id",
    ),
    Ability(
        id="recall", category=Category.MEMORY, environment="memory",
        objective="Retrieve previously remembered facts matching a query.",
        params={"query": "what to recall"}, required=["query"],
        risk=Risk.LOW, verification="result_only",
    ),

    # -- git repositories ---------------------------------------------------
    # All read-only. Committing, pushing and checking out are deliberately
    # absent: they need their own verification and risk tier rather than
    # inheriting LOW from their read-only neighbours.
    Ability(
        id="repo_status", category=Category.SYSTEM, environment="repo",
        objective="Report a git repository's branch, changed files and "
                  "how far ahead or behind its upstream it is.",
        params={"path": "repository folder (default: current project)"},
        risk=Risk.LOW, verification="records_returned",
        failure_modes=["not a git repository", "git not installed"],
    ),
    Ability(
        id="repo_log", category=Category.SYSTEM, environment="repo",
        objective="List recent commits with author, age and subject.",
        params={"path": "repository folder", "count": "how many (default 10)"},
        risk=Risk.LOW, verification="records_returned",
    ),
    Ability(
        id="repo_diff", category=Category.SYSTEM, environment="repo",
        objective="Summarise uncommitted changes in a repository.",
        params={"path": "repository folder"},
        risk=Risk.LOW, verification="records_returned",
    ),
    Ability(
        id="repo_branches", category=Category.SYSTEM, environment="repo",
        objective="List local and remote branches of a repository.",
        params={"path": "repository folder"},
        risk=Risk.LOW, verification="records_returned",
    ),
    # -- learned file resolution ----------------------------------------------
    Ability(
        id="resolve_file", category=Category.FILE_SEARCH, environment="local_os",
        objective="Find a file from a vague description like 'my marksheet' or "
                  "'the internship report', using an index of this machine's "
                  "own files ranked by name, folder, type and recency.",
        params={"description": "how the user referred to the file",
                "suffix": "optional type filter such as .pdf"},
        required=["description"], risk=Risk.LOW, verification="result_only",
        failure_modes=["index not built yet", "nothing matches the description"],
    ),

    # -- code execution -------------------------------------------------------
    # The general-purpose primitive: given a problem nothing else covers,
    # write code and run it. HIGH risk always - there is no container, so the
    # only real control is that a human approves the source.
    Ability(
        id="run_python", category=Category.APP_CONTROL, environment="code",
        objective="Write and run a Python script to compute or transform "
                  "something no existing ability covers, and return its output.",
        params={"code": "the Python source to run",
                "timeout": "seconds before giving up (default 30)"},
        required=["code"], risk=Risk.HIGH, verification="exit_code",
        failure_modes=["syntax or runtime error", "timeout",
                       "guardrails refuse destructive source"],
        rollback="none automatic; the script runs in a scratch folder",
    ),
    Ability(
        id="run_powershell", category=Category.APP_CONTROL, environment="code",
        objective="Run a multi-line PowerShell script and return its output.",
        params={"code": "the PowerShell source to run",
                "timeout": "seconds before giving up (default 30)"},
        required=["code"], risk=Risk.HIGH, verification="exit_code",
        failure_modes=["nonzero exit", "timeout", "guardrails refuse source"],
    ),

    # -- documents ----------------------------------------------------------
    # Read-only. Writing documents is a separate, riskier capability: a
    # malformed write silently corrupts something hard to reconstruct.
    Ability(
        id="read_document", category=Category.FILE_SEARCH,
        environment="documents",
        objective="Read the text inside a PDF, Word, Excel, CSV or text file.",
        params={"path": "the document to read",
                "pages": "PDF only: page range like 1-5",
                "sheet": "Excel only: sheet name"},
        required=["path"], risk=Risk.LOW, verification="text_extracted",
        failure_modes=["scanned PDF has no text layer (OCR not implemented)",
                       "password-protected PDF",
                       "spreadsheet never opened in Excel has no cached values"],
    ),
    Ability(
        id="document_info", category=Category.FILE_SEARCH,
        environment="documents",
        objective="Report a document's size, type and whether it can be read.",
        params={"path": "the document to inspect"}, required=["path"],
        risk=Risk.LOW, verification="result_only",
    ),

    # -- hardware -----------------------------------------------------------
    # Reads are LOW; writes are MEDIUM because they are startling rather than
    # destructive - full volume at 2am should ask first.
    Ability(
        id="hardware_status", category=Category.SYSTEM, environment="hardware",
        objective="Report battery, CPU speed, GPU temperature and memory, "
                  "display brightness and audio volume.",
        params={}, risk=Risk.LOW, verification="result_only",
        failure_modes=["no NVIDIA GPU present", "sensor not exposed by firmware"],
    ),
    Ability(
        id="set_volume", category=Category.SYSTEM, environment="hardware",
        objective="Set the system audio volume, or mute and unmute it.",
        params={"percent": "target volume 0-100",
                "mute": "true to mute, false to unmute"},
        risk=Risk.MEDIUM, verification="volume_read_back",
        rollback="set the previous percentage again",
    ),
    Ability(
        id="set_brightness", category=Category.SYSTEM, environment="hardware",
        objective="Set the internal display brightness.",
        params={"percent": "target brightness 0-100"}, required=["percent"],
        risk=Risk.MEDIUM, verification="brightness_read_back",
        failure_modes=["external monitors need DDC/CI, which is unsupported"],
        rollback="set the previous percentage again",
    ),
    Ability(
        id="power_plan", category=Category.SYSTEM, environment="hardware",
        objective="Report the active Windows power plan, or switch to another.",
        params={"plan": "name to switch to; omit to just report"},
        risk=Risk.MEDIUM, verification="result_only",
        rollback="switch back to the previously active plan",
    ),
    Ability(
        id="wifi_status", category=Category.SYSTEM, environment="hardware",
        objective="Report the wireless adapter state, network and signal.",
        params={}, risk=Risk.LOW, verification="result_only",
    ),

    Ability(
        id="repo_search", category=Category.FILE_SEARCH, environment="repo",
        objective="Search the tracked source of a repository for a string, "
                  "skipping build output and ignored files.",
        params={"query": "text to search for", "path": "repository folder"},
        required=["query"], risk=Risk.LOW, verification="records_returned",
    ),
]

_BY_ID = {a.id: a for a in ABILITIES}


def get(ability_id: str) -> Ability | None:
    return _BY_ID.get(ability_id)


def all_abilities() -> list[Ability]:
    return list(ABILITIES)


def by_category(category: Category) -> list[Ability]:
    return [a for a in ABILITIES if a.category == category]


def catalogue_for_prompt() -> str:
    """Compact, model-facing description of every ability and its parameters."""
    lines: list[str] = []
    for ability in ABILITIES:
        params = ", ".join(
            f'"{name}"{"" if name in ability.required else " (optional)"}'
            for name in ability.params
        ) or "no arguments"
        lines.append(f"- {ability.id}: {ability.objective} Args: {params}")
    return "\n".join(lines)
