"""Report windows: whole calendar days in one timezone.

``--since 2026-10-10 --until 2026-10-16`` means 10 Oct 00:00 to 16 Oct
23:59:59 in the configured timezone (``defaults.timezone``, default
Europe/London). Git, the GitHub API and Bitbucket all have to agree on that:

- git's ``--since=2026-10-10`` (a bare date) takes the *current time of
  day*, so a report run at 18:13 silently started at 18:13 on the 10th and
  dropped everything before it. Git is given full ISO timestamps instead.
- API timestamps are UTC; ``created_at[:10]`` is the UTC date, an hour off
  from the London date during BST. They are converted with ``local_date()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/London"

_FRACTION_RE = re.compile(r"\.(\d+)")


def get_timezone(tz: tzinfo | str | None = None) -> tzinfo:
    """A tzinfo from a name, an existing tzinfo, or the default."""
    if isinstance(tz, tzinfo):
        return tz
    return ZoneInfo(tz or DEFAULT_TIMEZONE)


def parse_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp into an aware datetime.

    Handles GitHub's ``Z`` suffix, git's ``%cI`` offsets and Bitbucket's
    microsecond fractions (Python 3.10's ``fromisoformat`` only accepts
    3- or 6-digit fractions and no ``Z``). Naive timestamps are taken as
    UTC. Returns None for empty, date-only or unparseable input.
    """
    if not ts or len(ts) <= 10:
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_date(ts: str | None, tz: tzinfo | str | None = None) -> str:
    """The calendar date (``YYYY-MM-DD``) of a timestamp in ``tz``.

    Date-only strings come back unchanged; anything unparseable falls back
    to its first ten characters (the old behaviour).
    """
    if not ts:
        return ""
    dt = parse_timestamp(ts)
    if dt is None:
        return ts[:10]
    return dt.astimezone(get_timezone(tz)).date().isoformat()


@dataclass(frozen=True)
class DateWindow:
    """``since``..``until`` inclusive, as whole days in ``tz``."""

    since: str
    until: str
    tz: tzinfo

    @classmethod
    def from_dates(cls, since: str, until: str, tz: tzinfo | str | None = None) -> DateWindow:
        """Raises ValueError if either date is not ``YYYY-MM-DD``."""
        date.fromisoformat(since)
        date.fromisoformat(until)
        return cls(since=since, until=until, tz=get_timezone(tz))

    @property
    def start(self) -> datetime:
        """Midnight at the start of ``since`` (inclusive)."""
        return datetime.combine(date.fromisoformat(self.since), time(0), tzinfo=self.tz)

    @property
    def end(self) -> datetime:
        """Midnight after ``until`` (exclusive)."""
        nxt = date.fromisoformat(self.until) + timedelta(days=1)
        return datetime.combine(nxt, time(0), tzinfo=self.tz)

    def git_args(self) -> list[str]:
        """``--since``/``--until`` for git log/rev-list.

        Both bounds are inclusive in git, so the end is the last second of
        ``until`` rather than the following midnight.
        """
        last = self.end - timedelta(seconds=1)
        return [
            f"--since={self.start.isoformat(timespec='seconds')}",
            f"--until={last.isoformat(timespec='seconds')}",
        ]

    @property
    def api_since(self) -> str:
        """UTC date on or before the window start, for API ``since`` filters.

        Deliberately a superset (APIs that take a bare date treat it as
        00:00 UTC); the exact cut is made locally with ``local_date()``.
        """
        return self.start.astimezone(timezone.utc).date().isoformat()

    @property
    def api_until(self) -> str:
        """UTC date on or after the window end (superset, as ``api_since``)."""
        last = self.end - timedelta(seconds=1)
        return last.astimezone(timezone.utc).date().isoformat()

    def contains(self, ts: str | None) -> bool:
        d = local_date(ts, self.tz)
        return bool(d) and self.since <= d <= self.until


def git_window_args(since: str, until: str, tz: tzinfo | str | None = None) -> list[str]:
    """Git ``--since``/``--until`` arguments for a whole-day window.

    Falls back to passing the strings through when they aren't plain
    dates, so callers with a relative date ("2 weeks ago") keep working.
    """
    try:
        return DateWindow.from_dates(since, until, tz).git_args()
    except ValueError:
        return [f"--since={since}", f"--until={until}"]
