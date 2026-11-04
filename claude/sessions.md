# Sessions -- Canvas.API

One row per session that worked this repo. A row is stubbed automatically on compaction and enriched by `/save` and `/summary`. Detail lives in `claude/compact/` and `claude/summaries/`.

| SID | root | active | summary |
|-----|------|--------|---------|
| 30deebc6 | src | 2026-09-07..2026-09-07 | submissions_to_files.py v1.1.0: walk submission_history so re-submitted files are no longer lost (recovered 9 files on BIOL240 HW01); new send_message.py for Canvas Conversations with --dry-run recipient resolution; two API gotchas documented |
| 2cb1be0e | src | 2026-09-09..2026-09-09 | Added canvas.api.clear_late_status.py; cleared late_policy_status on 13 HW01 submissions whose late penalty MG had waived |
| 1f3356f1 | src | 2026-10-06..2026-10-06 | Added canvas.api.update_recitation_page.py (fill Slides/Worksheets/Readings/Key sections, upload files, never publish); used for BIOL240 R08 |
| 284debf9 | src | 2026-10-06..2026-10-06 | submissions_to_files 1.4.0: --sample/--seed/--exclude-group/--include-group for probe pulls of distinct teams |
| 08ecf564 | src | 2026-10-16..2026-10-16 | update_recitation_page.py: --reading and --key-note render one <p> per blank-line-separated block |
| 710e6972 | src | 2026-10-20..2026-10-20 | canvas.api.group.grades.post.py: --sync-scores added; --sync-comments edit param fixed (comment, not comment[text_comment]) |
| de1d6c2d | docs | 2026-10-30..2026-10-30 | docs: grade-write 403 intermittent, gradebook CSV import format, post policy; upload_gradebook worked for CATME III |
| f9953533 | src | 2026-11-03..2026-11-03 | canvas.api.exam.qanda.links.py v1.3.0: several boards per lecture (L13 has two), 'HWnn: Rnn's Assignment' link text from config hw field |
| dca5fb38 | . | 2026-11-04..2026-11-04 | docs: committed grade-write recipes + ledger row; found handoff's push-conflict claim stale (0/0 after fetch) |
| 175ad0d4 | src | 2026-11-04..2026-11-04 | db grades: late_pct in upload effective(); parent/half verifier + repair, serial-write default (race proven on sandbox 198177), sandbox fixture + tests |
