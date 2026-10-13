# Tests for canvas.api.gradebook.snapshot.py -- no Canvas access.
#
# The one property that matters: a score of 0 and a blank must never be
# confused.  v1.0.0's first draft dropped every 0 while trimming empty
# fields (0 == False in Python), and only --compare caught it.

import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "snap", os.path.join(os.path.dirname(HERE), "src", "canvas.api.gradebook.snapshot.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def _snap(score, half_score):
    raw = {"user_id": 1, "assignment_id": 9, "score": score, "excused": False,
           "sub_assignment_submissions": [
               {"sub_assignment_tag": "reply_to_topic", "score": half_score}]}
    rec = S.slim(raw, S.SUB_KEEP)
    rec["halves"] = [S.slim(h, S.HALF_KEEP)
                     for h in raw["sub_assignment_submissions"]]
    return {"assignments": [{"id": 9, "name": "L01"}], "submissions": [rec]}


def test_zero_survives_slimming():
    c = S.cells(_snap(0.0, 0.0))
    assert c[(1, 9, None)] == 0.0
    assert c[(1, 9, "reply_to_topic")] == 0.0


def test_blank_stays_blank():
    c = S.cells(_snap(None, None))
    assert c[(1, 9, None)] == ""
    assert c[(1, 9, "reply_to_topic")] == ""


def test_compare_flags_zero_becoming_blank(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_snap(0.0, 0.0)))
    b.write_text(json.dumps(_snap(None, None)))
    assert S.compare(str(a), str(b)) == 1
    assert "0.0 -> ''" in capsys.readouterr().out


def test_compare_passes_identical(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_snap(4.0, None)))
    assert S.compare(str(a), str(a)) == 0
