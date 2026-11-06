# How we use the Canvas API for teaching

Status:   DRAFT v0.1 (2026-11-06). Built from a read-only survey of tool headers,
          configs, CLAUDE.md files and docs. No Canvas calls were made. Endpoint
          lists come from grepping script URL strings, so read them as "paths
          present in the script", not a verified call graph.
Courses:  BIOL240 Genetics, Fall 2026 (course 261062, current);
          EEB403/EEB466 Cancer Evolution, Spring 2026 (course 244286);
          and the satellite repos of BIOL240 (see section 2).
Instance: https://utk.instructure.com
Purpose:  One place that says what we do through the API, how it is guarded, what
          depends on what, and what would break if Canvas changed. Written
          partly to assess exposure to the announced API changes (section 8).

Contents
  1. What this is, in one page
  2. Where things live (repos and who owns what)
  3. Plumbing: auth, config, course selection, HTTP
  4. Workflow catalogue: BIOL240 Genetics F2026
  5. Workflow catalogue: Cancer Evolution Sp2026
  6. Safety conventions (writes, grades, publishing)
  7. Data flow: source of truth vs Canvas
  8. Dependencies on Canvas behaviour, and exposure to change
  9. Known fragile points and gaps in this document
 10. Sources

---------------------------------------------------------------------------

## 1. What this is, in one page

1.1. We use Canvas as the student-facing surface and keep the source of truth in
     local repos (LaTeX, markdown, TSV, SQLite, YAML). Tools in this repo
     (`src/canvas.api.*.py`) push content and grades up, and pull state and
     student work down.

1.2. Almost all use is REST plus a little GraphQL, authenticated with one
     personal access token. Nothing uses the Canvas Data 2 / DAP bulk API.

1.3. Most use is manual, per event: per lecture, per recitation, per homework,
     per discussion-board block, per exam. The only scheduled job found
     (`grade.estimate.cron.sh`, Genetics) is on hold.

1.4. Almost every tool is a dry run unless given `--apply` (or `--execute`).
     Writes that students can see (publishing, due dates, revealing grades,
     announcements) are separate, explicit acts.

1.5. Rough shape of use by area (Genetics is the heavy user):

     | Area                          | Genetics F26 | Cancer Sp26 | Direction     |
     |-------------------------------|--------------|-------------|---------------|
     | Lecture pages, slides, modules| yes          | not found   | write         |
     | Recitation pages and files    | yes          | not found   | write         |
     | Syllabus                      | yes          | not found   | write         |
     | Discussion boards (grading)   | yes (heavy)  | read + flag | read/write    |
     | Homework submissions          | yes          | yes         | read          |
     | Team/group grade posting      | yes          | no          | write         |
     | Quizzes (Classic)             | no           | yes (weekly)| write + read  |
     | Exams                         | boards, files| curve column| read/write    |
     | CATME peer-eval grades        | roster, cal. | grade upload| read/write    |
     | Gradebook config/snapshot     | yes          | fetch/upload| read/write    |
     | Calendar / deadlines          | yes          | not found   | write         |
     | Attendance (Roll Call)        | via LTI      | manual CSV  | read          |
     | Announcements                 | inline code  | manual      | write         |

---------------------------------------------------------------------------

## 2. Where things live

2.1. `Canvas.API` (this repo): the shared toolkit. `src/canvas.api.*.py` CLIs plus
     library modules `canvas_http.py`, `canvas_api.py`, `canvas_course.py`,
     `io_utils.py`, `db_paths.py`, `model.py`, `render_html.py`, `sqlite_writer.py`.
     Stdlib only, Python 3.7+.

2.2. `~/Courses/Genetics` (also reachable as `course_biol240`, the same tree):
     the BIOL240 course. `bin/canvas.*.py` are symlinks to the shared tools,
     except `canvas.recitation.align.py` (a one-off repair, not shared) and
     a few Genetics-local scripts that call Canvas: `exam.contested.post.py`,
     `grade.estimate.post.py`, `attendance.rollcall.*`, and
     `F.2026/CATME/Tools/Survey.Results/process.catme.for.canvas.py`.
     Course config: `Genetics/canvas.course.json`.

