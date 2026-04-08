# Changelog

## 2026-04-08 — Gradebook fetcher + upload_quiz group placement
- New CLI: `src/canvas.api.fetch_gradebook.py` builds a Canvas-format
  gradebook CSV via the API (per-course grade export is not exposed via
  the Reports API; this fills that gap). Uses bulk
  `students/submissions?student_ids[]=all` for efficiency.
- `src/canvas.api.upload_quiz.py`: added `--group GROUP_ID_OR_NAME`
  (default `Quizzes`). Canvas's content migration places imported quizzes
  in `Imported Assignments` by default; this flag moves the new quiz to
  the right group automatically after upload.

## 2026-04-07 — Syllabus page update support
- Added `get_syllabus_body()` and `update_syllabus_body()` to `src/canvas_api.py`.
  Canvas's Syllabus page lives in the course object's `syllabus_body`
  field (not a wiki page), so it needs `include[]=syllabus_body` on GET
  and `course[syllabus_body]` on PUT.
- New CLI: `src/canvas.api.update_syllabus.py` with `--get`, `--set PATH`,
  and `--replace OLD:NEW` (repeatable literal substring replacement).
- Documented in `CLAUDE.md`.

## 2026-02-02 — Canvas API quiz export updates
- Added `canvasapi` helper example to test `include[]` parameters: `canvasapi/example.py`.
- Requested additional include parameters when fetching submission questions to surface student answers when permitted: updated `modular/src/canvas_api.py` (`submission_answers`, `submission_question_details`).
- Exporter now writes per-quiz JSON output directories: `<outdir>/COURSEID-QUIZID/` (implemented in `modular/src/api_to_json.py`).
- Added `API_ACCESS_NOTES.md` documenting testing steps, required OIT permissions, and next steps.
- Added robust `canvasapi` fallback logic: `canvasapi/example.py` uses SDK requester when available, otherwise falls back to `requests`.

Notes:
- Student answer fields are returned by the API only when the requesting account has appropriate Canvas role permissions (e.g. Instructor/TA or Account Admin) with `view_answer_audits` / grading permissions.
- Next step: Obtain service account or Instructor PAT from OIT and re-run the exporter to verify student responses for sample submissions (target: verify at least 6 students).
