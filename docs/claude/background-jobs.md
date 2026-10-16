# Long Admin Work Off the Request Path

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### Long Admin Work Runs Off the Request Path (#277, #384)
Anything that would outlive the proxy timeout is a **job row + FastAPI
`BackgroundTask`**, not a long request. The endpoint creates a `CrawlJob`,
`background_tasks.add_task(runner, job.id)`, and returns **202 + the job id**;
the client polls `GET /media-library/jobs/{id}`. Three properties to preserve
when adding a job type:

- **The `crawl_jobs` table is the generic job table despite its name.** Media
  crawls (`WIKIMEDIA`, `LOC`), LLM candidate scoring (`SCORE`), fact-check
  runs (`FACT_CHECK`, #384), the four long edu pipeline steps
  (`EDU_GENERATE_GUIDES`, `EDU_RENDER_SLIDES`, `EDU_GENERATE_AUDIO`,
  `EDU_ASSEMBLE_VIDEO`, #346, migration `0034`) *and* article narration
  (`ARTICLE_NARRATION`, #547, migration `0040`) all live in it (`CrawlJobType` in
  `app/models/enums.py`); state is in the row, not process memory, so any uvicorn
  worker can serve the poll. Don't add a parallel jobs table. A new job type is a
  drop-and-recreate of `ck_crawl_jobs_job_type` (`0021`, `0034`, `0040`) — and
  `job_type` is `String(20)`, so the name must fit (`EDU_GENERATE_GUIDES` is 19) — and the
  migration must be applied **before** the code that inserts it ships, or right
  after its deploy per "## Shipping" — deploys don't run migrations, and the
  CHECK turns every kickoff into a 500 until it is applied.
- **The runner opens its own session** (`SessionLocal()` — the request's session
  is closed once the response is sent), commits progress counters through a
  `progress(done, total, current)` callback, and **never raises**: failures land
  on the job row and go to Sentry (`crawl_jobs.run_crawl_job`,
  `fact_check.run_fact_check_job`).
- **In-flight work is refused at the granularity that matters — except where a
  replaying driver would trip over it.** Crawls refuse a second job of the *same
  type* (`active_job`) with a **409**. Fact-check refuses a second job for the
  *same subject* in the **service** (`create_fact_check_job` raises
  `FactCheckJobInFlightError`, because a re-run replaces that subject's claim
  rows and overlapping runs would corrupt each other) — but the two routers make
  **opposite** choices with that exception: `_kickoff_fact_check`
  (`app/api/dashboard.py`, serving suggestion / research / article) **joins** the
  running job, returning the usual 202 with the *existing* job id, while the edu
  kickoffs (`app/api/edu.py`, `EDU_RESEARCH` / `EDU_MATERIAL`) still 409. The
  join exists because the article write endpoint auto-kicks a grounding check and
  the driver's own `ARTICLE_FACT_CHECK` task raced it into back-to-back 409s
  within one second; the call plan simply polls whichever job id comes back. A
  job stuck `RUNNING` past `STALE_AFTER` (1h) is treated as dead so a recycled
  worker can't block kick-off forever. The edu steps (`app/services/edu_jobs.py`)
  take a **third** shape: one edu job per subject (the deck's `material_id`, or
  the `research_id` for guide generation), where an **identical** request (same
  type, same params) **joins** the running job, so re-clicking after a reload
  resumes polling, while any *other* edu job on that subject is a **409** — a
  re-render racing the video mux over one deck's slide rows would ship an
  inconsistent MP4. On success `job.stats` is exactly the body the synchronous
  endpoint used to return, so `runEduJob` in `services/admin.js` hands callers
  the old shape.
- **`run_edu_job` opens its session with `expire_on_commit=False` and commits in
  every `progress` callback.** That is how it honours the Connection Pool rule
  without refactoring the services: after each commit the connection goes back
  to the pool while the ORM objects stay loaded, so a slide's chromium /
  ElevenLabs / ffmpeg work runs without a pinned slot, and each rendered frame or
  synthesized MP3 is durable the moment it lands (a mid-deck failure keeps the
  finished slides). Guide generation is only partly there: `_generate_band`
  still holds a connection from its first query through that band's LLM calls.
