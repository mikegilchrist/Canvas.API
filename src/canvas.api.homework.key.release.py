#!/usr/bin/env python3
# ============================================================================
# Script:       canvas.api.homework.key.release.py
# Version:      1.1.0
# Created:      2026-10-05
# Revised:      2026-10-13
# Purpose:      Make a homework key available only AFTER the deadline and only
#               to teams that have SUBMITTED the homework (syllabus v1.3
#               policy, applies from HW05 onward).
#
# How it works (all standard Canvas, no per-team action):
#   1. The key is uploaded to the "Homework Keys" folder, HIDDEN from the
#      Files list, with "available from" = the assignment's due date, so it
#      cannot be opened by anyone before the deadline.
#   2. A module "HWnn Key" is created with sequential progress required and two
#      items: the homework assignment (requirement: must submit) and then the
#      key file (requirement: must view).  The key item stays locked for a
#      student until the assignment requirement is complete, i.e. until that
#      student's team has submitted.  A group submission counts for every
#      member (checked on HW02: 128/128 member rows 'submitted').
#   A team that submits late sees the key from the moment it submits.
#
# NOT stopped by this: a team passing the key to another team.
# NOT stopped by this: a key link left in the assignment description (HW04's
# has one).  Do not put the key in the description.
#
# Usage:        canvas.api.homework.key.release.py --hw 05 [--course-id N | --course-config FILE] [--key PATH]
#               [--update-description] [--apply]
# Input:        --hw NN   homework number; matches the Canvas assignment whose
#                         name starts with "HWNN"
#               --key     the key PDF
# Output:       dry run prints the plan; --apply creates the file and module
#
# Course:       any course: --course-id / --course-config / canvas.course.json
#               (see canvas_course.py).  Optional config keys: "homework":
#               {"assignment_prefix": "HW", "key_folder": "Homework Keys"}.
#
# Options:
#   -w, --hw NN        homework number (required)
#   -k, --key PATH     key PDF to release; omit to create the module only, then
#                      run again with --key once the key exists (idempotent)
#   -d, --update-description  replace the assignment's Key placeholder with
#                      'Posted in [HWnn Key module] after deadline and team
#                      submission.' (link to the module)
#   -s, --student ID   after --apply, print this student's module progression
#                      (repeatable); use to verify the gate on a real team
#   -a, --apply        make the changes (default: dry run)
#   -v, --verbose      print the API payloads
#   -h, --help         this text
# ============================================================================
import argparse
import json
import mimetypes
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canvas_course                     # noqa: E402
import canvas_http                       # noqa: E402

BASE = COURSE = None      # set in main() from canvas.course.json
FOLDER = None             # Files folder for keys (config homework.key_folder)
PREFIX = None             # assignment name prefix (config homework.assignment_prefix)


def api(tok, path, method="GET", data=None):
    url = BASE + path
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req))


def upload(tok, path, due):
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    init, _ = canvas_http.http_post_json(
        f"{BASE}/api/v1/courses/{COURSE}/files", tok,
        {"name": path.name, "size": len(data), "content_type": ctype,
         "parent_folder_path": FOLDER, "on_duplicate": "overwrite"})
    body, hdrs = canvas_http.http_post_multipart_no_auth(
        init["upload_url"], init.get("upload_params", {}),
        {"file": (path.name, data, ctype)})
    f = json.loads(body) if body else None
    if not f or "id" not in f:
        loc = dict(hdrs or {}).get("Location")
        r = canvas_http.http_get_json(loc, tok)
        f = r[0] if isinstance(r, tuple) else r
    canvas_http.http_put_json(f"{BASE}/api/v1/files/{f['id']}", tok,
                              {"hidden": True, "locked": False,
                               "unlock_at": due})
    return f


