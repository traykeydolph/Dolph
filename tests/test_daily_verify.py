"""daily_verify streak logic — the consecutive-clean-day counter toward 30.

The stateful part (Alpaca/DB checks) is integration-tested by running the tool;
this pins the pure streak math, which is what makes the 30-day count trustworthy
and idempotent.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_verify import compute_streak


def _h(day, verdict):
    return {"day": day, "verdict": verdict}


def test_empty_history_is_zero():
    assert compute_streak([]) == 0


def test_counts_consecutive_clean_from_the_most_recent_day():
    hist = [_h("2026-07-20", "CLEAN"), _h("2026-07-21", "DIRTY"),
            _h("2026-07-22", "CLEAN"), _h("2026-07-23", "CLEAN")]
    assert compute_streak(hist) == 2   # 22 + 23, stops at 21's DIRTY


def test_a_dirty_latest_day_resets_to_zero():
    assert compute_streak([_h("2026-07-22", "CLEAN"), _h("2026-07-23", "DIRTY")]) == 0


def test_all_clean_counts_all():
    hist = [_h(f"2026-07-{d:02d}", "CLEAN") for d in range(10, 25)]
    assert compute_streak(hist) == 15


def test_order_independent_uses_dates_not_list_order():
    # deliberately shuffled input — streak walks by DATE, newest first
    hist = [_h("2026-07-23", "CLEAN"), _h("2026-07-20", "CLEAN"),
            _h("2026-07-22", "CLEAN"), _h("2026-07-21", "DIRTY")]
    assert compute_streak(hist) == 2   # 22, 23 clean; 21 dirty


def test_reaching_the_goal():
    hist = [_h(f"2026-{m:02d}-{d:02d}", "CLEAN")
            for m in (6, 7) for d in range(1, 16)]
    assert compute_streak(hist) >= 30
