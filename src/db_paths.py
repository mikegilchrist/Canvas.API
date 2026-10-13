#!/usr/bin/env python3
# ============================================================================
# Module:       db_paths.py
# Version:      1.2.0
# Created:      2026-10-05
# Revised:      2026-10-12 -- blocks re-cut (MG): 1-4, 5-9, 10-13, 13-18, 19-22,
#               23-26.  Board 13 is in TWO blocks: the original L13 board (now
#               "L13 - DB Group L10-13", lecture taught after the block closed)
#               and the new "L13 - DB Group 13-18".  block_of_title() tells them
#               apart; every other board is resolved by number.
# Revised:      2026-10-13 -- generalised: the course root and the block table now come
#               from canvas.course.json (paths.course_root, db_blocks); moved to
#               Canvas.API/src, with a symlink left in Genetics/bin.
# Revised:      2026-10-05
# Purpose:      Where the discussion board (DB) files live.  Defined ONCE.
#
# LAYOUT (F.2026 on).  One folder per posting block:
#
#   F.<year>/Discussion.Boards/<BLOCK>/Posts/db.posts_<year>_L##.json
#                                      /Posts/Archive/
#                                      /Reports/   block-named reports,
#                                                  ledgers, grading html, TSVs
#                                      /Grading/   returned TA json files
#                                      /Notes/
#   F.<year>/Discussion.Boards/Reports/    cross-block files only
#                                          (db.topictext*, backups, ...)
#   F.<year>/Discussion.Boards/Templates/  shared templates
#
# BLOCK is one of L01-L04, L05-L09, L10-L13, L13-L18, L19-L22, L23-L26 (board
# numbers 1-4, 5-9, 10-13, 13-18, 19-22, 23-26).
#
# NAME.  Underscores and no periods so it is importable, like
# db_detect_ai_artifacts.py.  NOT A SCRIPT except for --self-test.
#
# Usage:        import db_paths       (with bin/ on sys.path)
#               db_paths.py --self-test
# ============================================================================
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import canvas_course  # noqa: E402


def _load():
    """Course root and block table from canvas.course.json (found from the cwd,
    else from the directory of the invoked script).  Fails loudly: there is no
    built-in course."""
    path = canvas_course.find_config()
    if path is None:
        raise canvas_course.CourseConfigError(
            "db_paths: no canvas.course.json found (searched from the current "
            "directory and from the invoked script's directory)")
    data = canvas_course.load_config(path)
    try:
        root = os.path.expanduser(data["paths"]["course_root"])
        ranges = [tuple(r) for r in data["db_blocks"]]
    except KeyError as e:
        raise canvas_course.CourseConfigError(
            f"db_paths: {path} lacks {e} (need paths.course_root and db_blocks)")
    return root, ranges


ROOT, BLOCK_RANGES = _load()   # BLOCK_RANGES: (first board, last board) per block, in order
BLOCKS = ["L%02d-L%02d" % r for r in BLOCK_RANGES]
# name -> (lo, hi): THE block table.  Scripts import this instead of keeping
# their own copy (six copies drifted apart before).
BLOCK_BOUNDS = dict(zip(BLOCKS, BLOCK_RANGES))


def block_of_board(n):
    """Block of board n.  Board 13 is in two blocks; this returns the FIRST
    (L10-L13).  Use block_of_title() when the title is known."""
    n = int(n)
    for (lo, hi), name in zip(BLOCK_RANGES, BLOCKS):
        if lo <= n <= hi:
            return name
    raise ValueError("board %r is in no block" % n)


def block_of_title(title):
    """Block of a board from its Canvas title.  The two L13 boards are told
    apart by their titles ("L13 - DB Group L10-13" vs "L13 - DB Group 13-18");
    anything else is resolved by its number.  None if not a board title."""
    m = re.match(r"^L(\d\d)\b", title or "")
    if not m:
        return None
    n = int(m.group(1))
    if n == 13:
        return "L13-L18" if re.search(r"\b13-18\b", title) else "L10-L13"
    return block_of_board(n)


