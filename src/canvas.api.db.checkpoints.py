#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.db.checkpoints.py
# Version:      1.1.0
# Created:      2026-10-12
# Revised:      2026-10-13
# Purpose:      Set the points and due dates of the two checkpoints of each
#               discussion board (reply_to_topic = the questions, reply_to_entry
#               = the responses), and publish boards, from a plan file.  Shows
#               a before/after diff first; writes nothing without --apply.
#
# WHY THIS EXISTS.  The boards are CHECKPOINTED discussions.  REST reads the
# checkpoints (assignments/<id>?include[]=checkpoints) but silently IGNORES a
# checkpoints parameter on PUT: the PUT returns 200 and nothing changes (tested
# 2026-10-12).  Only the GraphQL updateDiscussionTopic mutation writes them.
# The September reweighting was done by hand; this makes it a reviewable plan.
#
# PLAN FILE (TSV, # comments): title_prefix  total_points  initial  final
#   title_prefix   how the board's Canvas title STARTS ("L14:" or
#                  "L13 - DB Group 13-18"); must match exactly one board
#   total_points   split equally between the two halves (3 -> 1.5 + 1.5)
#   initial/final  Eastern local "YYYY-MM-DD HH:MM" (DST handled here)
#
# A GraphQL write also needs assignment.forCheckpoints AND setCheckpoints: sent
# with checkpoints alone, the mutation reports no errors and changes nothing.
#
# THE GRADE-UNPOSTING TRAP.  Changing checkpoint points on a board that already
# has grades UN-POSTED 296 of 302 L01-L04 grades on 2026-09-23.  This refuses
# any board with a non-blank score unless --allow-graded, and tells you to take
# a canvas.gradebook.snapshot.py --compare around the run.
#
# Usage:        canvas.api.db.checkpoints.py PLAN [--apply] [--publish]
#                 [--allow-graded] [-v] [--course-id ID] [--course-config FILE]
# Options:      --publish   also publish every board in the plan that is not
#                           published (notifyUsers false: no notification)
# ============================================================================
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zoneinfo

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import canvas_course  # noqa: E402

BASE = COURSE = TOKEN = None   # set in main() from canvas.course.json
ET = zoneinfo.ZoneInfo("America/New_York")


def req(path, method="GET", body=None, form=False):
    h = {"Authorization": "Bearer " + TOKEN}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, headers=h, method=method,
                               data=data)
    try:
        with urllib.request.urlopen(r) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit("HTTP %s on %s %s: %s" % (e.code, method, path,
                                           e.read().decode()[:300]))


def get_all(path):
    out, url = [], BASE + path
    h = {"Authorization": "Bearer " + TOKEN}
    while url:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h)) as resp:
            out += json.load(resp)
            url = None
            for p in (resp.headers.get("Link") or "").split(","):
                if 'rel="next"' in p:
                    url = p[p.index("<") + 1:p.index(">")]
    return out


