CREATE SEQUENCE IF NOT EXISTS job_runs_seq;

ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS seq BIGINT;

WITH numbered AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS n
  FROM job_runs
  WHERE seq IS NULL
)
UPDATE job_runs AS j SET seq = numbered.n FROM numbered WHERE j.id = numbered.id;

SELECT setval(
  'job_runs_seq',
  GREATEST(COALESCE((SELECT MAX(seq) FROM job_runs), 1), 1),
  (SELECT MAX(seq) FROM job_runs) IS NOT NULL
);

ALTER TABLE job_runs ALTER COLUMN seq SET DEFAULT nextval('job_runs_seq');
ALTER TABLE job_runs ALTER COLUMN seq SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS job_runs_seq_uidx ON job_runs (seq);
ALTER SEQUENCE job_runs_seq OWNED BY job_runs.seq;
