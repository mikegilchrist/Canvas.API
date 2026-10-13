#!/usr/bin/env python3
"""
canvas.api.calendar.sync.py -- sync new/changed events from a live Canvas
course calendar feed directly into a Nextcloud calendar over
CalDAV, filtered by category (Exams, Homework, CATME, Discussion Board,
etc. -- you choose which to bring in).

Unlike subscribing to the live Canvas .ics feed, this never re-adds
something you deleted, and never silently overwrites something you edited
-- see "Sync semantics" below. The target calendar (default name "BIOL240")
becomes your freely-editable copy: edit or delete events there in Nextcloud,
Thunderbird, or any CalDAV/synced app, and those edits stick.

CalDAV credentials are NOT stored by this script -- it reuses koan's config
(~/.config/koan/config.yaml + the "koan" system-keyring secret
"caldav-password"), the same credentials the `koan` CLI already uses. Set
them up with `koan config` / `koan config set-secret` if not already done.

Categories (first run, or --reconfigure, asks y/n for each):
  hw_posting        Homework posting reminders -- YOUR task (release the
                     next HW), not a student due date. Not in Canvas at
                     all; read from the schedule TSV's hw_posted column
                     (see load_hw_posting_events()).
  exams             Exams
  hw_individual     Individual Homework (due dates)
  hw_team           Team Homework (due dates)
  recitation        Recitation Q&A
  catme             CATME (postings + due dates)
  discussion_board  Discussion Board due dates -- collapsed to ONE event
                     per L0x-L0y block (the block's real final deadline),
                     not one event per lecture/checkpoint (see
                     collapse_discussion_board() for exactly how)

Every synced event also gets two VALARM reminders (1 week before, 1 day
before) -- see ALARM_OFFSETS / set_alarms() / sync_alarms().
Your choices are saved to calendar_<year>_categories.json and reused on
later runs. Anything Canvas adds that doesn't match a known category is
always included (and printed as a warning, since it means Canvas changed
in a way this script doesn't recognize yet).

Sync semantics (per Canvas event, keyed by its stable Canvas UID):
  Canvas event never seen before, category ON     -> ADDED to your calendar
  Canvas event never seen before, category OFF    -> skipped, never added
  Canvas event changed since last sync, your copy -> UPDATED (Canvas wins)
    is untouched since that same sync
  Canvas event changed since last sync, but your   -> CONFLICT: reported,
    copy was ALSO edited since then                    left untouched
  Canvas event removed/cancelled, your copy is     -> REMOVED from your
    untouched since last sync                          calendar
  Canvas event removed/cancelled, but your copy    -> CONFLICT: reported,
    was edited since last sync                         left untouched
  A category you turn OFF that already has events  -> REMOVED (if you
  in your calendar from an earlier sync                haven't edited them;
                                                        else CONFLICT)
  Anything Canvas hasn't touched since last sync    -> left exactly as-is
  Events you added yourself in the calendar (no     -> never touched
    matching Canvas UID, ever)

A CONFLICT never overwrites or deletes your edit automatically -- it is
only ever reported, for you to resolve by hand in Nextcloud.

State: a snapshot file records Canvas's content as of the last sync, so
the diff is against "what Canvas looked like last time", not against your
possibly-edited calendar. First run treats every included-category Canvas
event as new, which is exactly the initial import.

Usage:  canvas.api.calendar.sync.py [--dry-run] [--verbose] [--reconfigure]
                                 [--calendar-name NAME] [--target caldav|local]
                                 [--course-id ID] [--course-config FILE] [--year YYYY]
Input:  the Canvas course ICS feed (canvas.course.json "calendar_feed_url", or
        --feed-url / --feed-file); koan's CalDAV config/secret;
        calendar_<year>_categories.json; paths.calendar_dir, paths.schedule_tsv,
        calendar_name and course_tag in canvas.course.json
Output: creates/updates/deletes events in the target Nextcloud calendar (or
        a local .ics with --target local); rewrites the snapshot .ics;
        prints an ADDED/UPDATED/REMOVED/CONFLICT summary to stdout
Exit:   0 = synced cleanly, 2 = synced but conflicts need manual review,
        1 = error

Author: M. Gilchrist (with Claude Code)
Created: 2026-09-03   Revised: 2026-10-13
Version: 2.4.0 (2026-10-13: course-neutral -- feed URL, paths, calendar name and
         the [COURSE] title tag come from canvas.course.json;
         2.3.0 2026-09-03: direct CalDAV writes + category filtering,
         replacing the v1 "re-import a local .ics by hand" workflow;
         2.1.0 same day: collapse discussion_board to one event per block;
         2.2.0 same day: standing 7-day/1-day-before VALARM reminders on
         every synced event, backfilled onto already-synced ones too;
         2.3.0 same day: hw_posting category -- instructor-facing HW
         posting-date reminders, sourced from schedule_2026.tsv since
         Canvas has no representation of them at all)
"""

