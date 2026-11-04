# Parent/half experiment on the SANDBOX course (198177) -- 2026-11-04

LIVE scripts, not pytest. Each refuses any course id other than 198177.

- `sbx.py publish|unpublish|student` -- the Test Student endpoint needs the
  course published (use GET /courses/ID/student_view_student; POST is 404).
  Unpublish when done.
- `mkfixture.py --k N --start S` -- creates N checkpointed boards via GraphQL
  createDiscussionTopic. The parent assignment must NOT carry pointsPossible
  ("Cannot set points_possible in the parent assignment for checkpoints").
- `race.py --rounds R [--conc-only]` -- writes both halves of the Test Student
  on 6 boards concurrently (6 workers) vs serially, reads back, counts
  parent != sum(halves).

Result 2026-11-04 (8 rounds x 6 boards): concurrent 41/48 mismatches, serial
0/48. Mismatches persisted after 20 s (not eventual consistency).
`canvas.api.db.grades.verify.py --repair --apply` took 6 -> 0.
End to end: `canvas.api.db.grades.upload.py` default (--workers 1) exit 0;
`--workers 6` exit 1 with 6 mismatches.

Fixture boards in 198177: "VERIFY-TEST board 01..06", assignments 2957211,
2957214, 2957217, 2957220, 2957223, 2957226 (topics 2145965, 2145967-2145971).
