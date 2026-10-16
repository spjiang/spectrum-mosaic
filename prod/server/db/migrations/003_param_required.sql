ALTER TABLE param_definitions
  ADD COLUMN IF NOT EXISTS required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE param_definitions
  ADD COLUMN IF NOT EXISTS required_when JSONB;

UPDATE param_definitions
   SET required = (key IN ('input_dir', 'output_dir'));

UPDATE param_definitions
   SET required_when = CASE
     WHEN key = 'stop_after_stage' THEN '{"run_mode": "until_stage"}'::jsonb
     ELSE NULL
   END;
