"""Smart quick-add parsing (opt-in, sec-M3).

parse(text, today) pulls a due date and a priority out of a free-text task line
and returns the cleaned title alongside them. It understands English and Russian
date words (the UI is English, but the human types dates in either), a couple of
numeric date formats, and Todoist-style `!1..!3` priority flags.

Pure and side-effect free — the POST /tasks route calls it only when the quick-add
form opts in with smart=1, so a plain title is always safe to type.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# Todoist/TickTick convention: !1 is the MOST urgent. This app stores priority as
# 0..3 with 3 = high, so the flag number inverts onto the stored level.
_PRIO = {"1": 3, "2": 2, "3": 1}

# relative day words (English + Russian)
_REL = {
    "today": 0, "tomorrow": 1,
    "сегодня": 0, "завтра": 1, "послезавтра": 2,
}

# weekday words -> Python weekday() index (Mon=0 .. Sun=6), English + Russian
_WD = {
    "mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
    "пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2,
    "чт": 3, "четверг": 3, "пт": 4, "пятница": 4, "сб": 5, "суббота": 5,
    "вс": 6, "воскресенье": 6,
}

_PRIO_RE = re.compile(r"(?:^|\s)!([1-3])(?=\s|$)")
_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b")


def parse(text: str, today: str | date | None = None) -> dict:
    """{'title', 'due_date' (iso|None), 'priority' (0..3)} from a free-text line."""
    if isinstance(today, str):
        today = date.fromisoformat(today)
    elif today is None:
        today = date.today()

    priority = 0
    due: str | None = None

    def _take_prio(m: re.Match) -> str:
        nonlocal priority
        priority = _PRIO[m.group(1)]
        return " "

    text = _PRIO_RE.sub(_take_prio, text)

    def _take_iso(m: re.Match) -> str:
        nonlocal due
        try:
            due = date.fromisoformat(m.group(0)).isoformat()
            return " "
        except ValueError:
            return m.group(0)

    text = _ISO_RE.sub(_take_iso, text, count=1)

    if due is None:
        def _take_dmy(m: re.Match) -> str:
            nonlocal due
            try:
                y = int(m.group(3)) if m.group(3) else today.year
                due = date(y, int(m.group(2)), int(m.group(1))).isoformat()
                return " "
            except ValueError:
                return m.group(0)
        text = _DMY_RE.sub(_take_dmy, text, count=1)

    if due is None:
        words = text.split()
        for i, w in enumerate(words):
            key = w.lower().strip(".,;:!?")
            if key in _REL:
                due = (today + timedelta(days=_REL[key])).isoformat()
                words[i] = ""
                break
            if key in _WD:
                due = (today + timedelta(days=(_WD[key] - today.weekday()) % 7)).isoformat()
                words[i] = ""
                break
        text = " ".join(w for w in words if w)

    return {"title": re.sub(r"\s+", " ", text).strip(), "due_date": due, "priority": priority}
