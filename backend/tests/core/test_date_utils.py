from datetime import date, datetime

import pytest

from app.core.date_utils import add_months, parse_date_lenient


class TestParseDateLenient:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2025-06-02", date(2025, 6, 2)),
            ("2025-06-02T10:30:00", date(2025, 6, 2)),
            ("02.06.2025", date(2025, 6, 2)),
            ("2.6.2025", date(2025, 6, 2)),
            ("02.06.25", date(2025, 6, 2)),
            ("02/06/2025", date(2025, 6, 2)),
            ("2. Juni 2025", date(2025, 6, 2)),
            ("02. Jun. 2025", date(2025, 6, 2)),
            ("1. März 2024", date(2024, 3, 1)),
            ("  15.01.2024 ", date(2024, 1, 15)),
        ],
    )
    def test_accepted_formats(self, raw, expected):
        assert parse_date_lenient(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "Anfang Juni", "31.02.2024", "2. Foo 2025", 42])
    def test_unparseable_returns_none(self, raw):
        assert parse_date_lenient(raw) is None

    def test_date_objects_pass_through(self):
        assert parse_date_lenient(date(2024, 1, 1)) == date(2024, 1, 1)
        assert parse_date_lenient(datetime(2024, 1, 1, 12, 0)) == date(2024, 1, 1)


class TestAddMonths:
    @pytest.mark.parametrize(
        ("start", "months", "expected"),
        [
            (date(2024, 1, 15), 24, date(2026, 1, 15)),
            (date(2024, 1, 31), 1, date(2024, 2, 29)),  # Schaltjahr, auf Monatsende geklemmt
            (date(2023, 1, 31), 1, date(2023, 2, 28)),
            (date(2024, 11, 30), 2, date(2025, 1, 30)),
            (date(2024, 12, 31), 12, date(2025, 12, 31)),
        ],
    )
    def test_add_months(self, start, months, expected):
        assert add_months(start, months) == expected
