#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.db.grades.upload.py
# Version:      1.8.0
# Created:      2026-09-02
# Revised:      2026-10-13 -- course-agnostic: course id, base URL, token and year come
#               from canvas.course.json (canvas_course.py); --course-id,
#               --course-config, --year are supplied by it.
# Revised:      2026-10-05
# Purpose:      Push discussion-board scores from the TAs' returned grading
#               files into the Canvas gradebook, one entry per board per
#               student.
#
# WHY IT EXISTS.  db.grading.sheet.py gives each TA a file; --collect merges
# what they send back.  Without this, somebody retypes 128 students x 4 boards
# into SpeedGrader, which is both the slowest part of the job and the only
# part where a typo silently becomes a student's grade.
#
# THE BOARDS ARE CHECKPOINTED DISCUSSIONS.  A board is not one 16-point
# assignment.  Canvas splits it into two 8-point halves -- reply_to_topic, the
# questions a student posted, and reply_to_entry, the responses they wrote --
# and REFUSES a grade that does not say which half it is for:
#   "Must provide a valid sub assignment tag when grading checkpointed
#    discussions"
# The TA sheet records one subtotal per board, so the halves are recovered by
# adding up that board's scored posts by kind: thread -> reply_to_topic,
# response -> reply_to_entry.  The two halves are then checked to sum back to
# the board subtotal in the file, for every student, before anything is sent.
#
# ONE CALL PER HALF.  The bulk submissions/update_grades endpoint ignores
# sub_assignment_tag and fails the whole job with a bare "Unexpected error";
# only PUT .../submissions/<user_id> honours the tag.  So this posts one
# request per student per board per half -- 219 for a 59-student block --
# instead of one per assignment.  Slower, and the only thing that works.
#
# WHAT IT WRITES.  For each student and each board, the per-board subtotal the
# TA entered, split into its two halves, to that board's assignment.  It does NOT apply the 16-point block
# cap -- biol240_grades.py applies that when it sums the four boards, and
# capping here as well would take it off twice.
#
# THE GRADES ARE INDIVIDUAL.  The four DB assignments carry
# group_category_id=None and grade_group_students_individually=True (verified
# 2026-09-02 against course 261062), so a score posted for one student is that
# student's alone.  The discussion TOPIC is delivered through section groups so
# each student sees only their own section's board; that is a different thing
# from the assignment being group-graded, and the two are easy to confuse.
# The script re-checks the flag every run and refuses if it ever changes.
#
# THE CAP NOTE.  db.grades.cap.apply.py trims anyone over the 16-point block
# TWO NOTE CHANNELS, DELIBERATELY SEPARATE.  `cap_note` is ONE sentence sent
# to many students, so the file carries the text once and each student carries
# the boards it lands on.  `grading_comments` is the general case -- a list of
# {user_id, board, kind, text} -- because the late-penalty waiver and the
# duplicate-post explanation say different things to different people, and a
# student can need one of each on the same board.  They keep separate ledgers:
# the cap ledger is the delivery record for notes already sent and is not
# rewritten to gain a column.
#
# cap and records one generic sentence as `cap_note`, plus the boards it
# belongs on.  --post-cap-notes delivers it as a Canvas submission comment,
# which is what SpeedGrader shows the student.  It is the same text for
# everyone: a student who reached 16 is told that nothing past it could count.
# The note deliberately says nothing about a score being 0, because on a board
# that merely straddles the cap the score is not 0 -- it is whatever fitted
# under the 16.
#
# GROUP-COMMENT TRAP.  The DB assignments are group assignments against the
# recitation-section set, so a submission comment DEFAULTS to
# group_comment=1 and would deliver one student's note to all ~20 students in
# their section.  Every comment here sends comment[group_comment]=false.  Do
# not remove it.  This is a different flag from the
# grade_group_students_individually the score path checks: that one makes
# SCORES individual, and has no effect at all on who sees a comment.
#
# MUTED IS NOT THE FLAG THAT MATTERS.  An assignment reports muted:true
# whenever nothing has been posted yet, INCLUDING when its posting policy is
# automatic -- and under an automatic policy the first score written is
# published to the student that instant.  So muted:true on an ungraded
# assignment does not mean "safe to write"; it means "nothing written yet",
# which is the same thing right up until it is not.  On 2026-09-07 this tool
# printed "these assignments are muted, so students will not see the scores"
# after a run against four automatic-policy assignments; had the write
# succeeded it would have published 219 scores to 59 students, and the
# reassuring line would have been the last word on it.
#
# What decides visibility is post_manually.  This refuses to post to an
# assignment whose policy is automatic unless --allow-automatic-posting says
# so out loud, and reports the policy rather than the muted flag.
#
# SAFETY.  Dry run unless --apply.  Every run prints the current grade beside
# the new one and skips anything unchanged.  Every posted score is appended to
# a ledger, so a re-run cannot double-post and there is a record of what was
# sent and when.
#
# Usage:        canvas.api.db.grades.upload.py [--course-id N] [--course-config FILE]
#                  [--year YYYY] [OPTIONS] FILE [FILE ...]
# Input:        the JSON files the TAs saved out of the grading sheet
#               (schema biol240.db.grading/1)
# Output:       a table on stdout; with --apply, grades in Canvas and rows in
#               <BLOCK>/Reports/db.grades_<year>_<block>.ledger.tsv
#
# Arguments:
#       FILE ...          one or more returned TA files
#       --course-id ID    Canvas course id (default: canvas.course.json)
#       --course-config F canvas.course.json to use (default: search upward)
#       --year YYYY       year stamp (default: "year" in canvas.course.json)
#       --postdir DIR     snapshots, used to map a board to its topic
#                         (default: <BLOCK>/Posts)
#       --sheet PATH      the TA's grading sheet HTML, which is where a post's
#                         board and kind live.  Default: guessed from the
#                         file's grader and block
#       --over-checkpoint allow a half to exceed its 8 points, for a student
#                         who posted more than the two the half expects.
#                         Refused without it, with the students named
#       --allow-automatic-posting
#                         post even where the policy is automatic, which
#                         publishes each score to the student as it is
#                         written. Refused without it
#       --reportdir DIR   where the ledger is written
#       --apply           actually post.  Without it, nothing is written
#       --force           re-post a score even if the ledger says it was sent
#       --ta-notes        also print what the TA typed in the per-student
#                         NOTES box -- their remarks to the instructor, not
#                         anything a student sees.  It is never sent.
#                         (--comments is kept as the old spelling)
#       --post-cap-notes  post the file's cap_note as a submission comment on
#                         each board the cap affected.  Needs --apply as well;
#                         the note is printed in full either way
#       --post-comments
#                         send the file's grading_comments -- one comment per
#                         (student, board, kind), each carrying its own text.
#                         Same rules: needs --apply, prints every distinct
#                         text in full first, and is deduped against
#                         db.gradingcomments_<year>_<block>.ledger.tsv so a
#                         re-run does not send a second copy
#       --zero-fill-blanks
#                         after the block's LAST deadline, set every half that
#                         is blank in Canvas and gets nothing from the files to
#                         0, so Canvas's block summary is points / 16.  Never
#                         overwrites a score or an excusal; refuses while any
#                         board in the block is still open.  Students Canvas
#                         lists but no file covers are named, not zeroed
#       --zero-fill-students IDS
#                         with --zero-fill-blanks, also zero-fill these Canvas
#                         user ids (students in no TA file because they
#                         posted nothing).  Explicit ids only, never inferred
#       -n, --dry-run     explicit no-op (the default)
#       -v, --verbose
#       -h, --help
# ============================================================================