def to_utc(local):
    d = datetime.datetime.strptime(local.strip(), "%Y-%m-%d %H:%M")
    return (d.replace(tzinfo=ET).astimezone(datetime.timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%SZ"))


def to_local(iso):
    if not iso:
        return "-"
    d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d.astimezone(ET).strftime("%m-%d %H:%M")


def read_plan(path):
    rows = []
    for ln in open(path):
        ln = ln.rstrip("\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = [x.strip() for x in ln.split("\t")]
        if len(f) != 4:
            sys.exit("plan line needs 4 tab-separated fields: %r" % ln)
        rows.append({"prefix": f[0], "total": float(f[1]),
                     "initial": f[2], "final": f[3]})
    return rows


def current(topic):
    a = req("/api/v1/courses/%s/assignments/%s?include[]=checkpoints"
            % (COURSE, topic["assignment_id"]))
    cps = {c["tag"]: c for c in a.get("checkpoints") or []}
    return a, cps


def graded_count(assignment_id):
    n = 0
    for s in get_all("/api/v1/courses/%s/assignments/%s/submissions"
                     "?per_page=100" % (COURSE, assignment_id)):
        if s.get("score") is not None:
            n += 1
    return n


MUT = """mutation U($input: UpdateDiscussionTopicInput!) {
  updateDiscussionTopic(input: $input) {
    discussionTopic { _id title published }
    errors { message attribute }
  }
}"""


def gql(inp):
    doc = req("/api/graphql", "POST", {"query": MUT, "variables": {"input": inp}})
    if doc.get("errors"):
        sys.exit("GraphQL: %s" % json.dumps(doc["errors"])[:400])
    res = doc["data"]["updateDiscussionTopic"]
    if res.get("errors"):
        sys.exit("updateDiscussionTopic: %s" % json.dumps(res["errors"])[:400])
    return res["discussionTopic"]


def main():
    ap = argparse.ArgumentParser(
        description="Set discussion board checkpoint points/dates and "
                    "publish, from a plan file (dry run by default).")
    ap.add_argument("plan")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--allow-graded", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()
    global BASE, COURSE, TOKEN
    cfg, BASE, TOKEN = canvas_course.setup(a)
    COURSE = cfg.course_id

    plan = read_plan(a.plan)
    topics = [t for t in get_all("/api/v1/courses/%s/discussion_topics"
                                 "?per_page=100" % COURSE)
              if not re.search(r"\bCopy\s*$", t.get("title") or "")]
    # a title that only matches because of the unfinished duplicate would be
    # ambiguous; the duplicate is excluded above by name
    changes, problems = [], []
    for row in plan:
        m = [t for t in topics if (t.get("title") or "").startswith(row["prefix"])]
        if len(m) != 1:
            problems.append("%r matches %d boards" % (row["prefix"], len(m)))
            continue
        t = m[0]
        asg, cps = current(t)
        want = {
            "reply_to_topic": (row["total"] / 2, to_utc(row["initial"])),
            "reply_to_entry": (row["total"] / 2, to_utc(row["final"])),
        }
        diffs = []
        for tag, (pts, due) in want.items():
            c = cps.get(tag) or {}
            if (c.get("points_possible") != pts or c.get("due_at") != due):
                diffs.append((tag, c.get("points_possible"), pts,
                              c.get("due_at"), due))
        pub = (not t.get("published")) and a.publish
        changes.append((row, t, asg, diffs, pub))

    for row, t, asg, diffs, pub in changes:
        print("%-24s total %-4s -> %-4s %s" % (
            row["prefix"][:24], asg.get("points_possible"), row["total"],
            "PUBLISH" if pub else ""))
        for tag, p0, p1, d0, d1 in diffs:
            print("    %-15s pts %-4s -> %-4s   due %s -> %s" % (
                tag, p0, p1, to_local(d0), to_local(d1)))
        if not diffs and not pub:
            print("    (no change)")
    if problems:
        sys.exit("REFUSING: " + "; ".join(problems))

    todo = [c for c in changes if c[3] or c[4]]
    if not a.apply:
        print("\nDRY RUN: %d board(s) would change. Nothing sent." % len(todo))
        return 0

    for row, t, asg, diffs, pub in todo:
        if diffs and not a.allow_graded:
            n = graded_count(t["assignment_id"])
            if n:
                sys.exit("REFUSING %s: %d submission(s) already carry scores; "
                         "changing checkpoint points can un-post grades. "
                         "Snapshot, then --allow-graded." % (row["prefix"], n))
        inp = {"discussionTopicId": str(t["id"])}
        if diffs:
            # BOTH of these are required: with checkpoints alone the mutation
            # answers "no errors" and changes nothing (tested 2026-10-12).
            inp["setCheckpoints"] = True
            inp["assignment"] = {"forCheckpoints": True,
                                 "pointsPossible": row["total"]}
            inp["checkpoints"] = [{
                "checkpointLabel": tag,
                "pointsPossible": pts,
                "dates": [{"type": "everyone", "dueAt": due}],
                **({"repliesRequired": 2} if tag == "reply_to_entry" else {}),
            } for tag, (pts, due) in
                {"reply_to_topic": (row["total"] / 2, to_utc(row["initial"])),
                 "reply_to_entry": (row["total"] / 2, to_utc(row["final"]))}.items()]
        if pub:
            inp["published"] = True
            inp["notifyUsers"] = False
        res = gql(inp)
        # verify by re-reading, never by trusting the mutation's answer
        asg2, cps2 = current(t)
        ok = all(cps2.get(tag, {}).get("points_possible") == p1
                 and cps2.get(tag, {}).get("due_at") == d1
                 for tag, _p0, p1, _d0, d1 in diffs)
        t2 = req("/api/v1/courses/%s/discussion_topics/%s" % (COURSE, t["id"]))
        if pub:
            ok = ok and bool(t2.get("published"))
        print("  %-24s %s" % (row["prefix"][:24],
                              "verified" if ok else "MISMATCH after write"))
        if not ok:
            sys.exit("verification failed on %s" % row["prefix"])
    print("\napplied %d board(s)." % len(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