2.3. `homework_biol240`: the teaching repo for homework. LaTeX masters, keys,
     the TA grading-sheet generator and about 60 scripts. Safe to share with
     TAs; holds no student data. Pulls submissions and builds the grade package.

2.4. `submissions_homework_biol240`: restricted (FERPA). Raw Canvas pulls
     (git-annex), working responses, extracted pages, delivered sheet
     snapshots. No scripts of its own.

2.5. `discussions_biol240`: recitation and discussion materials (worksheets,
     slides, TA notes). No Canvas code of its own; pushes pages through
     `canvas.api.update_recitation_page.py`.

2.6. `exams_biol240`, `exams_eeb403`: exam question databases (SQLite). No Canvas
     API calls found in either.

2.7. `~/Courses/Cancer.Evolution`: the Cancer Evolution course. Config:
     `Cancer.Evolution/canvas.course.json` (the parent of `exams_eeb403`'s).

---------------------------------------------------------------------------

## 3. Plumbing

3.1. Authentication. One personal (manually generated) access token, sent as a
     Bearer header by `canvas_http.py`. Default token file is
     `~/.api/canvas_token` (code default in `canvas_course.py`); `~/.canvas_token`
     is a symlink to it, and the example profile points at the symlink, so both
     names work. File mode is 0600. `--token` and `--token-file` override.
     Setup notes: `README.TOKEN.SETUP.md` (it names `~/.canvas_token`).

3.2. Profile. `~/.canvas.api.conf` (INI) supplies `base_url` and `token_file`.
     Read by `load_profile()` in `io_utils.py`. CLI arguments always override.

3.3. Course selection. `canvas_course.py` finds a `canvas.course.json` by walking
     up from the current directory, then from the invoked script's directory (so
     symlinked tools work from anywhere). `--course-id`, `--course-config` and
     `--year` override. There is no default course; a missing id stops with
     `[FAIL]`. Configs can `extends` another (child wins, dicts merge). Keys
     include `course_id`, `base_url`, `year`, `folders`, `modules`, `repos`, and
     per-tool blocks (`gradebook`, `exam_boards`, `lecture_modules`, ...).
     Known limit: `db_paths` reads its config at import, so `--course-config`
     does not reach the `db.*` scripts that import it at the top.

3.4. HTTP layer. `canvas_http.py`: GET/POST/PUT with auth and pagination, a raw
     no-auth download helper for verifier URLs, and a multipart helper
     (`http_post_multipart_no_auth(url, fields, files=...)`, `files` is a dict
     `{name: (filename, bytes, content_type)}`). The module sets no explicit
     User-Agent; urllib's default is sent.

3.5. GraphQL. `POST /api/graphql` with the same token. Used where REST cannot
     do the job: discussion checkpoints, assignment post policy, reading
     anonymous boards, `postAssignmentGrades`, and some grade writes.

3.6. Claude Code sandbox. GETs to `utk.instructure.com` work inside the
     sandbox. PUT/POST/DELETE were denied there (even with the host allowed),
     so write runs need the sandbox lifted for that command. Pulls also need the
     file hosts `*.canvas-user-content.com` and `*.inscloudgate.net`. Tools that
     call `claude -p` fail inside the sandbox.

3.7. Sandbox course. Course 198177 ("SANDBOX-MIKEG") is the only place for write
     tests; `tests/sandbox.db.parent.half/` refuses any other id.

---------------------------------------------------------------------------

## 4. Workflow catalogue: BIOL240 Genetics F2026

Tool prefix is `canvas.api.` unless shown. W = writes to Canvas, R = read only.

