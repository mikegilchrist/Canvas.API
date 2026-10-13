#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.db.topic.text.py
# Version:      1.2.0
# Created:      2026-09-07
# Revised:      2026-10-13
# Purpose:      Edit the intro text of the discussion board topics -- add,
#               replace or remove one managed paragraph across a block of
#               boards, without touching the rest of what each one says.
#
# WHY IT EXISTS.  The boards carry the rules students actually read: what a
# block is worth, what is due when, and now what a late post costs.  Nothing
# in //bin could change that text, so a policy change meant hand-editing 26
# topics in the Canvas UI -- 26 chances to paste a different wording, and no
# record afterwards of which boards had been done.
#
# ONE PARAGRAPH, NOT THE WHOLE BODY.  Each board's intro is mostly specific to
# that board ("Questions on the lecture slides from L05: Explaining
# Dominance"), so this never replaces a message wholesale.  It manages a
# single <p> identified by its BOLD LEAD-IN -- "Late posts." -- replacing that
# paragraph if it is already there and appending it if it is not.  Run it
# twice and the second run is a no-op; change the wording and re-run and every
# board is corrected.
#
# WHY THE LEAD-IN AND NOT A MARKER.  The obvious design is an HTML comment
# around the managed block.  Canvas STRIPS HTML comments out of a message
# body, so the marker would survive the PUT and be gone by the next GET, and
# every run would append a second copy.  The bold lead-in is visible text,
# which is exactly why it survives.
#
# THE PARENT IS ENOUGH.  These are group discussions: each board has six child
# topics, one per recitation section, and those are what students post in.
# Their message bodies mirror the parent's, verified against L05 before this
# was written, so editing the parent updates all six.  --verify re-reads the
# children afterwards and reports any that did not follow.  A child topic is
# addressed as /groups/<group_id>/discussion_topics/<id>, NOT under the
# course: the course path 404s for every one of them.
#
# THE OLD TEXT IS SAVED FIRST.  Every message this is about to change is
# written to a timestamped JSON before anything is sent, because a discussion
# topic has no version history in Canvas and a bad wholesale edit would
# otherwise be unrecoverable.
#
# Usage:        canvas.db.topic.text.py [OPTIONS] --text-file FILE
#               canvas.db.topic.text.py [OPTIONS] --remove
#
# Input:        the Canvas discussion topics for the course
#               a file holding the paragraph to install, as HTML
#
# Output:       (shared, cross-block) Reports/db.topictext_<year>.ledger.tsv  -- what was changed
#               Reports/db.topictext.backup_<year>_<stamp>.json -- prior text
#
# Arguments:
#       --course-id ID    Canvas course id (default: canvas.course.json)
#       --course-config F course config (default: found upward from cwd)
#       --year YYYY       year stamp (default: config year)
#       --block BLOCK     L01-L04 | L05-L09 | ... ; or --boards
#       --boards LIST     explicit boards, e.g. L05,L06,L07
#       --all             every L## board in the course
#       --heading TEXT    the bold lead-in that identifies the managed
#                         paragraph (default: "Late posts.")
#       --text-file PATH  the paragraph to install, as HTML.  It must open
#                         with the heading in <strong> or <b>
#       --remove          delete the managed paragraph instead of writing one
#       --verify          after writing, re-read the child topics and confirm
#                         they picked the change up
#       --reportdir DIR   where the ledger and backup are written
#       --apply           actually write.  Without it, nothing is sent
#       -n, --dry-run     explicit no-op (the default)
#       -v, --verbose     print the full before and after for every board
#       -h, --help
# ============================================================================

import argparse
import csv
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import canvas_course  # noqa: E402
try:
    import db_paths  # noqa: E402  (reads canvas.course.json at import)
except canvas_course.CourseConfigError as _e:
    sys.exit("[FAIL] %s" % _e)
import canvas_course  # noqa: E402
import urllib.error
import urllib.parse
import urllib.request

BASE = COURSE = TOKEN = None   # set in main() from canvas.course.json
import db_paths as _dbp   # the block table lives in ONE place
BLOCKS = dict(_dbp.BLOCK_BOUNDS)
DEFAULT_HEADING = "Late posts."


