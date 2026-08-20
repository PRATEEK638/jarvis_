"""Capability gap detection.

A small local model asked to do something outside its ability set does not
reliably say "I can't". Observed behaviour: asked to send an email, llama3:8b
planned `create_file` instead — a plausible-looking substitute that satisfies no
part of the actual request. Prompt instructions alone did not prevent it.

So coverage is checked deterministically, before planning. If the request clearly
asks for an action class no registered ability provides, the run stops and says
so, naming what is missing. This is cheap, cannot be talked around by a model,
and each new gap is one entry in the table below.

Extension path: when an ability for one of these actions is registered, delete
its entry here.
"""

from __future__ import annotations

import re

# action class -> (patterns that signal it, what would be needed to support it)
_UNSUPPORTED: list[tuple[str, list[str], str]] = [
    (
        "sending email",
        [r"\bsend (an? )?(e-?mail|mail)\b",
         r"\be-?mail (it |them |him |her |me )?to\b",
         r"\breply to (the )?(e-?mail|mail)\b", r"\bcompose an? e-?mail\b",
         r"\bmail (this|that|it) to\b"],
        "an email provider integration (SMTP or a Gmail/Outlook API credential) "
        "plus an approval gate for outbound messages",
    ),
    (
        "sending chat or SMS messages",
        [r"\bsend (a )?(whats-?app|message|text|sms|dm)\b", r"\bwhats-?app\b.*\b(send|message)\b",
         r"\bmessage (him|her|them|my)\b", r"\btext (him|her|them)\b",
         r"\bsend.*\bon (slack|teams|telegram|discord)\b"],
        "a messaging integration (WhatsApp Web session, Slack/Teams API token) "
        "plus an approval gate for outbound messages",
    ),
    (
        "posting to social media",
        [r"\b(tweet|post)\b.*\b(twitter|x\.com|instagram|facebook|linkedin|reddit)\b",
         r"\bpost (this|that|it) (on|to)\b", r"\bupload.*\b(youtube|instagram)\b"],
        "an authenticated social-platform API and a publishing approval gate",
    ),
    (
        "calendar and meeting scheduling",
        [r"\bschedule (a )?(meeting|call|event)\b", r"\bcalendar invite\b",
         r"\badd (this |it )?to my calendar\b", r"\bbook (a )?(meeting|room|slot)\b"],
        "a calendar API integration (Google Calendar or Outlook)",
    ),
    (
        "payments and purchases",
        [r"\b(pay|purchase|buy|order)\b.*\b(online|with my card|from amazon)\b",
         r"\btransfer (money|funds|rupees|\$)\b", r"\bmake a payment\b"],
        "payment authorisation, which is intentionally out of scope for an "
        "autonomous agent",
    ),
    (
        "deleting files",
        [r"\bdelete\b", r"\bremove\b.*\bfile\b", r"\berase\b", r"\bwipe\b",
         r"\bempty the recycle bin\b", r"\btrash (this|that|these)\b"],
        "nothing — deletion is permanently and deliberately blocked. Files can be "
        "moved or renamed instead",
    ),
    (
        "printing",
        [r"\bprint (this|that|it|the)\b", r"\bsend.*\bto the printer\b"],
        "a printer/spooler integration",
    ),
    (
        "installing or uninstalling software",
        [r"\b(install|uninstall) (?!python\b)(?!the package\b)\w+",
         r"\bwinget install\b", r"\bpip install\b"],
        "a package-manager integration with an explicit elevation and approval path",
    ),
    (
        "editing images, audio or video",
        [r"\b(crop|resize|edit)\b.*\b(image|photo|picture|video)\b",
         r"\bconvert.*\b(mp4|mp3|wav|png|jpg)\b.*\bto\b",
         r"\b(trim|cut)\b.*\bvideo\b"],
        "a media-processing tool such as ffmpeg or Pillow, exposed as an ability",
    ),
]

_COMPILED = [
    (name, [re.compile(p, re.IGNORECASE) for p in pats], need)
    for name, pats, need in _UNSUPPORTED
]


def detect_gap(objective: str) -> str | None:
    """Return an honest explanation if the request needs an unregistered action.

    None means "nothing obviously out of scope" — not a guarantee of success,
    just that no known gap was matched.
    """
    text = objective.strip()
    for name, patterns, requirement in _COMPILED:
        for pattern in patterns:
            if pattern.search(text):
                return (
                    f"I can't do that: {name} is not one of my registered "
                    f"capabilities, so I won't pretend otherwise or substitute a "
                    f"different action.\n\nWhat it would take: {requirement}."
                )
    return None