import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import canvas_course  # noqa: E402
db_paths = None   # imported in main(), after the course config is resolved
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = COURSE = TOK = None   # set in main() from canvas.course.json


def api(path, method="GET", data=None):
    tok = TOK
    url = path if path.startswith("http") else BASE + path
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def boards_for(postdir, year, block):
    """board label -> {topic_id, assignment_id, points_possible}."""
    lo, hi = (int(x) for x in (block[1:3], block[5:7]))
    out = {}
    for n in range(lo, hi + 1):
        p = os.path.join(postdir, "db.posts_%s_L%02d.json" % (year, n))
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            snap = json.load(fh)
        topic = api("/api/v1/courses/%s/discussion_topics/%s"
                    % (COURSE, snap["topic_id"]))
        aid = topic.get("assignment_id")
        if not aid:
            sys.exit("%s (topic %s) has no assignment -- it is not graded"
                     % (snap["board"], snap["topic_id"]))
        a = api("/api/v1/courses/%s/assignments/%s" % (COURSE, aid))
        if not a.get("grade_group_students_individually", False) \
                and a.get("group_category_id"):
            sys.exit("REFUSING: %s (assignment %s) is group-graded. A score "
                     "posted for one student would be applied to their whole "
                     "group. Fix the assignment before uploading."
                     % (snap["board"], aid))
        out[snap["board"]] = {
            "topic_id": snap["topic_id"], "assignment_id": aid,
            "points_possible": a.get("points_possible"),
            "name": a.get("name"), "muted": a.get("muted"),
            "post_manually": a.get("post_manually"),
        }
    return out


