ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS memory_gb DOUBLE PRECISION;
UPDATE job_runs
   SET memory_gb = (params_snapshot->>'memory_gb')::double precision
 WHERE memory_gb IS NULL
   AND params_snapshot ? 'memory_gb'
   AND jsonb_typeof(params_snapshot->'memory_gb') IN ('number', 'string');