4.1. Lecture pages, slides, modules (W, manual, built at term start from
     `schedule_2026.tsv`).
     - `lecture.pages`: one page per lecture L02-L26 (never L01, which is hand
       built). Endpoints: `courses/{C}/pages` GET/POST/PUT.
     - `lecture.slides`: uploads slide PDFs by folder prefix, edits the pages.
       Endpoint: `courses/{C}/files`.
     - `lecture.modules`: rewires three modules via `modules/{id}/items`. Removes
       module items, never pages.
     - `exam.subheaders`: places exam-scope SubHeaders in modules.
     - Older single-page tool: `update_lecture_page`.

4.2. Recitation pages (W, manual per recitation; R02-R14, module 989578,
     folder 4000104).
     - `recitation.format`, `recitation.tidy`: normalise pages into Readings /
       Slides / Worksheets via `pages/{slug}`.
     - `update_recitation_page`, `material.upload`: upload a file and write the
       page section or assignment description.
     - `place`: replaces a "Not yet posted: <filename>" placeholder.
     - Never changes published state.
     - Posted files are replaced by uploading the same name into the same folder
       with `on_duplicate=overwrite`. That mints a NEW file id (section 8).

4.3. Syllabus (W). `syllabus.publish` uploads new PDFs, repoints the Syllabus
     page, refreshes the L01 handout link, retires the old version. Endpoints:
     `courses/{C}` with `include[]=syllabus_body`, `files`, `pages`.
     `update_syllabus` is the generic view/replace tool.

4.4. Discussion boards, the heaviest area. 26 graded boards in six blocks
     (L01-L04, L05-L09, L10-L13, L13-L18, L19-L22, L23-L26). Each board is a
     group discussion over a group set whose groups are the six recitation
     sections (156 child topics). The loop runs before and after each block's
     deadline.
     - `db.sections.enable|disable`, `group.sync` (W): set up group discussions
       against group sets. Endpoints: `group_categories`, `groups`,
       `discussion_topics`, `assignments`.
     - `db.fetch` (R): walks `group_topic_children` via
       `groups/{gid}/discussion_topics/{cid}/view` into `<BLOCK>/Posts/*.json`.
       Archives the old snapshot first because Canvas keeps no edit history.
     - Offline analysis (no Canvas): `db.analyze`, `db.compare`,
       `db.course.structure.screen`, `db_detect_ai_artifacts`, `db.aiflag.*`,
       `db.grading.sheet` (TA sheets).
     - `db.reply` (W): the only tool that posts replies or comments to students.
       Acts only on rows whose `decision` column is filled, needs `--apply`,
       appends to a ledger.
     - `db.checkpoints` (W, GraphQL): sets points and due dates on the two
       checkpoint halves (`reply_to_topic`, `reply_to_entry`); `--publish` is a
       separate flag. Refuses a board that already has scores.
     - `db.topic.text`, `db.period.sentence` (W): edit board intro text.
     - `db.grades.upload` (W): PUTs `assignments/{a}/submissions/{u}` per half
       plus GraphQL; serial by default; reads back and checks parent == sum of
       halves; exits 1 on mismatch. `db.grades.verify BLOCK [--repair --apply]`
       is the standalone check and repair.
     - Local grade tools (no Canvas): `db.grades.rules.apply`, `db.grades.cap.apply`,
       `db.grades.ta.comments`. The late rule is per block in
       `<BLOCK>/Notes/late.policy.json`.
     - Submission comments on these boards MUST send `comment[group_comment]=false`
       and be read back (`db_grades_verify.verify_comment_privacy`).

4.5. Exams (Canvas side only: contestation boards and posted files; how the
     exams themselves are delivered was not covered by the survey).
     - `exam.contestations.fetch` (R, GraphQL, because the boards allow anonymous
       posts and REST returns 404 for them).
     - `bin/exam.contested.post.py` (W, Genetics-local): posts responses and
       decisions under the instructor's name; dry run unless `--apply`.
     - `exam.posted.sync` (reconcile Canvas PDFs against `Exams/*/posted/`),
       `exam.pdfs.repair` (repoint file ids, drop stale `?verifier=`),
       `exam.qanda.links` (link R06/R12 pages to the Q&A boards).