import argparse
import csv
import json
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canvas_course  # noqa: E402
from icalendar import Alarm, Calendar, Event  # noqa: E402

# Set in main() from canvas.course.json (no built-in default course):
#   CAL_DIR       paths.calendar_dir  (snapshot, category choices, local .ics)
#   SCHEDULE_PATH paths.schedule_tsv  (Homework POSTING dates, an instructor
#                 task with no Canvas representation -- Canvas only ever shows
#                 student-facing due dates -- so they are read from the
#                 schedule's hw_posted column instead)
#   TAG           course_tag, the "[BIOL240]" suffix Canvas puts on titles
CAL_DIR = SCHEDULE_PATH = TAG = None

# DTSTAMP/SEQUENCE always change on every Canvas/CalDAV re-generation and
# carry no content, so they are excluded from the "did this event actually
# change" comparison. VALARM is deliberately excluded too -- alarms are our
# own standing reminder policy (see ALARM_OFFSETS), not Canvas content, so
# they must never trigger an UPDATE/CONFLICT on their own; see sync_alarms().
COMPARE_FIELDS = ["SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION"]

# Standing reminder policy applied to every synced event: a heads-up a week
# out, and a last call the day before. Deliberately NOT worded "due" --
# this same policy also reminds about instructor tasks (e.g. homework
# posting dates), which aren't a "due date" from MG's own perspective.
ALARM_OFFSETS = [
    (timedelta(days=-7), "1 week before"),
    (timedelta(days=-1), "1 day before"),
]


def set_alarms(comp):
    """Replace comp's VALARMs (if any) with the standing ALARM_OFFSETS set.
    Idempotent -- safe to call on every event on every run."""
    comp.subcomponents = [c for c in comp.subcomponents if c.name != "VALARM"]
    for trigger, desc in ALARM_OFFSETS:
        alarm = Alarm()
        alarm.add("ACTION", "DISPLAY")
        alarm.add("DESCRIPTION", desc)
        alarm.add("TRIGGER", trigger)
        comp.add_component(alarm)


def has_current_alarms(comp):
    triggers = {a.get("TRIGGER").dt for a in comp.subcomponents if a.name == "VALARM"}
    return triggers == {t for t, _ in ALARM_OFFSETS}

# Grounded in the actual F.2026 feed (102 events, checked 2026-09-03): every
# title matches exactly one of these, tested in order, except two stray
# bare "L05:"/"L26:" lecture markers that fall through to the discussion_board
# catch-all (any title starting with a lecture number).
CATEGORIES = [
    ("hw_posting", "Homework posting reminders (YOUR task -- release the next "
                    "HW; not a student due date)",
     re.compile(r"^POST ")),
    ("exams", "Exams",
     re.compile(r"^(Exam|Final Exam)")),
    ("hw_individual", "Individual Homework (due dates)",
     re.compile(r"^HW\d+:")),
    ("hw_team", "Team Homework (due dates)",
     re.compile(r"^Team Homework")),
    ("recitation", "Recitation Q&A",
     re.compile(r"Recitation Q&A")),
    ("catme", "CATME (postings + due dates)",
     re.compile(r"^CATME")),
    ("discussion_board", "Discussion Board due dates (one per group, not per lecture)",
     re.compile(r"^L\d")),
]
CATEGORY_KEYS = [key for key, _, _ in CATEGORIES]


