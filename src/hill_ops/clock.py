"""The strip's clock: the time in the locale's format, or 12- or 24-hour."""

from __future__ import annotations

import locale
import time
from datetime import datetime

LOCALE = "locale"
H12 = "12-hour"
H24 = "24-hour"
OFF = "off"


def twelve_hour() -> bool:
    """Whether the locale (LC_TIME) writes the time 12-hour, as en_US does on
    Linux. macOS's locales all write it 24-hour, whatever the system's
    setting, so there hill-ops → Clock → 12-hour is the way to it."""
    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        return False
    return any(code in locale.nl_langinfo(locale.T_FMT) for code in ("%I", "%l", "%r", "%p"))


def clock_text(form: str, now: datetime | None = None) -> str:
    """The time as the strip shows it: "14:05", or "2:05 PM" (the locale's
    word for PM, else PM); empty when the clock is off."""
    if form == OFF:
        return ""
    now = now or datetime.now()
    if form == H24 or (form == LOCALE and not twelve_hour()):
        return f"{now:%H:%M}"
    pm = now.hour >= 12
    word = (locale.nl_langinfo(locale.PM_STR if pm else locale.AM_STR) if form == LOCALE else "") or ("PM" if pm else "AM")
    return f"{now.hour % 12 or 12}:{now:%M} {word}"


def to_next_minute(now: float | None = None) -> float:
    """Seconds to the next minute, and a little more, so the clock changes
    just after it does."""
    now = time.time() if now is None else now
    return 60 - now % 60 + 0.05
