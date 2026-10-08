from datetime import datetime

import hill_ops.clock
from hill_ops.clock import clock_text, to_next_minute

AFTERNOON = datetime(2026, 10, 8, 14, 5)
NIGHT = datetime(2026, 10, 8, 0, 7)


def test_24_hour_and_12_hour():
    assert clock_text("24-hour", AFTERNOON) == "14:05"
    assert clock_text("24-hour", NIGHT) == "00:07"
    assert clock_text("12-hour", AFTERNOON) == "2:05 PM"
    assert clock_text("12-hour", NIGHT) == "12:07 AM"


def test_the_locales_follows_whether_it_writes_12_hour(monkeypatch):
    monkeypatch.setattr(hill_ops.clock, "twelve_hour", lambda: False)
    assert clock_text("locale", AFTERNOON) == "14:05"
    monkeypatch.setattr(hill_ops.clock, "twelve_hour", lambda: True)
    assert clock_text("locale", AFTERNOON).startswith("2:05 ")


def test_off_shows_nothing():
    assert clock_text("off", AFTERNOON) == ""


def test_the_next_tick_is_just_after_the_minute():
    assert 0.05 < to_next_minute(1_000_000_000.0) - 20 < 0.06  # 40 s past a minute