def api(path, method="GET", data=None):
    tok = TOKEN
    url = path if path.startswith("http") else BASE + path
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.load(fh), fh.headers.get("Link", "")


def get_all(path):
    """Every page of a list endpoint."""
    out, url = [], path
    while url:
        page, link = api(url)
        out.extend(page)
        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip(" <>")
    return out


def strip_html(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def wanted_boards(a):
    if a.all:
        return None                      # everything
    if a.boards:
        return {b.strip().upper() for b in a.boards.split(",") if b.strip()}
    if a.block:
        if a.block not in BLOCKS:
            sys.exit("unknown block %r; one of %s"
                     % (a.block, ", ".join(sorted(BLOCKS))))
        lo, hi = BLOCKS[a.block]
        return {"L%02d" % n for n in range(lo, hi + 1)}
    sys.exit("say which boards: --block, --boards or --all")


def split_paragraphs(msg):
    """The message as a list of chunks, so one <p> can be replaced in place."""
    return re.findall(r"<p\b.*?</p>|[^<]+|<[^>]+>", msg or "", re.S | re.I)


def find_managed(msg, heading):
    """Index of the <p> whose bold lead-in is `heading`, or None."""
    parts = split_paragraphs(msg)
    want = heading.strip().lower()
    for i, chunk in enumerate(parts):
        if not re.match(r"<p\b", chunk, re.I):
            continue
        m = re.search(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", chunk,
                      re.S | re.I)
        if m and strip_html(m.group(1)).lower().rstrip() == want:
            return parts, i
    return parts, None


def rewrite(msg, heading, new_html):
    """The message with the managed paragraph installed, replaced or removed.

    Returns (text, what) where what is added / replaced / removed / unchanged.
    """
    parts, at = find_managed(msg, heading)
    if new_html is None:                        # --remove
        if at is None:
            return msg, "absent"
        del parts[at]
        return "".join(parts).rstrip(), "removed"
    if at is None:
        joined = (msg or "").rstrip()
        return (joined + "\n" + new_html), "added"
    if parts[at].strip() == new_html.strip():
        return msg, "unchanged"
    parts[at] = new_html
    return "".join(parts), "replaced"


def main():
    ap = argparse.ArgumentParser(
        description="Edit one managed paragraph in the discussion board "
                    "intro text.")
    ap.add_argument("--block", default=None)
    ap.add_argument("--boards", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--heading", default=DEFAULT_HEADING)
    ap.add_argument("--text-file", default=None, dest="text_file")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--reportdir", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("-n", "--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()

    global BASE, COURSE, TOKEN
    cfg, BASE, TOKEN = canvas_course.setup(a)
    COURSE = cfg.course_id
    if cfg.year is None:
        sys.exit("[FAIL] no year: pass --year or set \"year\" in canvas.course.json")
    a.year = str(cfg.year)
    repdir = a.reportdir or db_paths.shared_reports_dir(a.year)

    if a.remove and a.text_file:
        sys.exit("--remove and --text-file are opposites; pick one")
    new_html = None
    if not a.remove:
        if not a.text_file:
            sys.exit("give --text-file (the paragraph to install) or --remove")
        new_html = open(a.text_file, encoding="utf-8").read().strip()
        lead = re.search(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>",
                         new_html, re.S | re.I)
        if not lead or strip_html(lead.group(1)).lower().rstrip() \
                != a.heading.strip().lower():
            sys.exit("the text must open with the heading %r inside <strong> "
                     "or <b> -- that lead-in is the only thing identifying "
                     "the paragraph on a later run, because Canvas strips "
                     "HTML comments." % a.heading)

    want = wanted_boards(a)
    topics = get_all("/api/v1/courses/%s/discussion_topics?per_page=100"
                     % COURSE)
    picked = []
    for t in topics:
        m = re.match(r"^(L\d\d)\b", t.get("title", "") or "")
        if not m:
            continue
        if re.search(r"\bCopy\s*$", t.get("title", "") or ""):
            print("  skipping a duplicate board: %s" % t["title"])
            continue
        if want is None or m.group(1) in want:
            picked.append((m.group(1), t))
    picked.sort(key=lambda x: x[0])
    if not picked:
        sys.exit("no matching boards found")
    missing = (want - {b for b, _ in picked}) if want else set()
    if missing:
        sys.exit("no board found for %s" % ", ".join(sorted(missing)))

    print("course %s, %d board(s), managed paragraph %r"
          % (COURSE, len(picked), a.heading))
    print("%s\n" % ("REMOVING it" if a.remove else "installing:"))
    if new_html:
        print("  " + strip_html(new_html))
        print()

    plan, counts = [], {}
    for label, t in picked:
        msg = t.get("message") or ""
        out, what = rewrite(msg, a.heading, new_html)
        counts[what] = counts.get(what, 0) + 1
        if what in ("unchanged", "absent"):
            print("  %-5s %-8s %s" % (label, what, t.get("title", "")[:48]))
            continue
        plan.append({"board": label, "topic_id": t["id"],
                     "title": t.get("title", ""), "old": msg, "new": out,
                     "what": what,
                     "children": [(c["group_id"], c["id"]) for c in
                                  (t.get("group_topic_children") or [])]})
        print("  %-5s %-8s %s" % (label, what, t.get("title", "")[:48]))
        if a.verbose:
            print("      before: %s" % strip_html(msg)[:300])
            print("      after : %s" % strip_html(out)[:300])

    print("\n%s" % ", ".join("%d %s" % (n, k)
                             for k, n in sorted(counts.items())))
    if not plan:
        print("nothing to do.")
        if a.verify:                    # still worth asking: are they in sync?
            verify(picked)
        return 0

    if not a.apply or a.dry_run:
        print("\nDRY RUN -- nothing was sent. Re-run with --apply.")
        return 0

    os.makedirs(repdir, exist_ok=True)
    stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
    backup = os.path.join(
        repdir, "db.topictext.backup_%s_%s.json"
        % (a.year, stamp.replace(":", "").replace("-", "")))
    with open(backup, "w") as fh:
        json.dump({p["board"]: {"topic_id": p["topic_id"],
                                "message": p["old"]} for p in plan},
                  fh, indent=1)
    print("\nprior text saved to %s" % backup)

    ledger = os.path.join(repdir, "db.topictext_%s.ledger.tsv" % a.year)
    fresh = not os.path.exists(ledger)
    done = 0
    with open(ledger, "a", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        if fresh:
            w.writerow(["when", "board", "topic_id", "heading", "action",
                        "chars_before", "chars_after", "backup"])
        for p in plan:
            try:
                api("/api/v1/courses/%s/discussion_topics/%s"
                    % (COURSE, p["topic_id"]), "PUT", {"message": p["new"]})
            except urllib.error.HTTPError as e:
                print("  FAILED %s: %s %s" % (p["board"], e.code, e.reason))
                continue
            w.writerow([stamp, p["board"], p["topic_id"], a.heading,
                        p["what"], len(p["old"]), len(p["new"]),
                        os.path.basename(backup)])
            done += 1
    print("changed %d board(s); ledger %s" % (done, ledger))

    if a.verify:
        verify(picked)
    return 0


def verify(picked):
    """Do the per-section child topics say what their parent says?

    Each child hangs off its GROUP, not the course: the course path 404s.
    Compared as text, since Canvas normalises the markup it stores.
    """
    print("\nverifying the child topics followed:")
    bad = checked = 0
    for label, t in picked:
        parent, _ = api("/api/v1/courses/%s/discussion_topics/%s"
                        % (COURSE, t["id"]))
        want = strip_html(parent.get("message"))
        for c in (t.get("group_topic_children") or []):
            checked += 1
            try:
                child, _ = api("/api/v1/groups/%s/discussion_topics/%s"
                               % (c["group_id"], c["id"]))
            except urllib.error.HTTPError as e:
                print("  %s child %s: could not read (%s)"
                      % (label, c["id"], e.code))
                bad += 1
                continue
            if strip_html(child.get("message")) != want:
                print("  %s child %s (group %s) does NOT match its parent"
                      % (label, c["id"], c["group_id"]))
                bad += 1
    print("  %d child topic(s) checked; %s"
          % (checked, "every one matches its parent" if not bad
             else "%d did NOT match" % bad))


if __name__ == "__main__":
    sys.exit(main())
