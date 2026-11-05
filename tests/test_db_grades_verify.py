"""Offline tests for db_grades_verify: known-bad states must be found."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import db_grades_verify as V


def test_self_test_passes():
    assert V.self_test() == 0


def test_the_l10_l13_shape_is_caught():
    # parent kept one half (4) while both halves hold 4: the 82-cell failure
    st = {"9": {"parent": 4, "workflow": "pending_review",
                "halves": {V.TOPIC: {"score": 4}, V.ENTRY: {"score": 4}}}}
    assert [m["user_id"] for m in V.find_mismatches(st)] == ["9"]


def test_consistent_and_blank_are_not_flagged():
    st = {"1": {"parent": 8, "halves": {V.TOPIC: {"score": 4}, V.ENTRY: {"score": 4}}},
          "2": {"parent": None, "halves": {V.TOPIC: {"score": None}, V.ENTRY: {"score": None}}}}
    assert V.find_mismatches(st) == []


def test_a_comment_visible_to_another_student_is_a_leak():
    st = {"1": ["Good.  -- Selena"], "2": ["Good.  -- Selena"]}
    leaks, missing = V.find_comment_leaks(st, [("1", "Good.  -- Selena")])
    assert leaks == [("2", "Good.  -- Selena")] and missing == []


def test_a_missing_comment_and_a_clean_board():
    leaks, missing = V.find_comment_leaks({"1": ["a"], "2": []}, [("1", "a"), ("2", "a")])
    assert leaks == [] and missing == [("2", "a")]
    assert V.find_comment_leaks({"1": ["a"], "2": ["b"]}, [("1", "a")]) == ([], [])