def boards_of_block(block):
    m = re.fullmatch(r"L(\d\d)-L(\d\d)", block)
    if not m or block not in BLOCKS:
        raise ValueError("unknown block %r (one of %s)"
                         % (block, ", ".join(BLOCKS)))
    return list(range(int(m.group(1)), int(m.group(2)) + 1))


def db_root(year):
    return os.path.join(ROOT, "F.%s" % year, "Discussion.Boards")


def block_dir(year, block):
    boards_of_block(block)          # validates
    return os.path.join(db_root(year), block)


def posts_dir(year, block):
    return os.path.join(block_dir(year, block), "Posts")


def reports_dir(year, block):
    return os.path.join(block_dir(year, block), "Reports")


def grading_dir(year, block):
    return os.path.join(block_dir(year, block), "Grading")


def notes_dir(year, block):
    return os.path.join(block_dir(year, block), "Notes")


def all_posts_dirs(year):
    """Every existing <BLOCK>/Posts directory, in block order."""
    return [d for d in (posts_dir(year, b) for b in BLOCKS)
            if os.path.isdir(d)]


def templates_dir(year):
    return os.path.join(db_root(year), "Templates")


def shared_reports_dir(year):
    """Top-level Reports/, for files that belong to no single block."""
    return os.path.join(db_root(year), "Reports")


def _self_test():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print("  %s  %s" % ("ok  " if cond else "FAIL", msg))
        ok = ok and bool(cond)

    expect = {1: "L01-L04", 4: "L01-L04", 5: "L05-L09", 9: "L05-L09",
              10: "L10-L13", 12: "L10-L13", 14: "L13-L18", 18: "L13-L18",
              19: "L19-L22", 22: "L19-L22", 23: "L23-L26", 26: "L23-L26"}
    check(all(block_of_board(n) == b for n, b in expect.items()),
          "block_of_board over every boundary")
    try:
        block_of_board(27)
        check(False, "board 27 rejected")
    except ValueError:
        check(True, "board 27 rejected")
    allb = sorted(sum((boards_of_block(b) for b in BLOCKS), []))
    check(sorted(set(allb)) == list(range(1, 27))
          and allb.count(13) == 2 and len(allb) == 27,
          "blocks cover boards 1-26, with 13 in exactly two blocks")
    check(all(block_of_board(n) == b for b in BLOCKS
              for n in boards_of_block(b) if n != 13), "round trip")
    check(block_of_title("L13 - DB Group L10-13") == "L10-L13"
          and block_of_title("L13 - DB Group 13-18") == "L13-L18"
          and block_of_title("L13: Bacterial Genetics II") == "L10-L13"
          and block_of_title("L14: DNA Structure") == "L13-L18"
          and block_of_title("L19: Chromosome Structure") == "L19-L22"
          and block_of_title("Course Structure") is None,
          "block_of_title separates the two L13 boards")
    try:
        boards_of_block("L01-L05")
        check(False, "bad block rejected")
    except ValueError:
        check(True, "bad block rejected")
    check(posts_dir("2026", "L01-L04") ==
          ROOT + "/F.2026/Discussion.Boards/L01-L04/Posts", "posts_dir")
    check(reports_dir("2026", "L05-L09").endswith("/L05-L09/Reports"),
          "reports_dir")
    check(grading_dir("2026", "L05-L09").endswith("/L05-L09/Grading"),
          "grading_dir")
    check(notes_dir("2026", "L05-L09").endswith("/L05-L09/Notes"),
          "notes_dir")
    check(templates_dir("2026").endswith("/Discussion.Boards/Templates"),
          "templates_dir")
    check(shared_reports_dir("2026").endswith("/Discussion.Boards/Reports"),
          "shared_reports_dir")
    dirs = all_posts_dirs("2026")
    check(all(os.path.isdir(d) for d in dirs),
          "all_posts_dirs exist: %d" % len(dirs))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    print("import db_paths; run with --self-test")
    sys.exit(0)
