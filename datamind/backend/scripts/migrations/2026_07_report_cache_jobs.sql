-- report_cache background-jobs foundations (PLAN 04).
-- Idempotent (IF NOT EXISTS) — safe to re-run.
--
-- Queue tech: this repo has no Redis and arq is not installed, but APScheduler
-- IS already a dependency — so PLAN 04's documented fallback is used: a DB-backed
-- job table (below) drained by a worker, plus APScheduler for the cron-style
-- lifecycle jobs. No new dependency, no new infra. See report_cache/jobs/.

-- Durable job queue. The worker (report_cache/jobs/worker.py) claims 'queued'
-- rows whose run_after has passed, runs them, and marks them done/error.
-- `payload` carries task kwargs, incl. an optional short-lived POS `token`
-- (the embed request's v2.0 aat) so onboarding/ingest enqueued from a live
-- request can authenticate — the stored api_token is v1.0 and does NOT
-- authenticate against the v2.0 report API (see report_cache/jobs/tasks.py).
CREATE TABLE IF NOT EXISTS report_cache_job (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  task        VARCHAR(64)  NOT NULL,
  tenant_id   VARCHAR(64),
  payload     JSON         NOT NULL,
  status      ENUM('queued','running','done','error') NOT NULL DEFAULT 'queued',
  attempts    INT          NOT NULL DEFAULT 0,
  run_after   DATETIME     NOT NULL,
  last_error  VARCHAR(512),
  created_at  DATETIME     NOT NULL,
  updated_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_claim (status, run_after)
) ENGINE=InnoDB;

-- Per-tenant report_cache lifecycle markers (PLAN 04 Step 3/4).
-- Kept separate from tenant_profile because tenant_profile.profile_json is the
-- raw POS payload and is fully overwritten on every profile re-sync — an
-- onboarding flag stored there would be wiped. This table survives re-syncs.
CREATE TABLE IF NOT EXISTS report_cache_state (
  tenant_id        VARCHAR(64) NOT NULL,
  onboarded_at     DATETIME,          -- set once eager-recent ingest completes (NULL means not yet onboarded)
  last_backfill_at DATETIME,
  updated_at       DATETIME    NOT NULL,
  PRIMARY KEY (tenant_id)
) ENGINE=InnoDB;
