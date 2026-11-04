#!/usr/bin/env python3
# ============================================================================
# Script:       db_grades_verify.py   (CLI wrapper: canvas.api.db.grades.verify.py)
# Version:      1.0.0
# Created:      2026-11-04
# Purpose:      Read-back check for checkpointed discussion boards: for every
#               student on every board, the PARENT score must equal the sum of
#               the two checkpoint halves.  Read-only unless --repair --apply.
#
# WHY IT EXISTS.  After the L10-L13 upload (2026-10-30), 82 cells (67 students)
# had a Canvas parent score BELOW the sum of their halves.  Nothing said so:
# every PUT returned 200, the upload ledger was clean, and it was found two
# days after the grades were revealed.  A write that succeeds is not evidence
# that the stored result is right; this reads the result back and says so.
#
# WHAT A MISMATCH IS.  abs(parent - sum(non-excused halves that hold a score))
# > 1e-9.  A blank parent counts as 0.  A student whose PARENT submission is
# excused is skipped (nothing to add up); an excused half contributes nothing.
#
# REPAIR (--repair --apply).  What fixed the 82 cells by hand: re-send one
# half UNCHANGED (the reply_to_entry half when it holds a score, else the
# topic half), which makes Canvas recompute the parent.  The upload tool skips
# a half already at its target, so it cannot do this itself.  After repairing,
# everything is read back and checked again; the exit status reflects what is
# STILL wrong, not what was attempted.
#
# EXIT STATUS.  0 = no mismatch.  1 = mismatches remain (or a read failed).
# A check that cannot fail is not a check; --self-test feeds it a known-bad
# state and requires it to be found.
#
# Usage:        canvas.api.db.grades.verify.py BLOCK [--repair [--apply]]
#               canvas.api.db.grades.verify.py --assignments ID[,ID...]
#               canvas.api.db.grades.verify.py --self-test
# ============================================================================

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOL = 1e-9
TOPIC, ENTRY = "reply_to_topic", "reply_to_entry"


# ---------------------------------------------------------------- pure logic
def find_mismatches(state, tol=TOL):
    """state: user_id -> {"parent": score|None, "excused": bool,
    "workflow": str, "halves": {tag: {"score": s|None, "excused": bool}}}.
    Returns a list of dicts, one per student whose parent != sum of halves."""
    bad = []
    for uid, s in state.items():
        if s.get("excused"):
            continue
        total = 0.0
        for h in (s.get("halves") or {}).values():
            if h.get("excused") or h.get("score") is None:
                continue
            total += float(h["score"])
        parent = 0.0 if s.get("parent") is None else float(s["parent"])
        if abs(parent - total) > tol:
            bad.append({"user_id": str(uid), "parent": s.get("parent"),
                        "halves_sum": total, "state": s.get("workflow"),
                        "halves": {t: h.get("score")
                                   for t, h in (s.get("halves") or {}).items()}})
    return bad


def pick_half_to_resend(m):
    """The half to re-send for a mismatch: entry if it holds a score, else
    topic.  None when neither does (nothing to re-send)."""
    for tag in (ENTRY, TOPIC):
        if m["halves"].get(tag) is not None:
            return tag, m["halves"][tag]
    return None


# ------------------------------------------------------------------ Canvas
def _open(base, tok, url, method="GET", data=None):
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(
        url if url.startswith("http") else base + url, data=body,
        method=method, headers={"Authorization": "Bearer " + tok})
    return urllib.request.urlopen(req)


def fetch_state(base, tok, course, aid):
    """One board's state, read with its checkpoint halves."""
    out, url = {}, ("/api/v1/courses/%s/assignments/%s/submissions"
                    "?include[]=sub_assignment_submissions&per_page=100"
                    % (course, aid))
    while url:
        with _open(base, tok, url) as r:
            page, link = json.load(r), r.headers.get("Link", "")
        for s in page:
            out[str(s["user_id"])] = {
                "parent": s.get("score"), "excused": bool(s.get("excused")),
                "workflow": s.get("workflow_state"),
                "halves": {h.get("sub_assignment_tag"):
                           {"score": h.get("score"),
                            "excused": bool(h.get("excused"))}
                           for h in s.get("sub_assignment_submissions") or []}}
        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    return out


def verify(base, tok, course, boards):
    """boards: label -> assignment id.  Returns (mismatches, read_errors)
    where each mismatch carries its board label and assignment id."""
    found, errors = [], []
    for label, aid in boards.items():
        try:
            state = fetch_state(base, tok, course, aid)
        except Exception as e:                       # a failed read is a FAIL
            errors.append("%s (assignment %s): %s" % (label, aid, e))
            continue
        for m in find_mismatches(state):
            m["board"], m["assignment_id"] = label, aid
            found.append(m)
    return found, errors


def repair(base, tok, course, mismatches, apply=False):
    """Re-send one half unchanged for each mismatch.  Returns (sent, skipped)
    lists of strings; with apply False nothing is sent."""
    sent, skipped = [], []
    for m in mismatches:
        pick = pick_half_to_resend(m)
        tag_s = "%s %s student %s" % (m["board"], pick[0] if pick else "-",
                                      m["user_id"])
        if not pick:
            skipped.append(tag_s + ": no half holds a score")
            continue
        if not apply:
            sent.append(tag_s + " (dry run)")
            continue
        try:
            with _open(base, tok, "/api/v1/courses/%s/assignments/%s/"
                       "submissions/%s" % (course, m["assignment_id"],
                                           m["user_id"]), "PUT",
                       {"submission[posted_grade]": pick[1],
                        "sub_assignment_tag": pick[0]}):
                pass
            sent.append(tag_s)
        except urllib.error.HTTPError as e:
            skipped.append("%s: %s" % (tag_s, e.code))
    return sent, skipped


