#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.group.grades.post.py
# Version:      1.2.0
# Created:      2026-10-06
# Revised:      2026-10-13 -- course-agnostic: course id, base URL and token come
#               from canvas.course.json (canvas_course.py); --course-id/--course-config.
# Purpose:      Post a group homework's grades and per-team comments to Canvas,
#               from the package built by grading.canvas.package.from.final.py,
#               and verify what Canvas now holds.  Dry run is the default.
#
# What --apply does, in this order (nothing is skipped silently):
#   1. Assignment: points_possible = the package total, grading_type = percent
#      (MG's setup: raw points are posted and Canvas shows the percentage).
#   2. Post policy -> manual (GraphQL; the REST endpoint 404s), BEFORE any grade
#      is written, so nothing is visible to students yet.
#   3. One PUT per group, to one member's submission: submission[posted_grade]
#      (raw points) plus comment[text_comment] with comment[group_comment]=true,
#      so the grade and comment reach every member.  A score PUT does NOT update
#      an earlier comment, so grade and comment are always sent together.
#   4. Verify: re-read every submission; its score must equal the package total
#      and its latest comment must equal the package comment.  Any mismatch is
#      listed and the exit code is 1.
#   Release to students is a separate, deliberate step: --release.
#
# Usage:        canvas.api.group.grades.post.py [--course-id N] [--course-config FILE]
#                  --assignment-id N --prefix PATH [--apply] [--release]
# Input:        PREFIX.comments.json  (team -> {total, points_possible, comment})
#               PREFIX.gradebook.csv  (per student: canvas_user_id, Team)
# Output:       dry run prints every call; --apply writes and verifies
#
# Options:
#   -i, --assignment-id N   Canvas assignment id (required)
#   -p, --prefix PATH       package prefix, e.g. Grader.Output/grading.2026.HW02
#   -a, --apply             make the changes (default: dry run)
#   -r, --release           after a clean verify, release the grades to students
#   -v, --verbose           print each group's payload
#   -h, --help              this text
# ============================================================================
import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canvas_course                     # noqa: E402

BASE = COURSE = None   # set in main() from canvas.course.json


def call(tok, path, method="GET", data=None, raw=None):
    body = raw if raw is not None else (urllib.parse.urlencode(data).encode() if data else None)
    hdr = {"Authorization": "Bearer " + tok}
    if raw is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=hdr)
    return json.load(urllib.request.urlopen(req))


def get_all(tok, path):
    out, url = [], BASE + path
    while url:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        resp = urllib.request.urlopen(req)
        out += json.load(resp)
        url = None
        for part in resp.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                url = part[part.find("<") + 1:part.find(">")]
    return out


def graphql(tok, query, variables):
    return call(tok, "/api/graphql", "POST",
                raw=json.dumps({"query": query, "variables": variables}).encode())


