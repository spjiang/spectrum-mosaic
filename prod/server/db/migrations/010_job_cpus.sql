ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS cpus INTEGER;

UPDATE job_runs
   SET cpus = (params_snapshot->>'cpus')::int
 WHERE cpus IS NULL
   AND params_snapshot ? 'cpus'
   AND jsonb_typeof(params_snapshot->'cpus') IN ('number', 'string');
