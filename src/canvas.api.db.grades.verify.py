#!/usr/bin/env python3
# CLI wrapper for db_grades_verify.py (see its header).  Read-only unless
# --repair --apply.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import db_grades_verify  # noqa: E402

if __name__ == "__main__":
    sys.exit(db_grades_verify.main())