def checkpoints_for(assignment_id):
    """tag -> points_possible.  REST does not expose these; GraphQL does."""
    q = ("query A($id: ID!) { assignment(id: $id) { hasSubAssignments "
         "checkpoints { tag pointsPossible } } }")
    body = json.dumps({"query": q, "variables": {"id": str(assignment_id)}})
    tok = TOK
    req = urllib.request.Request(
        BASE + "/api/graphql", data=body.encode(), method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        doc = json.load(r)
    a = ((doc.get("data") or {}).get("assignment")) or {}
    if not a.get("hasSubAssignments"):
        return {}
    return {c["tag"]: c.get("pointsPossible") for c in a.get("checkpoints") or []}


def load_sheet(path):
    """The TA sheet, which is the only place a post's kind is recorded."""
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    if not m:
        sys.exit("%s has no DATA blob -- is it a grading sheet?" % path)
    return json.loads(m.group(1))


# thread -> the questions half, response -> the responses half
TAG_OF = {"thread": "reply_to_topic", "response": "reply_to_entry"}
TAG_NAME = {"reply_to_topic": "questions", "reply_to_entry": "responses"}


def late_rule(src):
    """How lateness is priced, in whichever shape this file speaks.

    The rule changed twice on 2026-09-07 -- a flat deduction, then a ceiling
    on questions, now -1 per day per post -- and a returned file is
    authoritative about the rule it was scored under.  Taking the SHEET's word
    for it instead is how a waived block came back looking a point short: the
    sheet still said late_penalty 1, because it was built before the waiver
    existed.
    """
    if src is None:
        return None
    for key, kind in (("late_per_day", "per_day"), ("late_max", "ceiling"),
                      ("late_penalty", "penalty")):
        if key in src:
            return {"kind": kind, "v": src.get(key)}
    return None


def effective(points, post, rule):
    """What one post contributes, after whatever the late rule is."""
    if not post.get("late") or not rule:
        return points
    kind, v = rule["kind"], rule["v"]
    if kind == "per_day":
        return max(0.0, points - (v or 0) * (post.get("days_late") or 0))
    if kind == "ceiling":
        return points if v is None else min(points, v)
    return max(0.0, points - (v or 0))


def split_by_checkpoint(stu, layout, board, rule):
    """{tag: points} for one student on one board, from their scored posts."""
    out = {}
    for post in layout["boards"].get(board, []):
        cell = (stu.get("items") or {}).get(str(post["id"]))
        if not cell or cell.get("p") in (None, ""):
            continue
        tag = TAG_OF.get(post["kind"])
        if tag is None:
            sys.exit("post %s has kind %r, which maps to no checkpoint"
                     % (post["id"], post.get("kind")))
        out[tag] = out.get(tag, 0.0) + effective(float(cell["p"]), post, rule)
    return out


def current_grades(assignment_id):
    """user_id -> current score, for everything already in the gradebook."""
    out, url = {}, ("/api/v1/courses/%s/assignments/%s/submissions?per_page=100"
                    % (COURSE, assignment_id))
    while url:
        tok = TOK
        req = urllib.request.Request(
            url if url.startswith("http") else BASE + url,
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req) as r:
            page = json.load(r)
            link = r.headers.get("Link", "")
        for s in page:
            out[str(s["user_id"])] = s.get("score")
        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    return out


def current_halves(assignment_id):
    """user_id -> {tag: {"score": s, "excused": bool}} for each checkpoint
    half.  The parent score alone cannot tell a blank half from a zero one."""
    out, url = {}, ("/api/v1/courses/%s/assignments/%s/submissions"
                    "?include[]=sub_assignment_submissions&per_page=100"
                    % (COURSE, assignment_id))
    while url:
        tok = TOK
        req = urllib.request.Request(
            url if url.startswith("http") else BASE + url,
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req) as r:
            page = json.load(r)
            link = r.headers.get("Link", "")
        for s in page:
            out[str(s["user_id"])] = {
                h.get("sub_assignment_tag"): {"score": h.get("score"),
                                              "excused": bool(h.get("excused"))}
                for h in s.get("sub_assignment_submissions") or []}
        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    return out


def last_deadline(assignment_id):
    """The latest checkpoint due date of a board, as an aware datetime."""
    q = ("query A($id: ID!) { assignment(id: $id) { "
         "checkpoints { tag dueAt } } }")
    body = json.dumps({"query": q, "variables": {"id": str(assignment_id)}})
    tok = TOK
    req = urllib.request.Request(
        BASE + "/api/graphql", data=body.encode(), method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        doc = json.load(r)
    cps = (((doc.get("data") or {}).get("assignment")) or {}).get(
        "checkpoints") or []
    dues = [c["dueAt"] for c in cps if c.get("dueAt")]
    if len(dues) != len(cps) or not dues:
        return None
    return max(datetime.datetime.fromisoformat(d) for d in dues)


def wrap_note(text, width=72):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


def load_note_ledger(path):
    """(user_id, board) pairs that already received the cap note."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                seen.add((row.get("user_id"), row.get("board")))
    return seen


def load_comment_ledger(path):
    """(user_id, board, kind) triples whose grading comment has been sent."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                seen.add((row.get("user_id"), row.get("board"),
                          row.get("kind")))
    return seen


