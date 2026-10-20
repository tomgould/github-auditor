from __future__ import annotations

import pytest

from auditer.timewindow import DateWindow, git_window_args, local_date, parse_timestamp


class TestLocalDate:
    def test_utc_late_evening_in_bst_is_next_day(self):
        # 23:30 UTC on 9 Oct is 00:30 BST on 10 Oct
        assert local_date("2026-10-09T23:30:00Z") == "2026-10-10"

    def test_utc_late_evening_in_gmt_is_same_day(self):
        # After the clocks go back (25 Oct 2026) London is UTC
        assert local_date("2026-11-09T23:30:00Z") == "2026-11-09"

    def test_git_offset_is_converted(self):
        # A committer in New York at 22:00 on the 16th: 03:00 on the 17th in London
        assert local_date("2026-10-16T22:00:00-04:00") == "2026-10-17"

    def test_bitbucket_microsecond_fraction(self):
        assert local_date("2026-10-09T23:30:00.123456+00:00") == "2026-10-10"
        # Python 3.10's fromisoformat rejects other fraction lengths
        assert local_date("2026-10-09T23:30:00.12+00:00") == "2026-10-10"

    def test_other_timezone(self):
        assert local_date("2026-10-09T23:30:00Z", "UTC") == "2026-10-09"
        assert local_date("2026-10-10T02:00:00Z", "America/New_York") == "2026-10-09"

    def test_date_only_and_empty(self):
        assert local_date("2026-10-10") == "2026-10-10"
        assert local_date("") == ""
        assert local_date(None) == ""

    def test_unparseable_falls_back_to_prefix(self):
        assert local_date("2026-10-10 garbage here") == "2026-10-10"

    def test_naive_timestamp_taken_as_utc(self):
        assert parse_timestamp("2026-10-09T23:30:00").utcoffset().total_seconds() == 0


class TestDateWindow:
    def test_git_args_are_whole_days_in_bst(self):
        w = DateWindow.from_dates("2026-10-10", "2026-10-16")
        assert w.git_args() == [
            "--since=2026-10-10T00:00:00+01:00",
            "--until=2026-10-16T23:59:59+01:00",
        ]

    def test_git_args_follow_the_clock_change(self):
        # Window spanning the end of BST (25 Oct 2026)
        w = DateWindow.from_dates("2026-10-24", "2026-10-26")
        assert w.git_args() == [
            "--since=2026-10-24T00:00:00+01:00",
            "--until=2026-10-26T23:59:59+00:00",
        ]

    def test_api_dates_are_a_utc_superset(self):
        w = DateWindow.from_dates("2026-10-10", "2026-10-16")
        # 00:00 BST on the 10th is 23:00 UTC on the 9th
        assert w.api_since == "2026-10-09"
        assert w.api_until == "2026-10-16"

    def test_contains_uses_local_days(self):
        w = DateWindow.from_dates("2026-10-10", "2026-10-16")
        assert w.contains("2026-10-09T23:30:00Z")       # 00:30 BST on the 10th
        assert not w.contains("2026-10-09T22:59:59Z")   # 23:59 BST on the 9th
        assert w.contains("2026-10-16T22:59:59Z")       # 23:59 BST on the 16th
        assert not w.contains("2026-10-16T23:00:00Z")   # 00:00 BST on the 17th
        assert not w.contains(None)

    def test_rejects_non_dates(self):
        with pytest.raises(ValueError):
            DateWindow.from_dates("10/10/2026", "2026-10-16")

    def test_git_window_args_passes_relative_dates_through(self):
        assert git_window_args("2 weeks ago", "now") == ["--since=2 weeks ago", "--until=now"]