def main():
    ap = argparse.ArgumentParser(description="Post group grades and comments to Canvas.")
    ap.add_argument("-i", "--assignment-id", required=True, type=int)
    ap.add_argument("-p", "--prefix", required=True)
    ap.add_argument("-a", "--apply", action="store_true")
    ap.add_argument("--sync-comments", action="store_true",
                    help="with --apply: edit existing comments that differ from the package (no grade change)")
    ap.add_argument("-r", "--release", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()
    global BASE, COURSE
    cfg, BASE, tok = canvas_course.setup(a)
    COURSE = cfg.course_id
    pkg = json.load(open(a.prefix + ".comments.json"))
    rows = list(csv.DictReader(open(a.prefix + ".gradebook.csv")))
    members = {}
    for r in rows:
        members.setdefault(r["Team"], []).append(int(r["canvas_user_id"]))
    total_pts = {v["points_possible"] for v in pkg.values()}
    if len(total_pts) != 1 or set(pkg) != set(members):
        print(f"FAIL: package/roster mismatch (points {total_pts}; "
              f"teams only in one: {set(pkg) ^ set(members)})")
        return 1
    pts = total_pts.pop()
    asg = call(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}")
    tag = "" if a.apply else "[DRY-RUN] "
    print(f"{tag}assignment : {asg['name'][:60]} (points {asg['points_possible']} -> {pts:g}, "
          f"grading_type {asg['grading_type']} -> percent, group_category {asg['group_category_id']})")
    print(f"{tag}post policy: manual (GraphQL) before any grade is written")
    existing = get_all(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions?per_page=100&include[]=submission_comments")
    scored = [s["user_id"] for s in existing if s.get("score") is not None]
    if scored and not a.sync_comments:
        print(f"FAIL: {len(scored)} submissions already carry a score; refusing to overwrite")
        return 1
    plan = []
    for team, v in sorted(pkg.items()):
        uid = min(members[team])
        plan.append((team, uid, v["total"], v["comment"]))
        if a.verbose or not a.apply:
            print(f"{tag}PUT group {team}: via user {uid}, grade {v['total']:g}/{pts:g}, "
                  f"comment {len(v['comment'])} chars, {len(members[team])} members")
    with_comments = [x["user_id"] for x in existing if x.get("submission_comments")]
    if a.sync_comments:
        if not a.apply:
            print(f"{tag}SYNC: would edit any comment that differs from the package")
            return 0
        want = {int(r["canvas_user_id"]): pkg[r["Team"]] for r in rows}
        subs = get_all(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions"
                            "?per_page=100&include[]=submission_comments")
        fixed = 0
        for s_ in subs:
            w = want.get(s_["user_id"])
            cm = [c for c in s_.get("submission_comments", []) if c.get("comment")]
            if w and cm and cm[-1]["comment"].strip() != w["comment"].strip():
                call(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions/"
                          f"{s_['user_id']}/comments/{cm[-1]['id']}", "PUT",
                     {"comment[text_comment]": w["comment"]})
                fixed += 1
        subs = get_all(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions"
                            "?per_page=100&include[]=submission_comments")
        bad = [s_["user_id"] for s_ in subs if s_["user_id"] in want and (
               [c["comment"].strip() for c in s_.get("submission_comments", []) if c.get("comment")]
               != [want[s_["user_id"]]["comment"].strip()])]
        print(f"edited {fixed} comment(s); {len(bad)} student(s) not holding exactly the package comment")
        return 1 if bad else 0
    if with_comments:
        print(f"FAIL: {len(with_comments)} submissions already carry comments. A second PUT would ADD "
              "duplicates (group comments are never replaced). Use --sync-comments to edit them.")
        return 1
    print(f"{tag}{len(plan)} group PUTs covering {sum(len(m) for m in members.values())} students")
    if not a.apply:
        print("\ndry run -- nothing written.  Re-run with --apply (then --release once verified)")
        return 0
    call(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}", "PUT",
         {"assignment[points_possible]": f"{pts:g}", "assignment[grading_type]": "percent"})
    r = graphql(tok, "mutation($id:ID!){ setAssignmentPostPolicy(input:{assignmentId:$id,"
                     "postManually:true}){ postPolicy{ postManually } errors{ message } } }",
                {"id": str(a.assignment_id)})
    pm = (((r.get("data") or {}).get("setAssignmentPostPolicy") or {}).get("postPolicy") or {}).get("postManually")
    if pm is not True:
        print("FAIL: post policy is not manual; stopping before any grade is written:", r)
        return 1
    print("assignment settings and manual post policy set")
    for team, uid, total, comment in plan:
        call(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions/{uid}", "PUT",
             {"submission[posted_grade]": f"{total:g}", "comment[text_comment]": comment,
              "comment[group_comment]": "true"})
    print(f"{len(plan)} groups written; verifying")
    time.sleep(3)
    subs = get_all(tok, f"/api/v1/courses/{COURSE}/assignments/{a.assignment_id}/submissions"
                        "?per_page=100&include[]=submission_comments")
    want = {int(r["canvas_user_id"]): pkg[r["Team"]] for r in rows}
    bad = []
    for s in subs:
        w = want.get(s["user_id"])
        if not w:
            continue
        cm = [c for c in s.get("submission_comments", []) if c.get("comment")]
        latest = cm[-1]["comment"] if cm else None
        if s.get("score") is None or abs(s["score"] - w["total"]) > 0.005:
            bad.append((s["user_id"], "score", s.get("score"), w["total"]))
        if latest is None or latest.strip() != w["comment"].strip():
            bad.append((s["user_id"], "comment", None, None))
    print(f"verified {len(want)} students: {len(bad)} mismatch(es)")
    for b in bad[:20]:
        print("  MISMATCH", b)
    if bad:
        return 1
    if a.release:
        r = graphql(tok, "mutation($id:ID!){ postAssignmentGrades(input:{assignmentId:$id,"
                         "gradedOnly:true}){ progress{ _id state } errors{ message } } }",
                    {"id": str(a.assignment_id)})
        print("release requested:", json.dumps(r)[:200])
    else:
        print("grades are written and HIDDEN. Release with --release after MG's go.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