def load_ledger(path):
    """(user_id, board, half, score) already sent."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                seen.add((row.get("user_id"), row.get("board"),
                          row.get("half"), row.get("score")))
    return seen


def main():
    global BASE, COURSE, TOK, db_paths
    ap = argparse.ArgumentParser(
        description="Upload discussion board scores to the Canvas gradebook.")
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--postdir", default=None)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--over-checkpoint", action="store_true",
                    dest="over_checkpoint")
    ap.add_argument("--allow-automatic-posting", action="store_true",
                    dest="allow_automatic")
    ap.add_argument("--reportdir", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ta-notes", "--comments", action="store_true",
                    dest="ta_notes")
    ap.add_argument("--post-cap-notes", action="store_true",
                    dest="post_cap_notes")
    ap.add_argument("--post-comments", action="store_true",
                    dest="post_comments")
    ap.add_argument("--zero-fill-blanks", action="store_true",
                    dest="zero_fill")
    ap.add_argument("--zero-fill-students", default="", dest="zero_extra",
                    help="comma-separated Canvas user ids, in no TA file, "
                         "to zero-fill as well (students who posted nothing)")
    ap.add_argument("-n", "--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()
    cfg, BASE, TOK = canvas_course.setup(a)
    COURSE = cfg.course_id
    if cfg.year is None:
        sys.exit("[FAIL] no year: pass --year or set \"year\" in canvas.course.json")
    a.year = str(cfg.year)
    # db_paths reads canvas.course.json at import time; hand it the SAME config
    # this run resolved (it would otherwise search from the cwd), and report a
    # problem as [FAIL] rather than a traceback.
    canvas_course.use_config(cfg.source)   # db_paths reads the SAME config
    try:
        import db_paths as _db_paths
    except canvas_course.CourseConfigError as e:
        sys.exit(f"[FAIL] {e}")
    db_paths = _db_paths
    postdir, repdir = a.postdir, a.reportdir   # defaults need the block

    # --- read what the TAs sent back --------------------------------------
    rows, graders, block, doc_cap_note = {}, [], None, None
    comments_in = []
    for path in a.files:
        with open(path) as fh:
            doc = json.load(fh)
        if doc.get("schema") != "biol240.db.grading/1":
            sys.exit("%s is not a grading sheet file (schema %r)"
                     % (path, doc.get("schema")))
        if block and doc.get("block") != block:
            sys.exit("%s is for block %s but a previous file was for %s"
                     % (path, doc.get("block"), block))
        block = doc.get("block")
        for n in doc.get("grading_comments") or []:
            comments_in.append(dict(n, source=os.path.basename(path)))
        if doc.get("cap_note"):
            if doc_cap_note and doc_cap_note != doc["cap_note"]:
                sys.exit("%s carries a different cap_note from an earlier "
                         "file -- they must agree before anything is posted"
                         % path)
            doc_cap_note = doc["cap_note"]
        graders.append((doc.get("grader") or "?", os.path.basename(path),
                        len(doc.get("students", []))))
        for s in doc.get("students", []):
            key = str(s["user_id"])
            if key in rows:
                sys.exit("student %s (%s) appears in two files -- the TAs' "
                         "sections overlap, which they should not"
                         % (key, s.get("last")))
            s["_rule"] = late_rule(doc)
            rows[key] = s

    print("block %s, %d student(s) from %d file(s)"
          % (block, len(rows), len(a.files)))
    for g, f, n in graders:
        print("  %-20s %-42s %3d student(s)" % (g, f, n))

    postdir = postdir or db_paths.posts_dir(a.year, block)
    repdir = repdir or db_paths.reports_dir(a.year, block)
    boards = boards_for(postdir, a.year, block)
    print("\nassignments:")
    for b, meta in sorted(boards.items()):
        print("  %s  id %s  out of %-6s posting policy: %s"
              % (b, meta["assignment_id"], meta["points_possible"],
                 "MANUAL, grades held until you post them"
                 if meta["post_manually"]
                 else "AUTOMATIC -- each score PUBLISHES as it is written"))

    auto = [b for b, m in sorted(boards.items()) if not m["post_manually"]]
    if auto:
        print("\nWARNING: %s %s an automatic posting policy. Every score "
              "written there becomes visible to the student immediately and "
              "cannot be recalled. Set those assignments to a manual posting "
              "policy in the gradebook first, or pass "
              "--allow-automatic-posting if publishing now is what you want."
              % (", ".join(auto), "has" if len(auto) == 1 else "have"))

    ledger_path = os.path.join(
        repdir, "db.grades_%s_%s.ledger.tsv" % (a.year, block))
    already = set() if a.force else load_ledger(ledger_path)

    sheet_path = a.sheet
    if not sheet_path:
        who = (graders[0][0] or "").lower()
        sheet_path = os.path.join(
            db_paths.reports_dir(a.year, block),
            "db.grading_%s_%s_%s.html" % (a.year, block, who))
    if not os.path.exists(sheet_path):
        sys.exit("grading sheet not found: %s (pass --sheet)" % sheet_path)
    sheet = load_sheet(sheet_path)
    layout = {str(x["user_id"]): x for x in sheet["students"]}
    sheet_rule = late_rule(sheet) or {"kind": "penalty", "v": 0}
    for s in rows.values():             # a file naming no rule of its own
        if s.get("_rule") is None:      # falls back to the sheet's
            s["_rule"] = sheet_rule
    print("sheet %s" % sheet_path)

    marks = {}
    for b in sorted(boards):
        marks[b] = checkpoints_for(boards[b]["assignment_id"])
        if not marks[b]:
            sys.exit("%s (assignment %s) has no checkpoints. This tool grades "
                     "checkpointed discussions; a plain assignment needs the "
                     "bulk path that was removed in 1.2.0."
                     % (b, boards[b]["assignment_id"]))
    print("checkpoints: " + ", ".join(
        "%s %s/%s" % (TAG_NAME[t], marks[sorted(boards)[0]][t], t)
        for t in sorted(marks[sorted(boards)[0]])))

    # --- work out what would change ---------------------------------------
    plan, unchanged, skipped, over = [], 0, 0, []
    for b in sorted(boards):
        cur = current_grades(boards[b]["assignment_id"])
        for uid, s in rows.items():
            total = (s.get("boards") or {}).get(b)
            if total is None:
                continue
            if uid not in layout:
                sys.exit("student %s (%s) is not in the grading sheet -- "
                         "wrong sheet for this file?" % (uid, s.get("last")))
            halves = split_by_checkpoint(s, layout[uid], b, s["_rule"])
            got = sum(halves.values())
            if abs(got - float(total)) > 1e-9:
                sys.exit("REFUSING: %s %s on %s -- the file says %s but the "
                         "posts add up to %s. The sheet and the returned file "
                         "disagree; do not grade from either until that is "
                         "understood."
                         % (s.get("last"), s.get("first"), b, total, got))
            for tag, val in sorted(halves.items()):
                cap = marks[b].get(tag)
                if cap is not None and val > cap:
                    over.append((s.get("last"), s.get("first"), b, tag, val,
                                 cap))
                if (uid, b, tag, str(val)) in already:
                    skipped += 1
                    continue
                plan.append({"uid": uid, "board": b, "tag": tag, "new": val,
                             "total": total, "old": cur.get(uid),
                             "last": s.get("last"), "first": s.get("first"),
                             "section": s.get("section"),
                             "aid": boards[b]["assignment_id"]})

    # --- zero-fill: blank halves become 0 once the block has closed --------
    # The boards' points are spread so a block's boards total 16 (2026-09-23),
    # which makes Canvas's block summary equal points / 16 -- but only if
    # every half carries a score, because Canvas leaves a blank out of the
    # summary instead of counting it as 0.  So after the block's LAST
    # deadline, a half that is blank in Canvas AND has nothing coming from
    # the TA file is set to 0.  It never overwrites a score, never touches an
    # excused half, and never runs before every deadline in the block has
    # passed (a 0 posted while students can still post would read as
    # "missed").  A late post graded afterwards replaces the 0 in the normal
    # way, since the plan above posts whatever the TA file says.
    zeros, zero_kept = [], 0
    if a.zero_fill:
        now = datetime.datetime.now(datetime.timezone.utc)
        for b in sorted(boards):
            due = last_deadline(boards[b]["assignment_id"])
            if due is None:
                sys.exit("REFUSING --zero-fill-blanks: %s has a checkpoint "
                         "with no due date" % b)
            if due > now:
                sys.exit("REFUSING --zero-fill-blanks: %s is not closed yet "
                         "(last deadline %s). Blank halves are not zeros "
                         "until then." % (b, due.isoformat()))
        coming = {(p["uid"], p["board"], p["tag"]) for p in plan}
        not_in_files = set()
        # Named students with no row in any file: they posted nothing, so the
        # sheet never listed them.  Only ever by explicit id -- a student who
        # is missing because a FILE is missing must not be zeroed.
        extra = {}
        for uid in [x.strip() for x in a.zero_extra.split(",") if x.strip()]:
            if uid in rows:
                sys.exit("--zero-fill-students: %s is in a TA file already"
                         % uid)
            u = api("/api/v1/courses/%s/users/%s?include[]=enrollments"
                    % (COURSE, uid))
            last, _, first = (u.get("sortable_name") or uid).partition(", ")
            extra[uid] = {"last": last, "first": first,
                          "section": ",".join(str(e.get("course_section_id"))
                                              for e in u.get("enrollments")
                                              or [])}
        for b in sorted(boards):
            cur = current_halves(boards[b]["assignment_id"])
            not_in_files.update(u for u in cur
                                if u not in rows and u not in extra)
            for uid, s in list(rows.items()) + list(extra.items()):
                for tag in sorted(marks[b]):
                    if (uid, b, tag) in coming:
                        continue
                    h = (cur.get(uid) or {}).get(tag) or {}
                    if h.get("excused") or h.get("score") is not None:
                        zero_kept += 1
                        continue
                    if (uid, b, tag, "0.0") in already:
                        skipped += 1
                        continue
                    zeros.append({"uid": uid, "board": b, "tag": tag,
                                  "new": 0.0, "total": "blank->0",
                                  "old": None, "zero": True,
                                  "last": s.get("last"),
                                  "first": s.get("first"),
                                  "section": s.get("section"),
                                  "aid": boards[b]["assignment_id"]})
        print("\nzero-fill: %d blank half(s) -> 0 over %d student(s); %d "
              "half(s) left alone because Canvas already holds a score or "
              "an excusal"
              % (len(zeros), len({z["uid"] for z in zeros}), zero_kept))
        by = {}
        for z in zeros:
            by[z["board"]] = by.get(z["board"], 0) + 1
        for b in sorted(by):
            print("    %s  %d" % (b, by[b]))
        if not_in_files:
            # Recorded, not guessed at: a student Canvas lists but no TA file
            # covers may be a missing file, and gets NO zeros from this run.
            print("    %d student(s) in Canvas are in none of the files given "
                  "and were NOT zero-filled: %s"
                  % (len(not_in_files), ", ".join(sorted(not_in_files))))
        plan += zeros

    plan.sort(key=lambda p: (p["section"] or "", (p["last"] or "").lower(),
                             p["board"], p["tag"]))
    print("\n%d half-score(s) to post over %d student-board(s), %d in the "
          "ledger already"
          % (len(plan), len({(p["uid"], p["board"]) for p in plan}), skipped))
    if plan:
        print("\n  %-4s %-24s %-5s %-10s %6s   (board)"
              % ("sec", "student", "board", "half", "points"))
        for p in plan:
            print("  %-4s %-24s %-5s %-10s %6s   %s"
                  % (p["section"], ("%s, %s" % (p["last"], p["first"]))[:24],
                     p["board"], TAG_NAME[p["tag"]], p["new"], p["total"]))

    if over:
        print("\n%d half-score(s) exceed the points that half is out of:"
              % len(over))
        for last, first, b, tag, val, cap in over:
            print("    %s, %s  %s %s  %s out of %s"
                  % (last, first, b, TAG_NAME[tag], val, cap))
        if not a.over_checkpoint:
            sys.exit("REFUSING: pass --over-checkpoint to post these, or fix "
                     "the scores. Canvas accepts a score above the maximum; "
                     "this is a decision, not an accident.")
        print("    --over-checkpoint given: posting them as they stand.")

    missing = [s for s in rows.values()
               if not any((s.get("boards") or {}).get(b) is not None
                          for b in boards)]
    if missing:
        print("\n%d student(s) in the files with no scores at all:" % len(missing))
        for s in missing[:20]:
            print("    %s, %s (section %s)"
                  % (s.get("last"), s.get("first"), s.get("section")))

    if a.ta_notes:
        noted = [s for s in rows.values() if (s.get("notes") or "").strip()]
        print("\n%d student(s) carry a TA note. NOT posted -- read them and "
              "decide:" % len(noted))
        for s in noted:
            print("    %s, %s: %s" % (s.get("last"), s.get("first"),
                                      " ".join(s["notes"].split())))

    # --- the cap note ------------------------------------------------------
    note = (doc_cap_note or "").strip()
    note_ledger = os.path.join(
        repdir, "db.capnotes_%s_%s.ledger.tsv" % (a.year, block))
    note_plan = []
    if a.post_cap_notes:
        if not note:
            sys.exit("--post-cap-notes given but the file carries no "
                     "cap_note. Run db.grades.cap.apply.py first.")
        sent = set() if a.force else load_note_ledger(note_ledger)
        for uid, s in rows.items():
            cap = s.get("capped") or {}
            for b in cap.get("boards") or []:
                if b not in boards:
                    sys.exit("%s %s is capped on %s, which is not a board in "
                             "this block" % (s.get("last"), uid, b))
                if (uid, b) in sent:
                    continue
                note_plan.append({"uid": uid, "board": b,
                                  "aid": boards[b]["assignment_id"],
                                  "last": s.get("last"),
                                  "first": s.get("first"),
                                  "section": s.get("section")})
        note_plan.sort(key=lambda p: (p["section"] or "",
                                      (p["last"] or "").lower(), p["board"]))
        print("\n%d cap note(s) to post, on %d student(s):"
              % (len(note_plan), len({p["uid"] for p in note_plan})))
        for p in note_plan:
            print("  %-4s %-26s %s" % (p["section"],
                                       ("%s, %s" % (p["last"], p["first"]))[:26],
                                       p["board"]))
        print("\nthe comment every one of them receives, verbatim:\n")
        for line in wrap_note(note):
            print("  " + line)
        print("\nposted with comment[group_comment]=false, so it reaches that "
              "student only.")

    # --- the grading comments --------------------------------------------
    comment_ledger = os.path.join(
        repdir, "db.gradingcomments_%s_%s.ledger.tsv" % (a.year, block))
    comment_plan = []
    if a.post_comments:
        if not comments_in:
            sys.exit("--post-comments given but no file carries a "
                     "grading_comments list.")
        sent = set() if a.force else load_comment_ledger(comment_ledger)
        for n in comments_in:
            uid, b, kind = str(n["user_id"]), n["board"], n["kind"]
            if b not in boards:
                sys.exit("a %s note for %s names board %s, which is not in "
                         "this block" % (kind, uid, b))
            if uid not in rows:
                sys.exit("a %s note names student %s, who is not in the "
                         "grading file" % (kind, uid))
            if (uid, b, kind) in sent:
                continue
            st = rows[uid]
            comment_plan.append({"uid": uid, "board": b, "kind": kind,
                               "text": n["text"].strip(),
                               "aid": boards[b]["assignment_id"],
                               "last": st.get("last"), "first": st.get("first"),
                               "section": st.get("section"),
                               "source": n.get("source", "")})
        comment_plan.sort(key=lambda p: (p["kind"], p["section"] or "",
                                       (p["last"] or "").lower(), p["board"]))
        kinds = sorted({p["kind"] for p in comment_plan})
        print("\n%d grading comment(s) to send, on %d student(s), %d kind(s):"
              % (len(comment_plan), len({p["uid"] for p in comment_plan}),
                 len(kinds)))
        for kind in kinds:
            mine = [p for p in comment_plan if p["kind"] == kind]
            print("\n  %s -- %d comment(s) over %d student(s):"
                  % (kind, len(mine), len({p["uid"] for p in mine})))
            for p in mine:
                print("    %-4s %-26s %s"
                      % (p["section"],
                         ("%s, %s" % (p["last"], p["first"]))[:26], p["board"]))
            for text in sorted({p["text"] for p in mine}):
                print("\n    the comment, verbatim:")
                for line in wrap_note(text):
                    print("      " + line)
        print("\nposted with comment[group_comment]=false, so each reaches "
              "that student only.")

    if auto and a.apply and not a.dry_run and not a.allow_automatic:
        sys.exit("REFUSING to post: %s would publish to students on write. "
                 "See the warning above." % ", ".join(auto))

    if not a.apply or a.dry_run:
        print("\nDRY RUN -- nothing was written. Re-run with --apply to post.")
        return 0

    # --- post -------------------------------------------------------------
    os.makedirs(repdir, exist_ok=True)
    new_ledger = not os.path.exists(ledger_path)
    stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
    posted = 0
    with open(ledger_path, "a", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        if new_ledger:
            w.writerow(["when", "board", "half", "assignment_id", "user_id",
                        "last", "first", "section", "previous", "score",
                        "source"])
        failed = 0
        for i, p in enumerate(plan, 1):
            try:
                api("/api/v1/courses/%s/assignments/%s/submissions/%s"
                    % (COURSE, p["aid"], p["uid"]), "PUT",
                    {"submission[posted_grade]": p["new"],
                     "sub_assignment_tag": p["tag"]})
            except urllib.error.HTTPError as e:
                print("  FAILED %s, %s %s %s: %s %s"
                      % (p["last"], p["first"], p["board"],
                         TAG_NAME[p["tag"]], e.code,
                         e.read()[:120].decode(errors="replace")))
                failed += 1
                continue
            w.writerow([stamp, p["board"], p["tag"], p["aid"], p["uid"],
                        p["last"], p["first"], p["section"],
                        "" if p["old"] is None else p["old"], p["new"],
                        "zero-fill" if p.get("zero")
                        else ",".join(g[1] for g in graders)])
            fh.flush()
            posted += 1
            if i % 25 == 0 or i == len(plan):
                print("  %d/%d posted" % (posted, len(plan)))
        if failed:
            print("  %d half-score(s) FAILED and are not in the ledger"
                  % failed)

    print("\nposted %d half-score(s); ledger %s" % (posted, ledger_path))

    if note_plan:
        fresh = not os.path.exists(note_ledger)
        done = 0
        with open(note_ledger, "a", newline="") as fh:
            w = csv.writer(fh, delimiter="\t")
            if fresh:
                w.writerow(["when", "board", "assignment_id", "user_id",
                            "last", "first", "section", "chars", "source"])
            for p in note_plan:
                api("/api/v1/courses/%s/assignments/%s/submissions/%s"
                    % (COURSE, p["aid"], p["uid"]), "PUT",
                    {"comment[text_comment]": note,
                     "comment[group_comment]": "false"})
                w.writerow([stamp, p["board"], p["aid"], p["uid"], p["last"],
                            p["first"], p["section"], len(note),
                            ",".join(g[1] for g in graders)])
                done += 1
        print("posted %d cap note(s); ledger %s" % (done, note_ledger))
    if comment_plan:
        fresh = not os.path.exists(comment_ledger)
        done = 0
        with open(comment_ledger, "a", newline="") as fh:
            w = csv.writer(fh, delimiter="\t")
            if fresh:
                w.writerow(["when", "board", "kind", "assignment_id",
                            "user_id", "last", "first", "section", "chars",
                            "source"])
            for p in comment_plan:
                api("/api/v1/courses/%s/assignments/%s/submissions/%s"
                    % (COURSE, p["aid"], p["uid"]), "PUT",
                    {"comment[text_comment]": p["text"],
                     "comment[group_comment]": "false"})
                w.writerow([stamp, p["board"], p["kind"], p["aid"], p["uid"],
                            p["last"], p["first"], p["section"],
                            len(p["text"]), p["source"]])
                done += 1
        print("sent %d grading comment(s); ledger %s" % (done, comment_ledger))

    held = [b for b, m in sorted(boards.items()) if m["post_manually"]]
    if held:
        print("HELD: %s %s a manual posting policy, so nothing above is "
              "visible to students until you post grades in the Canvas "
              "gradebook. Comments go with them."
              % (", ".join(held), "has" if len(held) == 1 else "have"))
    if auto:
        print("PUBLISHED: %s had an automatic posting policy, so those "
              "scores are already visible to students."
              % ", ".join(auto))
    return 0


if __name__ == "__main__":
    sys.exit(main())