def main():
    ap = argparse.ArgumentParser(description="Release a homework key after the "
                                 "deadline, only to submitting teams.")
    ap.add_argument("-w", "--hw", required=True,
                    help="homework number; matches the assignment named <prefix>NN")
    ap.add_argument("-k", "--key", help="key PDF; omit to create the module only")
    ap.add_argument("-d", "--update-description", action="store_true",
                    help="point the assignment's Key section at the module")
    ap.add_argument("-s", "--student", action="append", default=[])
    ap.add_argument("-a", "--apply", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    canvas_course.add_arguments(ap)
    a = ap.parse_args()
    global BASE, COURSE, FOLDER, PREFIX
    cfg, BASE, tok = canvas_course.setup(a)
    COURSE = str(cfg.course_id)
    hwcfg = cfg.data.get("homework", {})
    FOLDER = hwcfg.get("key_folder", "Homework Keys")
    PREFIX = hwcfg.get("assignment_prefix", "HW")
    key = Path(a.key).expanduser() if a.key else None
    if key and not key.is_file():
        print(f"FAIL: key file not found: {key}")
        return 1
    name_re = re.compile(rf"^{re.escape(PREFIX)}{int(a.hw):02d}\b")
    hits = [x for x in api(tok, f"/api/v1/courses/{COURSE}/assignments?per_page=100")
            if name_re.match(x["name"])]
    if len(hits) != 1:
        print(f"FAIL: {len(hits)} assignments match {PREFIX}{int(a.hw):02d}: "
              f"{[x['name'] for x in hits]}")
        return 1
    asg = hits[0]
    due = asg["due_at"]
    if not due:
        print("FAIL: the assignment has no due date, so the key has no unlock time")
        return 1
    mname = f"{PREFIX}{int(a.hw):02d} Key"
    mods = [m for m in api(tok, f"/api/v1/courses/{COURSE}/modules?per_page=100"
                                "&include[]=items") if m["name"] == mname]
    mod = mods[0] if mods else None
    has_file = bool(mod) and any(i["type"] == "File" for i in mod.get("items", []))
    tag = "" if a.apply else "[DRY-RUN] "
    print(f"{tag}assignment : {asg['name']}  (id {asg['id']})  due {due}")
    if mod:
        print(f"{tag}MODULE     : '{mname}' exists (id {mod['id']}), "
              f"{'has' if has_file else 'has no'} key item")
    else:
        print(f"{tag}MODULE     : create '{mname}', sequential progress, published; "
              f"item 1 = the assignment (must submit)")
    if key and has_file:
        print(f"{tag}KEY        : module already has a key item; not touched")
        key = None
    if key:
        print(f"{tag}KEY        : upload {key.name} -> Files/{FOLDER}, hidden, "
              f"available from {due}; item 2 = the file (must view)")
    if a.update_description:
        print(f"{tag}DESCRIPTION: Key section -> 'Posted in [{mname} module] after "
              f"deadline and team submission.' (link to the module)")
    if not a.apply:
        print("\ndry run -- nothing written.  Re-run with --apply")
        return 0
    if not mod:
        mod = api(tok, f"/api/v1/courses/{COURSE}/modules", "POST",
                  {"module[name]": mname, "module[require_sequential_progress]": "true"})
        api(tok, f"/api/v1/courses/{COURSE}/modules/{mod['id']}/items", "POST",
            {"module_item[title]": asg["name"], "module_item[type]": "Assignment",
             "module_item[content_id]": asg["id"], "module_item[position]": 1,
             "module_item[completion_requirement][type]": "must_submit"})
        api(tok, f"/api/v1/courses/{COURSE}/modules/{mod['id']}", "PUT",
            {"module[published]": "true"})
        print(f"created module {mod['id']}")
    if key:
        f = upload(tok, key, due)
        api(tok, f"/api/v1/courses/{COURSE}/modules/{mod['id']}/items", "POST",
            {"module_item[title]": f"{mname}: {key.name}", "module_item[type]": "File",
             "module_item[content_id]": f["id"], "module_item[position]": 2,
             "module_item[completion_requirement][type]": "must_view"})
        print(f"key file id {f['id']} added to module {mod['id']}")
    if a.update_description:
        d = asg.get("description") or ""
        link = f'{BASE}/courses/{COURSE}/modules/{mod["id"]}'
        new = (f'<p>Posted in <a href="{link}">{mname} module</a> after deadline '
               f'and team submission.</p>')
        d2, n = re.subn(r"(<h2>\s*Key\s*</h2>\s*)<p>.*?</p>", lambda m: m.group(1) + new,
                        d, count=1, flags=re.S)
        if n != 1:
            print("FAIL: no '<h2>Key</h2><p>..</p>' section found in the description")
            return 1
        api(tok, f"/api/v1/courses/{COURSE}/assignments/{asg['id']}", "PUT",
            {"assignment[description]": d2})
        print("description updated")
    for sid in a.student:
        items = api(tok, f"/api/v1/courses/{COURSE}/modules/{mod['id']}/items"
                         f"?student_id={sid}&include[]=content_details")
        print(f"student {sid}:",
              [(i["type"], (i.get("completion_requirement") or {}).get("completed"),
                (i.get("content_details") or {}).get("locked_for_user"))
               for i in items])
    return 0


if __name__ == "__main__":
    sys.exit(main())