4.6. Homework.
     - Pull (R): `hw.submissions.retrieve.py` (in `homework_biol240/bin`) wraps
       `submissions_to_files` and calls `assignments/A/submissions`. It
       compares the live submitted list with folders on disk, flags stale
       resubmissions, checks each attachment size, and exits non-zero if
       anything is missing. `canvas.attachment.index.py` records attempt,
       `submitted_at` and user per attachment id. `ingest.canvas.api.py` files
       the pull into `Responses/` keeping the union of a team's attempts.
     - Processing is local: page extraction, page matching, a vision model only
       for handwriting transcription and QC, a self-contained HTML sheet the
       TAs grade in, merge of TA exports into `final.json`. No model grades
       content.
     - Write-back package: `grading.canvas.package.from.final.py` writes files
       only (gradebook CSV, comments JSON, a review markdown). It fails on
       suspect comments (very short, numeric, "not found", ending in "?").
     - Post (W): `group.grades.post` with `--apply`, in order: set
       `points_possible` and percent grading; set post policy to manual via
       GraphQL BEFORE any grade is written (REST 404s); one PUT per group with
       `posted_grade` and `comment[text_comment]` with `group_comment=true`
       (deliberate for team homework); then re-read every submission and compare
       score and latest comment, exit 1 on any mismatch. Release is a separate
       `--release` step after the instructor says so.
     - Keys: `homework.key.release` uploads the key hidden, available from the
       due date, gated to submitting teams. Uploads use `upload_files --replace`,
       which mints new file ids, so links must be repointed.
     - `clear_late_status` is used only on explicit per-team instruction.

4.7. Groups and CATME.
     - `catme.roster` (R): enrollments and sections into CATME's upload layout.
     - `catme.calendar` (W): CATME deadlines to the calendar.
     - `process.catme.for.canvas.py` prepares scores; upload path is
       `upload_gradebook` (UI import is the fallback).

4.8. Gradebook.
     - `gradebook.configure` (W): assignment groups and weights from the adopted
       scheme, grading standard, `omit_from_final_grade`. Changes posting only
       with `--auto-post`. Post policy goes through GraphQL
       `setAssignmentPostPolicy` because REST ignores it.
     - `gradebook.snapshot` (R): rebuilds the gradebook CSV from REST (the
       Canvas CSV export is browser-only). Used before and after changes to prove
       no grade moved. Takes over five minutes; run it in the background.
     - `grade.estimate.post.py` (W, dry run unless `--apply`): writes estimates
       to a 1000-point item created UNPUBLISHED. Cron posting is ON HOLD.

4.9. Calendar and deadlines.
     - `deadline.events` (W): mirrors deadlines of unpublished assignments as
       calendar events; removes them when the assignment goes live.
     - `calendar.sync` (R from Canvas): pulls the Canvas feed into a Nextcloud
       CalDAV calendar.

4.10. Announcements. No tool posts them. They are posted inline with
      `http_post_json(.../discussion_topics, {is_announcement: True, ...})` and
      mirrored by hand into `F.<year>/Announcements/announcement_<date>_<desc>.html`.

4.11. State capture. `pull` snapshots live Canvas state into tracked TSVs and
      checks the schedule against it. It exists because the course came from an
      IMSCC import and later Canvas-side edits are otherwise invisible to git.

4.12. Attendance (Roll Call). Roll Call has no API. `attendance.rollcall.fetch.py`
      uses `sessionless_launch` for the external tool, requests a report, and
      picks it up from a mailbox. Must run outside the sandbox.

---------------------------------------------------------------------------

## 5. Workflow catalogue: Cancer Evolution Sp2026