def report(found, errors, out=sys.stdout):
    for e in errors:
        print("  READ FAILED: " + e, file=out)
    for m in found:
        print("  MISMATCH %s student %s: parent %s, halves %s = %s (%s)"
              % (m["board"], m["user_id"], m["parent"], m["halves"],
                 m["halves_sum"], m["state"]), file=out)
    print("verify: %d mismatch(es), %d read failure(s)"
          % (len(found), len(errors)), file=out)


def check_and_report(base, tok, course, boards, do_repair=False, apply=False):
    """The one call tools use.  Returns the number of problems REMAINING."""
    found, errors = verify(base, tok, course, boards)
    report(found, errors)
    if found and do_repair:
        sent, skipped = repair(base, tok, course, found, apply)
        print("repair: %d %s, %d skipped" % (
            len(sent), "re-sent" if apply else "would be re-sent", len(skipped)))
        for s in skipped:
            print("  skipped: " + s)
        if apply:
            found, errors = verify(base, tok, course, boards)
            print("after repair:")
            report(found, errors)
    return len(found) + len(errors)


# --------------------------------------------------------------- self test
def self_test():
    good = {"1": {"parent": 8, "halves": {TOPIC: {"score": 4}, ENTRY: {"score": 4}}},
            "2": {"parent": None, "halves": {TOPIC: {"score": None}, ENTRY: {"score": None}}},
            "3": {"parent": 4, "halves": {TOPIC: {"score": 4}, ENTRY: {"score": 0, "excused": True}}},
            "4": {"parent": 3.6, "halves": {TOPIC: {"score": 1.8}, ENTRY: {"score": 1.8}}},
            "5": {"parent": 99, "excused": True, "halves": {TOPIC: {"score": 1}}}}
    assert find_mismatches(good) == [], find_mismatches(good)
    bad = dict(good)
    bad["6"] = {"parent": 4, "halves": {TOPIC: {"score": 4}, ENTRY: {"score": 4}},
                "workflow": "pending_review"}          # the L10-L13 shape
    bad["7"] = {"parent": 0, "halves": {TOPIC: {"score": 0}, ENTRY: {"score": 4}}}
    bad["8"] = {"parent": 5, "halves": {TOPIC: {"score": None}, ENTRY: {"score": None}}}
    got = sorted(m["user_id"] for m in find_mismatches(bad))
    assert got == ["6", "7", "8"], got
    assert pick_half_to_resend({"halves": {TOPIC: 4, ENTRY: 4}}) == (ENTRY, 4)
    assert pick_half_to_resend({"halves": {TOPIC: 4, ENTRY: None}}) == (TOPIC, 4)
    assert pick_half_to_resend({"halves": {TOPIC: None, ENTRY: None}}) is None
    print("self-test OK (known-bad states are found; good states are not)")
    return 0


# --------------------------------------------------------------------- CLI
def main(argv=None):
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    ap = argparse.ArgumentParser(
        description="Check that every checkpointed board's parent score "
                    "equals the sum of its halves (read-only by default).")
    ap.add_argument("block", nargs="?", help="L13-L18 etc.")
    ap.add_argument("--assignments", default="",
                    help="comma-separated assignment ids instead of a block")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="with --repair: actually re-send (default: dry run)")
    ap.add_argument("--postdir", default=None)
    ap.add_argument("--self-test", action="store_true", dest="selftest")
    import canvas_course
    canvas_course.add_arguments(ap)
    a = ap.parse_args(argv)
    if a.selftest:
        return self_test()
    cfg, base, tok = canvas_course.setup(a)
    boards = {}
    if a.assignments:
        for x in a.assignments.split(","):
            boards["A" + x.strip()] = x.strip()
    elif a.block:
        if cfg.year is None:
            sys.exit("[FAIL] no year: pass --year or set \"year\" in canvas.course.json")
        canvas_course.use_config(cfg.source)
        import db_paths
        postdir = a.postdir or db_paths.posts_dir(str(cfg.year), a.block)
        lo, hi = int(a.block[1:3]), int(a.block[5:7])
        for n in range(lo, hi + 1):
            p = os.path.join(postdir, "db.posts_%s_L%02d.json" % (cfg.year, n))
            if os.path.exists(p):
                snap = json.load(open(p))
                with _open(base, tok, "/api/v1/courses/%s/discussion_topics/%s"
                           % (cfg.course_id, snap["topic_id"])) as r:
                    aid = json.load(r).get("assignment_id")
                if not aid:
                    sys.exit("%s has no assignment" % snap["board"])
                boards[snap["board"]] = str(aid)
    else:
        ap.error("give a BLOCK, --assignments, or --self-test")
    if not boards:
        sys.exit("[FAIL] no boards found -- nothing was checked")
    left = check_and_report(base, tok, cfg.course_id, boards,
                            a.repair, a.apply)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