def classify(title):
    for key, _, pattern in CATEGORIES:
        if pattern.search(title):
            return key
    return "other"


def load_hw_posting_events(schedule_path=None):
    """Read the hw_posted column of schedule_2026.tsv and synthesize one
    all-day event per row (e.g. "POST HW04:L8-9 [<TAG>]" on 2026-09-15).
    Returns {} if the schedule file isn't found, rather than erroring --
    this reminder category is a nice-to-have layered on top of the Canvas
    sync, not something that should block it."""
    schedule_path = schedule_path or SCHEDULE_PATH
    events = {}
    if not schedule_path.exists():
        return events
    with open(schedule_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            value = (row.get("hw_posted") or "").strip()
            date_str = (row.get("date") or "").strip()
            if not value or not date_str:
                continue
            token = value.split(":", 1)[0].strip()
            uid_token = re.sub(r"[^A-Za-z0-9]+", "-", token).strip("-").lower()
            y, m, d = (int(part) for part in date_str.split("-"))
            ev = Event()
            ev.add("UID", f"hw-posting-{uid_token}")
            ev.add("SUMMARY", f"POST {value} [{TAG}]")
            ev.add("DTSTART", date(y, m, d))
            events[str(ev.get("UID"))] = ev
    return events


def collapse_discussion_board(canvas_events):
    """Replace the many raw per-lecture/marker discussion_board events Canvas
    emits for each L0x-L0y block with ONE synthetic event per block, dated at
    the block's real final deadline.

    Canvas emits, per block: an "X: Initial Posts" marker (when the topic
    opens -- NOT a due date), an "X: Final Posts" marker (usually, but not
    always, present), plus a "Reply to Topic" and "Required Replies" event
    for EVERY lecture in the block. All of those collapse into one event
    per block here, because the individual per-lecture entries are all
    softer checkpoints toward the same shared block deadline (verified
    2026-09-03 against the live F.2026 feed: every lecture in a block
    shares an identical Required Replies due timestamp).

    The "X: Final Posts" marker's own DTSTART is used verbatim as that
    block's due date when present. The one block observed without a Final
    Posts marker (L01-L04, a leftover gap in how the course's first block
    was set up) falls back to the shared Required Replies timestamp among
    that block's own lecture range, parsed from the block's own "X:
    Initial Posts" label -- and raises if that fallback ever finds
    disagreeing timestamps, rather than silently guessing one.
    """
    other = {}
    db_events = {}
    for uid, ev in canvas_events.items():
        if classify(str(ev.get("SUMMARY", ""))) == "discussion_board":
            db_events[uid] = ev
        else:
            other[uid] = ev

    final_posts = {}
    initial_posts = {}
    for ev in db_events.values():
        title = str(ev.get("SUMMARY", ""))
        m = re.match(rf"^(L\d+-L\d+): Final Posts \[{re.escape(TAG)}\]$", title)
        if m:
            final_posts[m.group(1)] = ev
        m = re.match(rf"^(L\d+-L\d+): Initial Posts \[{re.escape(TAG)}\]$", title)
        if m:
            initial_posts[m.group(1)] = ev

    blocks = {label: ev.get("DTSTART").dt for label, ev in final_posts.items()}

    for label in initial_posts:
        if label in blocks:
            continue
        lo, hi = (int(n) for n in re.match(r"L(\d+)-L(\d+)", label).groups())
        candidates = []
        for ev in db_events.values():
            m = re.match(r"^L(\d+):.*Required Replies", str(ev.get("SUMMARY", "")))
            if m and lo <= int(m.group(1)) <= hi:
                candidates.append(ev.get("DTSTART").dt)
        if not candidates:
            continue
        if len(set(candidates)) > 1:
            raise RuntimeError(
                f"discussion_board block {label} has disagreeing Required "
                f"Replies due dates and no Final Posts marker to resolve it: "
                f"{sorted(set(candidates))}"
            )
        blocks[label] = candidates[0]

    for label, dt in blocks.items():
        synthetic = Event()
        synthetic.add("UID", f"db-block-{label}")
        synthetic.add("SUMMARY", f"{label}: Discussion Board due [{TAG}]")
        synthetic.add("DTSTART", dt)
        other[f"db-block-{label}"] = synthetic

    return other


def load_calendar_from_url(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return Calendar.from_ical(resp.read())


def load_calendar_from_file(path):
    return Calendar.from_ical(path.read_bytes())


def new_empty_calendar(like=None):
    cal = Calendar()
    cal.add("VERSION", "2.0")
    cal.add("PRODID", f"-//{TAG.lower()}//canvas.api.calendar.sync.py//EN")
    cal.add("CALSCALE", "GREGORIAN")
    if like is not None and like.get("X-WR-CALNAME"):
        cal.add("X-WR-CALNAME", str(like.get("X-WR-CALNAME")))
    return cal


def events_by_uid(cal):
    return {str(c.get("UID")): c for c in cal.walk("VEVENT")}


def fingerprint(ev):
    return tuple(str(ev.get(f)) if ev.get(f) is not None else "" for f in COMPARE_FIELDS)


def label(ev):
    summary = str(ev.get("SUMMARY", "(no title)"))
    dtstart = ev.get("DTSTART")
    when = dtstart.dt.isoformat() if dtstart is not None else "?"
    return f"{summary} [{when}]"


def wrap_single_event(comp):
    cal = new_empty_calendar()
    cal.add_component(comp)
    return cal.to_ical()


# ---------------------------------------------------------------------------
# Category wizard
# ---------------------------------------------------------------------------

def load_category_config(path):
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_category_config(path, config):
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


def run_category_wizard(canvas_events, existing_config, ask=input, out=print):
    from collections import defaultdict
    counts = defaultdict(list)
    for ev in canvas_events.values():
        counts[classify(str(ev.get("SUMMARY", "")))].append(str(ev.get("SUMMARY", "")))

    out("Choose which categories of Canvas events to bring into your calendar.")
    config = {}
    for key, display, _ in CATEGORIES:
        examples = counts.get(key, [])
        default = True if existing_config is None else existing_config.get(key, True)
        out(f"\n{display} -- {len(examples)} events")
        for ex in examples[:3]:
            out(f"    e.g. {ex}")
        prompt = f"  Include? [{'Y/n' if default else 'y/N'}] "
        while True:
            ans = ask(prompt).strip().lower()
            if ans == "":
                config[key] = default
                break
            if ans in ("y", "yes"):
                config[key] = True
                break
            if ans in ("n", "no"):
                config[key] = False
                break
            out("  please answer y or n")

    other = counts.get("other", [])
    if other:
        out(f"\nNote: {len(other)} event(s) didn't match any known category and "
            f"will always be included (Canvas added something new):")
        for ex in other[:5]:
            out(f"    {ex}")

    return config


# ---------------------------------------------------------------------------
# Sync targets: a small common interface (events/add/update/remove/save) so
# the diff engine below works identically against a live CalDAV calendar or
# a local .ics file.
# ---------------------------------------------------------------------------

class LocalIcsTarget:
    def __init__(self, path, like=None):
        self.path = path
        self.cal = (load_calendar_from_file(path) if path.exists()
                    else new_empty_calendar(like=like))

    def events(self):
        return events_by_uid(self.cal)

    def add(self, comp):
        self.cal.add_component(comp)

    def update(self, uid, comp):
        self.remove(uid)
        self.cal.add_component(comp)

    def remove(self, uid):
        self.cal.subcomponents = [
            c for c in self.cal.subcomponents
            if not (c.name == "VEVENT" and str(c.get("UID")) == uid)
        ]

    def save(self):
        CAL_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self.cal.to_ical())


class CalDavTarget:
    """Live Nextcloud calendar over CalDAV, using koan's stored credentials."""

    def __init__(self, calendar_name, create=True, verbose=False):
        import caldav
        from koan.config import KoanConfig

        cfg = KoanConfig.load()
        if not cfg.caldav.url or not cfg.caldav.password:
            raise RuntimeError(
                "CalDAV isn't configured -- this reuses koan's credentials "
                "(~/.config/koan/config.yaml + the 'koan' keyring secret "
                "'caldav-password'). Run `koan config` / `koan config "
                "set-secret` first."
            )
        client = caldav.DAVClient(url=cfg.caldav.url, username=cfg.caldav.username,
                                   password=cfg.caldav.password)
        principal = client.principal()
        found = None
        for cal in principal.calendars():
            if (cal.name or "").lower() == calendar_name.lower():
                found = cal
                break
        if found is None:
            if not create:
                raise RuntimeError(f"calendar '{calendar_name}' not found")
            if verbose:
                print(f"creating new calendar '{calendar_name}' ...")
            found = principal.make_calendar(name=calendar_name)
        self.cal = found
        self._live = {}
        for ev in self.cal.events():
            comp = ev.icalendar_component
            self._live[str(comp.get("UID"))] = ev

    def events(self):
        return {uid: ev.icalendar_component for uid, ev in self._live.items()}

    def add(self, comp):
        ev = self.cal.save_event(wrap_single_event(comp))
        self._live[str(comp.get("UID"))] = ev

    def update(self, uid, comp):
        ev = self._live[uid]
        ev.data = wrap_single_event(comp)
        ev.save()
        self._live[uid] = ev

    def remove(self, uid):
        self._live[uid].delete()
        del self._live[uid]

    def save(self):
        pass  # CalDAV writes are immediate; nothing to flush


# ---------------------------------------------------------------------------
# Diff engine, shared by both target types
# ---------------------------------------------------------------------------

def sync(canvas_events, snapshot_events, target, category_config, dry_run, verbose, out=print):
    live_events = target.events()
    added, updated, removed, conflicts, unchanged = [], [], [], [], []

    for uid in sorted(set(canvas_events) | set(snapshot_events)):
        in_canvas = uid in canvas_events
        in_snapshot = uid in snapshot_events
        in_live = uid in live_events

        title = str((canvas_events.get(uid) or snapshot_events.get(uid)).get("SUMMARY", ""))
        category = classify(title)
        included = category_config.get(category, True)

        if in_canvas and not in_snapshot:
            if not included:
                continue
            if in_live:
                unchanged.append((uid, "already present"))
            else:
                if not dry_run:
                    target.add(canvas_events[uid])
                added.append((uid, label(canvas_events[uid])))
            continue

        if in_canvas and in_snapshot:
            canvas_changed = fingerprint(canvas_events[uid]) != fingerprint(snapshot_events[uid])

            if not included:
                if in_live:
                    user_edited = fingerprint(live_events[uid]) != fingerprint(snapshot_events[uid])
                    if user_edited:
                        conflicts.append((uid, f"{label(canvas_events[uid])} -- "
                                                f"category excluded, but you edited it"))
                    else:
                        if not dry_run:
                            target.remove(uid)
                        removed.append((uid, f"{label(canvas_events[uid])} (category excluded)"))
                continue

            if not canvas_changed:
                continue
            user_deleted = not in_live
            user_edited = in_live and fingerprint(live_events[uid]) != fingerprint(snapshot_events[uid])
            if user_deleted or user_edited:
                why = "you deleted it" if user_deleted else "you edited it"
                conflicts.append((uid, f"{label(canvas_events[uid])} -- canvas changed it, but {why}"))
            else:
                if not dry_run:
                    target.update(uid, canvas_events[uid])
                updated.append((uid, label(canvas_events[uid])))
            continue

        # in_snapshot and not in_canvas: Canvas removed/cancelled this event
        if not in_live:
            unchanged.append((uid, "already absent"))
            continue
        user_edited = fingerprint(live_events[uid]) != fingerprint(snapshot_events[uid])
        if user_edited:
            conflicts.append((uid, f"{label(snapshot_events[uid])} -- canvas removed it, but you edited it"))
        else:
            if not dry_run:
                target.remove(uid)
            removed.append((uid, label(snapshot_events[uid])))

    def report(title, items):
        out(f"{title} ({len(items)}):")
        for _, msg in items:
            out(f"  - {msg}")

    if verbose:
        report("added", added)
        report("updated", updated)
        report("removed", removed)
        report("conflicts (left untouched, resolve by hand)", conflicts)
        out(f"unchanged: {len(unchanged)}")
    else:
        out(f"added={len(added)} updated={len(updated)} removed={len(removed)} "
            f"conflicts={len(conflicts)} unchanged={len(unchanged)}")
        for _, msg in conflicts:
            out(f"  CONFLICT: {msg}")

    if not dry_run:
        target.save()

    return {"added": added, "updated": updated, "removed": removed,
            "conflicts": conflicts, "unchanged": unchanged}


def sync_alarms(canvas_events, target, category_config, dry_run, verbose, out=print):
    """Backfill the standing ALARM_OFFSETS reminders onto events that are
    already live but predate this reminder policy (or otherwise lost their
    alarms). Runs independently of sync()'s change detection -- VALARM is
    excluded from fingerprint() on purpose, so an unchanged event never
    gets touched by the ADD/UPDATE/CONFLICT logic above, and would
    otherwise never pick up alarms added after it was first synced.

    Only touches an event when its non-alarm content still exactly matches
    the Canvas source (i.e. NOT a conflict candidate) -- an event you've
    edited, or one sync() just flagged as a conflict, is left alone here
    too, so this never overwrites your edit "just to fix the alarms."
    """
    live_events = target.events()
    fixed = []
    for uid, ev in canvas_events.items():
        category = classify(str(ev.get("SUMMARY", "")))
        if not category_config.get(category, True):
            continue
        if uid not in live_events:
            continue
        if fingerprint(live_events[uid]) != fingerprint(ev):
            continue  # sync() either already updated it, or it's a conflict -- leave it
        if has_current_alarms(live_events[uid]):
            continue
        if not dry_run:
            target.update(uid, ev)
        fixed.append((uid, label(ev)))

    if fixed:
        out(f"alarms added to {len(fixed)} already-synced event(s):")
        if verbose:
            for _, msg in fixed:
                out(f"  - {msg}")

    if fixed and not dry_run:
        target.save()

    return fixed


def main():
    ap = argparse.ArgumentParser(
        description="Sync new/changed Canvas course calendar events, filtered by "
                     "category, directly into a Nextcloud calendar over CalDAV."
    )
    ap.add_argument("--target", choices=["caldav", "local"], default="caldav",
                     help="write to a live CalDAV calendar (default) or a local .ics file")
    ap.add_argument("--calendar-name", default=None,
                     help="CalDAV calendar name (default: calendar_name in "
                          "canvas.course.json); created if it doesn't exist")
    ap.add_argument("--local-static", default=None,
                     help="path for --target local (default: <calendar_dir>/calendar_<year>.ics)")
    ap.add_argument("--snapshot", default=None,
                     help="last-synced Canvas snapshot, internal state (default: "
                          "<calendar_dir>/calendar_<year>_canvas-snapshot.ics)")
    ap.add_argument("--category-config", default=None,
                     help="where category on/off choices are saved (default: "
                          "<calendar_dir>/calendar_<year>_categories.json)")
    ap.add_argument("--reconfigure", action="store_true",
                     help="re-run the category selection wizard")
    ap.add_argument("--feed-url", default=None,
                     help="Canvas course ICS feed URL (default: calendar_feed_url "
                          "in canvas.course.json)")
    ap.add_argument("--feed-file", default=None,
                     help="read the Canvas feed from a local file instead of the URL (testing)")
    ap.add_argument("-n", "--dry-run", action="store_true",
                     help="report what would change without writing/creating/deleting anything")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every event checked")
    canvas_course.add_arguments(ap)
    args = ap.parse_args()

    global CAL_DIR, SCHEDULE_PATH, TAG
    try:
        cfg = canvas_course.resolve(args)
        CAL_DIR = Path(cfg.path("calendar_dir"))
        SCHEDULE_PATH = Path(cfg.path("schedule_tsv"))
    except canvas_course.CourseConfigError as e:
        sys.exit(f"[FAIL] {e}")
    TAG = cfg.data.get("course_tag")
    if not TAG:
        sys.exit(f"[FAIL] no \"course_tag\" in {cfg.source or 'canvas.course.json'}")
    year = cfg.year
    args.calendar_name = args.calendar_name or cfg.data.get("calendar_name") or TAG
    args.local_static = args.local_static or str(CAL_DIR / f"calendar_{year}.ics")
    args.snapshot = args.snapshot or str(CAL_DIR / f"calendar_{year}_canvas-snapshot.ics")
    args.category_config = (args.category_config
                            or str(CAL_DIR / f"calendar_{year}_categories.json"))
    if not args.feed_url and not args.feed_file:
        args.feed_url = cfg.data.get("calendar_feed_url")
        if not args.feed_url:
            sys.exit("[FAIL] no feed: pass --feed-url/--feed-file or set "
                     f"\"calendar_feed_url\" in {cfg.source or 'canvas.course.json'}")

    try:
        canvas_cal = (load_calendar_from_file(Path(args.feed_file)) if args.feed_file
                      else load_calendar_from_url(args.feed_url))
    except Exception as e:
        print(f"error: could not fetch/parse the Canvas feed: {e}", file=sys.stderr)
        return 1
    canvas_events = collapse_discussion_board(events_by_uid(canvas_cal))
    canvas_events.update(load_hw_posting_events())
    for ev in canvas_events.values():
        set_alarms(ev)

    category_path = Path(args.category_config)
    existing_cfg = load_category_config(category_path)
    if existing_cfg is None or args.reconfigure:
        cfg = run_category_wizard(canvas_events, existing_cfg)
        if not args.dry_run:
            category_path.parent.mkdir(parents=True, exist_ok=True)
            save_category_config(category_path, cfg)
            print(f"saved category choices to {category_path}")
        else:
            print("(dry run -- category choices not saved)")
    else:
        cfg = existing_cfg

    snapshot_path = Path(args.snapshot)
    snapshot_events = (events_by_uid(load_calendar_from_file(snapshot_path))
                        if snapshot_path.exists() else {})

    try:
        if args.target == "caldav":
            target = CalDavTarget(args.calendar_name, verbose=args.verbose)
        else:
            target = LocalIcsTarget(Path(args.local_static), like=canvas_cal)
    except Exception as e:
        print(f"error: could not reach the target calendar: {e}", file=sys.stderr)
        return 1

    result = sync(canvas_events, snapshot_events, target, cfg, args.dry_run, args.verbose)
    sync_alarms(canvas_events, target, cfg, args.dry_run, args.verbose)

    if args.dry_run:
        print("(dry run -- no changes made)")
    else:
        # Saved as the COLLAPSED set (what was actually diffed/synced), not
        # the raw Canvas feed -- otherwise the synthetic per-block discussion
        # board events would never appear in next run's snapshot, and this
        # script would stop detecting when Canvas moves a block's due date.
        snapshot_cal = new_empty_calendar(like=canvas_cal)
        for comp in canvas_events.values():
            snapshot_cal.add_component(comp)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(snapshot_cal.to_ical())
        print(f"wrote {snapshot_path}")

    return 2 if result["conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main())
