#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.db.period.sentence.py
# Version:      1.1.0
# Created:      2026-10-12
# Revised:      2026-10-13
# Purpose:      Correct the sentence in each discussion board's intro that
#               names its grading period and final deadline:
#                 "This DB is part of the L14-19 DB Participation grading
#                  period, which is due Thu Oct 29."
#               That sentence is hand-written text carried over from the
#               course copy, so nothing regenerated it when the blocks were
#               re-cut on 2026-10-12.  The block comes from db_paths and the
#               date from the plan file (the FINAL deadline of that block).
#
# Usage:        canvas.api.db.period.sentence.py PLAN [--boards L13,L14,...]
#                 [--apply] [--course-id ID] [--course-config FILE] [--year Y]
#               PLAN is Reports/db.checkpoints.plan_<year>.tsv (the same file
#               canvas.db.checkpoints.py reads).  Dry run unless --apply.
# Safety:       old messages are written to a timestamped JSON first (a topic
#               has no version history); child topics are re-read afterwards.
# ============================================================================
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import canvas_course  # noqa: E402
try:
    import db_paths  # noqa: E402  (reads canvas.course.json at import)
except canvas_course.CourseConfigError as _e:
    sys.exit("[FAIL] %s" % _e)
import canvas_course  # noqa: E402

BASE = COURSE = TOKEN = None   # set in main() from canvas.course.json
YEAR = None
RX = re.compile(r"part of the L\d\d-\d\d DB Participation grading period, "
                r"which is due \w{3} \w{3} \d\d?")


def call(path, method="GET", form=None):
    h = {"Authorization": "Bearer " + TOKEN}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    with urllib.request.urlopen(urllib.request.Request(
            BASE + path, headers=h, method=method, data=data)) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(
        description="Fix each board's 'part of the Lxx-yy grading period, "
                    "due <date>' sentence (dry run by default).")
    ap.add_argument("plan")
    ap.add_argument("--boards", default=None, help="L13,L14,... (default: all in the plan)")
    ap.add_argument("--apply", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()
    global BASE, COURSE, TOKEN, YEAR
    cfg, BASE, TOKEN = canvas_course.setup(a)
    COURSE = cfg.course_id
    YEAR = cfg.year

    # board title prefix -> final due date (local), from the plan
    final = {}
    for ln in open(a.plan):
        if ln.strip() and not ln.startswith("#"):
            f = ln.rstrip("\n").split("\t")
            final[f[0]] = f[3]
    want = set(a.boards.split(",")) if a.boards else None

    topics, url = [], "/api/v1/courses/%s/discussion_topics?per_page=100" % COURSE
    topics = call(url)
    out, backup = [], []
    for prefix, fin in final.items():
        m = [t for t in topics if (t.get("title") or "").startswith(prefix)
             and not re.search(r"\bCopy\s*$", t.get("title") or "")]
        if len(m) != 1:
            sys.exit("%r matches %d boards" % (prefix, len(m)))
        t = m[0]
        label = re.match(r"^L(\d\d)", t["title"]).group(0)
        if want and label not in want:
            continue
        block = db_paths.block_of_title(t["title"])
        lo, hi = db_paths.BLOCK_BOUNDS[block]
        new = "part of the L%02d-%02d DB Participation grading period, which is due %s" % (
            lo, hi, datetime.datetime.strptime(fin, "%Y-%m-%d %H:%M").strftime("%a %b %d"))
        msg = t.get("message") or ""
        mm = RX.search(msg)
        if not mm:
            sys.exit("no period sentence found in %s" % t["title"])
        state = "ok" if mm.group(0) == new else "CHANGE"
        print("%-24s %-6s %s" % (t["title"][:24], state,
              "" if state == "ok" else "%s -> %s" % (mm.group(0)[12:], new[12:])))
        if state == "CHANGE":
            out.append((t, msg.replace(mm.group(0), new, 1), new))
            backup.append({"id": t["id"], "title": t["title"], "message": msg})
    if not a.apply:
        print("\nDRY RUN: %d board(s) would change. Nothing sent." % len(out))
        return 0
    if backup:
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        if YEAR is None:
            sys.exit("[FAIL] no year: pass --year or set \"year\" in canvas.course.json")
        bp = os.path.join(db_paths.shared_reports_dir(str(YEAR)),
                          "db.periodsentence.backup_%s_%s.json" % (YEAR, stamp))
        json.dump(backup, open(bp, "w"), indent=1)
        print("old messages saved: %s" % bp)
    for t, msg, new in out:
        call("/api/v1/courses/%s/discussion_topics/%s" % (COURSE, t["id"]),
             "PUT", {"message": msg})
        # the children mirror the parent a few seconds LATER (async), so
        # poll before calling it a mismatch
        import time
        for _ in range(12):
            d = call("/api/v1/courses/%s/discussion_topics/%s" % (COURSE, t["id"]))
            bad = []
            if new not in (d.get("message") or ""):
                bad.append("parent")
            for k in d.get("group_topic_children") or []:
                c = call("/api/v1/groups/%s/discussion_topics/%s" % (k["group_id"], k["id"]))
                if new not in (c.get("message") or ""):
                    bad.append("child " + str(k["id"]))
            if not bad:
                break
            time.sleep(5)
        print("  %-24s %s" % (t["title"][:24],
              "verified (parent + %d children)" % len(d.get("group_topic_children") or [])
              if not bad else "MISMATCH " + ", ".join(bad)))
        if bad:
            sys.exit("verification failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
