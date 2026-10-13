#!/usr/bin/env python3
"""
canvas.api.catme.calendar.py -- put CATME survey deadlines and exam dates on a
course's Canvas calendar.  The dates, assignment ids and exam list live in the
"catme_calendar" block of the course's canvas.course.json (survey_due,
survey_release, team_formation, exams).

Three kinds of write, all idempotent:
  1. due_at on the five "CATME <n>: ... Survey Completed" assignments
  2. a description note on those same five (CATME is emailed, not in Canvas)
  3. course calendar events for the five CATME release dates and the three exams

Release dates cannot ride on the assignment: Canvas plots an assignment at its
due date only, and unlock_at would HIDE the item until then.  Hence events.

Author: M. Gilchrist (with Claude Code)
Version: 1.2.0  (2026-10-13: course id, dates, assignment ids and exams moved
         into canvas.course.json; --course-id/--course-config/--year added.
         1.1.0 2026-08-18: CATME is 5% of the course, so each
         survey is worth 1%, not 0.9%)
Created: 2026-08-18   Revised: 2026-10-13
Usage:   canvas.api.catme.calendar.py [--course-id ID] [--course-config FILE]
                                      [--year YYYY] [--apply] [-v] [--skip-note]
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import canvas_http  # noqa: E402
import canvas_course  # noqa: E402

BASE = COURSE = None   # set in main() from canvas.course.json
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Course-specific data comes from canvas.course.json, block "catme_calendar":
#   survey_due      [[name, canvas assignment id, "YYYY-MM-DD"], ...]
#   survey_release  [[name, "YYYY-MM-DD"], ...]   (schedule catme_posted column)
#   team_formation  [[title, date, start, end, description], ...]  (calendar
#                   events only; no Canvas column by design -- it carries no credit)
#   exams           [[title, date, start, end, location], ...]
# Filled in by main():
SURVEY_DUE = SURVEY_RELEASE = TEAM_FORMATION = EXAMS = None

NOTE = (
    "<p><strong>This survey is completed at CATME.org, not in Canvas.</strong></p>\n"
    "<p>You will be emailed an invitation in my name from CATME.org with a link to "
    "your individual peer evaluation form. Watch your UTK email, including the spam "
    "and junk folders. The link is individual to you, so do not use a teammate's "
    "CATME link.</p>\n"
    "<p>If you registered with CATME using an address other than "
    "NETID@vols.utk.edu, I cannot see your account.</p>\n"
    "<p>This Canvas column records only whether you completed the survey; there is "
    "nothing to submit here. Each of the five surveys you complete is worth 1% of "
    "your course grade, and each one you skip costs you that same 1%.</p>"
)

# Shown on each "opens" calendar event.
OPEN_NOTE = (
    "{name} opens today. Your invitation comes from CATME.org, in my name, with a "
    "link to your individual peer evaluation form -- check your UTK email, including "
    "the spam and junk folders. The link is individual to you, so do not use a "
    "teammate's CATME link. Due {due}."
)


def iso(date, hhmm):
    """Local wall-clock in America/New_York -> the UTC stamp Canvas stores."""
    h, m = (int(x) for x in hhmm.split(":"))
    dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=ET)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def existing_events(tok):
    url = (f"{BASE}/api/v1/calendar_events?context_codes[]=course_{COURSE}"
           f"&all_events=true&type=event&per_page=100")
    return {e["title"]: e for e in canvas_http.paginated_get_all(url, tok)}


def main():
    global BASE, COURSE, SURVEY_DUE, SURVEY_RELEASE, TEAM_FORMATION, EXAMS
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Dry run by DEFAULT, per the house convention.  Until 2026-08-18 this
    # took --dry-run instead, so the bare command wrote to Canvas; that read
    # as a preview and was run as one.  --dry-run is still accepted so an old
    # invocation cannot suddenly mean the opposite of what it used to.
    p.add_argument("-a", "--apply", action="store_true",
                   help="perform the writes (default is a dry run)")
    p.add_argument("--dry-run", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("-v", "--verbose", action="store_true", help="show payloads")
    p.add_argument("--skip-note", action="store_true",
                   help="set due dates only; leave descriptions alone")
    canvas_course.add_arguments(p)
    args = p.parse_args()
    args.dry_run = not args.apply

    cfg, BASE, tok = canvas_course.setup(args)
    COURSE = cfg.course_id
    block = cfg.data.get("catme_calendar")
    if not block:
        sys.exit(f"[FAIL] no \"catme_calendar\" block in {cfg.source or 'canvas.course.json'}")
    try:
        SURVEY_DUE = [tuple(x) for x in block["survey_due"]]
        SURVEY_RELEASE = [tuple(x) for x in block["survey_release"]]
        TEAM_FORMATION = [tuple(x) for x in block["team_formation"]]
        EXAMS = [tuple(x) for x in block["exams"]]
    except KeyError as e:
        sys.exit(f"[FAIL] catme_calendar is missing key {e}")
    tag = "[DRY-RUN] " if args.dry_run else ""
    if args.dry_run:
        # A dry run must not need the network; the duplicate check is the only
        # thing that would, so skip it and say so.
        have = {}
        print(f"{tag}course {COURSE}; skipping the existing-event check (no network "
              f"on a dry run), so nothing below shows as SKIP\n")
    else:
        have = existing_events(tok)
        print(f"course {COURSE}; {len(have)} calendar event(s) already present\n")

    print("== 1. due dates on CATME Survey Completed ==")
    for name, aid, date in SURVEY_DUE:
        due = iso(date, "23:59")
        payload = {"assignment": {"due_at": due}}
        if not args.skip_note:
            payload["assignment"]["description"] = NOTE
        print(f"  {tag}PUT assignment {aid}  {name:<10} due_at={date} 23:59 ET"
              f"{'' if args.skip_note else ' + description note'}")
        if args.verbose:
            print("      " + json.dumps(payload)[:200])
        if not args.dry_run:
            canvas_http.http_put_json(
                f"{BASE}/api/v1/courses/{COURSE}/assignments/{aid}", tok, payload)

    print("\n== 2. calendar events: team formation questionnaire ==")
    for title, date, start, end, desc in TEAM_FORMATION:
        mk_event(tok, args, have, title, date, start, end, None, desc)

    print("\n== 3. calendar events: CATME release dates ==")
    due_by_name = {n: d for n, _, d in SURVEY_DUE}
    for name, date in SURVEY_RELEASE:
        due = datetime.strptime(due_by_name[name], "%Y-%m-%d").strftime("%a %b %d")
        mk_event(tok, args, have, f"{name} peer evaluation opens", date,
                 "08:00", "09:00", None,
                 OPEN_NOTE.format(name=name, due=f"{due} at 11:59pm"))

    print("\n== 4. calendar events: exams ==")
    for title, date, start, end, loc in EXAMS:
        mk_event(tok, args, have, title, date, start, end, loc, None)

    print(f"\n{tag}done.")


def mk_event(tok, args, have, title, date, start, end, loc, desc):
    tag = "[DRY-RUN] " if args.dry_run else ""
    body = {"calendar_event": {
        "context_code": f"course_{COURSE}",
        "title": title,
        "start_at": iso(date, start),
        "end_at": iso(date, end),
    }}
    if loc:
        body["calendar_event"]["location_name"] = loc
    if desc:
        body["calendar_event"]["description"] = desc

    if title in have:
        print(f"  {tag}SKIP (already exists) {date}  {title}")
        return
    print(f"  {tag}POST event  {date} {start}-{end}  {title}"
          f"{'  @ ' + loc if loc else ''}")
    if args.verbose:
        print("      " + json.dumps(body)[:250])
    if not args.dry_run:
        try:
            canvas_http.http_post_json(f"{BASE}/api/v1/calendar_events", tok, body)
        except urllib.error.HTTPError as e:
            print(f"      FAILED {e.code}: {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