5.1. Quizzes (weekly, Classic Quizzes, S02-S26). Authored as text2qti markdown,
     built to a QTI zip, pushed with `upload_quiz.py COURSE QTI_ZIP [--publish]`
     (Content Migrations API plus polling, then repositions the quiz in session
     order). `publish_quiz.py` toggles publication by id or `--title S12`.
     Read side: `quiz_summary` and `report_to_csv` (Student/Item Analysis CSV,
     which is also the workaround for UTK's 403 on `/quizzes/:id/questions`).

5.2. Quiz integrity pulls (R): `quiz_submission_events` and per-course scripts
     read `quizzes`, `quizzes/Q/submissions`, `.../events`. Outputs are regenerable
     CSV/JSONL.

5.3. Exams (free response, paper). Source is LaTeX; scans are split and returned
     by email, outside Canvas. Canvas side is only `curve_exam.py COURSE MATCH
     --target N`, which creates a NEW "Curved" assignment column and writes
     grades (`--dry-run` available). Final grades are computed locally
     (`calculate_grades.py` and a YAML settings file); nothing found pushes
     final grades back.

5.4. CATME grade upload. CATME export, `process.catme.for.canvas.py`, then
     `upload_gradebook.py 244286 CSV --create-missing --group-id 595699`. It
     matches columns to assignments by name, POSTs missing assignments (1 point,
     no submission type), PUTs each non-empty grade, skips blanks and the "Points
     Possible" row. Run after each of four CATME rounds. Always dry run first.

5.5. Gradebook. `fetch_gradebook -o PATH` (R) for timestamped snapshots. A
     documented penalty workflow (2026-05-19): pre-snapshot, `penalties.csv` driver,
     generated upload CSV, `upload_gradebook -n -v`, real upload, post-snapshot.

5.6. Discussion boards. `discussions_to_json` and `list_discussions` (R). A
     per-course script, `flag.duplicate.questions.py`, downloads boards, runs
     Claude CLI analysis, caches in SQLite, and with `--post-flags` edits student
     posts to add banners, replies with warnings, and adds submission comments
     (library calls `post_discussion_entry_reply`, `update_discussion_entry`,
     `post_submission_comment`). `--dry-run` and `--from-session` limit scope.
     The Genetics `db.*` tools were not used here.

5.7. Homework submissions. `submissions_to_files` (R, v1.4.0): files, rubric
     scores, comments, history; `--latest-only`, `--update`, `--anonymize`.

5.8. Attendance: manual Canvas CSV export, then a local summary script; no API.

5.9. Not found for this course: lecture pages, recitations, syllabus tooling,
     calendar tools, `group.grades.post`, `db.*` grading.

---------------------------------------------------------------------------

## 6. Safety conventions

6.1. Dry run by default. Every `canvas.api.*` tool does nothing without `--apply`
     (or `--execute`); several also take `-n/--dry-run`. `db.reply` additionally
     needs a filled `decision` column.

6.2. First run of any write path is a first run. The 2026-10-13 move of the
     course-config tools did not exercise write paths. Dry run, read the output,
     write ONE student, read it back, then the rest.

6.3. Never trust a 200. A PUT can return 200 and silently ignore the change
     (discussion checkpoints via REST; assignment post policy via REST). Re-read
     the object. Every grade writer here re-reads and exits non-zero on mismatch.

6.4. Vocabulary (use these words the same way everywhere):
     - upload: the score is written into Canvas; invisible while the posting
       policy is manual.
     - reveal: students can see the score (`postAssignmentGrades`, two passes,
       `gradedOnly:true` then `false`, or an automatic policy).
     - post: ambiguous; do not use it alone.
     Revealing needs an explicit yes naming the assignments, asked on its own
     line. Quiz grades are visible the moment they are written.

6.5. Hidden columns: create the assignment, set `postManually:true` BEFORE writing
     any grade, verify `posted_at` is empty, release later by GraphQL.

6.6. Student-visible state needs an explicit word: publishing, unpublishing,
     changing a due date, posting an announcement, linking a key. "Up", "stage",
     "upload" do not mean publish (publishing emails students). No tool publishes
     pages; boards publish only through `db.checkpoints --publish`.

6.7. Never delete a published quiz, assignment, board or page that holds student
     work. Update in place. `upload_quiz.py` still offers to delete a same-prefix
     quiz: decline when it has submissions. Never hard-delete through the API.

6.8. Comments. Discussion-board comments: `group_comment=false`, read back, exit 1
     on any leak. Team homework comments: `group_comment=true`, deliberately. A
     group comment PUT adds and never replaces; deleting one deletes it for every
     member. Grade and comment are always sent together.

6.9. Audit trail. Pre- and post-gradebook snapshots around grade changes;
     discussion snapshots archived before overwrite; ledgers for replies.

6.10. Concurrency. Parallel PUTs corrupted parent/half scores on discussion
      checkpoints (82 cells, repaired 2026-11-04). Grade writes are serial with
      read-back by default.

6.11. Messages sent in the instructor's name (replies, announcements, comments)
      are shown to him verbatim before sending.

---------------------------------------------------------------------------

## 7. Data flow

7.1. Source of truth is local: schedule TSV/ODS, lectures, recitations
     (`discussions_biol240`), homework (`homework_biol240`), exams, board posts
     and grades under `F.2026/Discussion.Boards/<BLOCK>/{Posts,Reports,Notes,
     Grader.Output}`, CATME CSVs, YAML grade settings, the question databases.

7.2. Pushed to Canvas: pages, files, module structure, due dates, points,
     grades, comments, replies, group membership, calendar events, QTI quizzes,
     CATME and curved columns.

7.3. Pulled back: board posts (JSON), gradebook snapshots, TSV state dumps
     (`pull`), submissions and attachments, quiz summaries and item analysis,
     quiz events, CATME rosters, contestation digests.

7.4. Archival formats: JSON, SQLite, CSV, HTML, plus downloaded student files.
     `output/` is gitignored. `Posts/`, `Responses/`, `Raw.Download/` hold
     student education records and are restricted; `Raw.Download` is git-annex
     (a GitHub push is not its backup).

7.5. Weakest sync: edits made in the Canvas UI exist only on Canvas until
     `canvas.api.pull` is run. Imported dates are not trustworthy (a Q&A due
     date was a week off for two weeks; DST offsets differ between recitations).

---------------------------------------------------------------------------

## 8. Dependencies on Canvas behaviour, and exposure to change

This section records what our tooling assumes. Status of external changes is as
of 2026-11-05; see the research note in the session ledger.

8.1. Personal access tokens. All tools authenticate with a manually generated
     token. A University of Missouri System IT article says Instructure will
     deprecate token access in the first half of 2027, with no stated
     replacement. NOT confirmed by any Instructure page: the official change log
     and the Enforcements, Deprecations and Breaking Changes page list nothing
     of the kind. Instructure's own docs already say multi-user apps must use
     OAuth2 with a developer key (1-hour tokens, refresh token). If tokens go,
     every tool stops; the fix is an OAuth2 provider in `canvas_http.py` and
     `io_utils.py`, which needs a UTK admin to issue a developer key. TODO open
     (not due before about 2026-11-12).

8.2. Verifier URLs. File downloads and page links rely on `?verifier=` tokens:
     `canvas_http.py`, `canvas_api.py`, `report_to_csv`, `exam.pdfs.repair`,
     `update_recitation_page`, `update_lecture_page`, and the homework retrieve
     script. Instructure lists "Removal of Unauthenticated File Access" (the
     verifier parameter) as a deprecation with date TO BE DETERMINED after
     several slips (2026-03-25, then 2026-06-17). Most concrete risk found.
     Saving a page already rewrites the verifier tokens in its file links.

8.3. File ids. `on_duplicate: overwrite` mints a new id; the old id aliases to
     the new file, so links keep working but any script that records a numeric
     file id drifts.

8.4. REST limits we work around: checkpoints and post policy need GraphQL; quiz
     questions return 403 for instructors (use the Student Analysis CSV);
     announcements 404 on `/discussion_topics/:id` (use `/announcements?
     context_codes[]=` with start/end dates); anonymous boards 404 on REST.

8.5. Other announced changes checked: the synchronous provisional-grades publish
     endpoint was removed 2026-10-15 (we do not use it); a User-Agent header
     has been required since January 2026 (we send urllib's default and have
     had no failures); Instructure's API policy requires 90 days' notice before
     removing a documented endpoint or field, may revoke tokens for heavy
     non-GET use, and prohibits connecting through unapproved MCP servers.

8.6. DAP / Canvas Data 2 and DAP CLI 3.0. Not used. DAP is a read-only bulk-data
     API; CLI 3.0 (release candidate 2026-10-22, change dated 2026-11-09) requires
     table-scoped CD2 keys and regional gateways, with the old keys and the global
     gateway ending 2027-12-31. Irrelevant to us unless we adopt DAP. It cannot
     replace our writes (grades, assignments, checkpoints, uploads).

---------------------------------------------------------------------------

## 9. Known fragile points and gaps in this document

9.1. Fragile
     - Recreating a discussion topic silently breaks R06/R12 Q&A links (by id).
     - `--sync-comments` changed nothing before 2026-10-16 (wrong edit
       parameter); confirm by re-reading Canvas.
     - A puller logs only a WARN on a truncated download; the homework retrieve
       script exists for that reason.
     - A score PUT does not update an earlier comment; push both.
     - Changing one student's grade on a group assignment needs
       `grade_group_students_individually` toggled true, then false, then the
       teammates' scores re-read.
     - CATME constants (assignment group 595699) are hardcoded in this repo's
       CLAUDE.md, not in `canvas.course.json`.
     - A hardcoded `~/Repositories/Canvas.API/src` sys.path in the Cancer
       duplicate-flagging script.
     - Quizzes uploaded via the API once became inaccessible after the deadline;
       check "Hide results", "Let students see responses" and the availability
       window after every upload.

9.2. Not examined in this draft
     - Internals of `submissions_to_files`, `upload_gradebook` (header not read),
       `upload_quiz`, `list_*`, `recreate_groups`, `canvas.api.browser.py`.
     - The Genetics `canvas.exam.*` scripts under `course_biol240/bin`.
     - Memory notes referenced by the homework docs
       (`reference_canvas_grade_posting`, `project_hw02_canvas_posting`).
     - A written late-penalty policy (only the per-block JSON and a late-status
       clearing tool were found).
     - Whether any Genetics-local Canvas scripts are missing from a tool table.

9.3. Proposed next steps for this document: verify the endpoint lists against
     the code; add a short per-tool table with exact flags; add worked example
     commands for the three most common events (new HW, new DB block, exam);
     cross-link `README.TOKEN.SETUP.md`.

---------------------------------------------------------------------------

## 10. Sources

- `Canvas.API/CLAUDE.md` and `src/*.py` headers
- `Genetics/CLAUDE.md` ("Canvas tools"), `Genetics/canvas.course.json`,
  `F.2026/Discussion.Boards/README.md` and `TODO.md`,
  `Course.Conventions/canvas.gradebook.setup.md`
- `Cancer.Evolution/CLAUDE.md`, `2026/Quizzes/CLAUDE.md`,
  `2026/Student.Info.and.Issues/AI.Usage/gradebook/README.md`
- `homework_biol240`: `CLAUDE.md`, `bin/CLAUDE.md`, `grading.pipeline.*.md`
- `submissions_homework_biol240`, `discussions_biol240`: `CLAUDE.md`, `README.md`
- Instructure: Canvas API Policy; 2026 API and CLI Change Log; Enforcements,
  Deprecations, and Breaking Changes; PyPI `instructure-dap-client` history
- UM System: Canvas user API token policy (secondary source for 8.1)
