---
status: done
since: 2026-10-08
created: 2026-09-28
---
# The strip shows the time

## Goal
A clock at the right end of the strip, so a full-screen app doesn't hide
what time it is.

## Before
- [x] Decided 2026-09-30: a setting turns it off, on by default. The
  question was: is the clock always there?
- [x] Decided 2026-10-07: the locale's (`2:05 PM`), with a setting for
  24-hour (`14:05`). The question was: 24-hour or the locale's?

## Notes
- The strip redraws on its own already when a setting changes; a timer
  each minute, on the minute, is all the clock needs.
- Narrow terminals: the clock goes before the help hint does.
- The plan: a clock in the locale's format at the strip's right end,
  redrawn on the minute; one setting turns it off, another makes it
  24-hour.
- 2026-10-08, built. keyline has `right=` text at the line's right end,
  shown only while every hint fits, so it goes first (eeb6ceb). hill's
  strip ends with the time, redrawn just after each minute, and
  Hill → Clock is one setting with four choices instead of two: the
  locale's (the default), 12-hour, 24-hour, off (96949de).
  - The locale's format is LC_TIME's. On macOS every locale writes
    24-hour, whatever the system says, so 12-hour is a choice of its own;
    reading macOS's own setting would be a second way for one platform.
- Done: the strip shows the time, Hill → Clock sets its format or turns
  it off, and `tests/test_clock.py` and the strip test check both.

## Done when
- The clock shows in the strip, follows its setting, and the tests check
  both.
