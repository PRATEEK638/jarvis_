# JARVIS — start here

A working assistant that does real things on this computer. Type or talk to it;
it plans, acts, checks its own work, and tells you what it actually did.

## Run it

Double-click **`JARVIS.bat`**, or from a terminal in this folder:

```
JARVIS.bat                 interactive session
JARVIS.bat --voice         talk to it, it talks back
JARVIS.bat --status        what is available right now
JARVIS.bat "make a folder called reports on my desktop"
```

First run creates the Python environment and installs dependencies by itself.

## What it can do

| Ask it | It will |
|---|---|
| "make a folder called reports on my desktop" | create it, then confirm it exists |
| "put a file called notes.txt in there saying hello" | write it and read it back |
| "where is budget.xlsx" | search your folders and give the real path |
| "which file in Documents mentions invoice" | grep inside the files |
| "open calculator" | launch it |
| "what windows do i have open" | list them from the real window list |
| "show me the buttons in the Calculator window" | read the actual UI control tree |
| "make a folder called logs then put run.txt inside saying started" | do both steps in order |
| "what is the capital of Japan" | answer, with sources |
| "remember my roll number is 21CS1234" | store it, and recall it days later |

## What it will refuse

Two limits are wired into the code, not the prompt, so no phrasing gets around
them:

- it will never delete your files
- it will never damage Windows

Anything else risky (running a shell command, overwriting a file) asks you first.
`--yes` skips those prompts and every auto-approval is logged.

## Reading the output

After each request you get a **route trace**: whether it ran locally or in the
cloud and why, how long it took, and how many bytes left the machine. Local
requests show `0 bytes left the machine` — that is the privacy claim being shown
rather than asserted.

Every step also shows `verified` or `NOT verified`. Verification re-checks the
real world (does the file exist? does it contain that text?) instead of trusting
the model's own claim of success.

## If something looks wrong

```
JARVIS.bat --status       are the models and microphone available
JARVIS.bat --stats        success rates from past runs
JARVIS.bat --abilities    everything it knows how to do
```

The full event log is `jarvis/data/events.jsonl` — every decision, model call,
tool call and verification, in order.
