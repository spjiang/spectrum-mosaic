-- mosaic install seed data (idempotent). Schema: init.sql
-- Passwords are passlib bcrypt hashes matching app.auth.hash_password.

-- A. roles
INSERT INTO roles (name) VALUES
  ('admin'), ('configurator'), ('executor'), ('viewer')
ON CONFLICT (name) DO NOTHING;

-- B. users (skip if username exists — do not refresh trial expiry)
INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'admin', '$2b$12$4JQ6OqbwGSu0QLY1Isr9c.8tjbWMUUo5jQmdyNJ6WGaEtNpN5xis2', TRUE, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'executor', '$2b$12$L08M0D3UrOIkyCSaO4ykrOkABV3kThSpsHB3uzctM2QA1h9E91cDa', TRUE, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'executor');

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'configurator', '$2b$12$IMTpGzqz8.EzRmSpZoPPWuEpCzb7IVnF1EjMh2ReQjfRwcrSEZOs.', TRUE, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'configurator');

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'viewer', '$2b$12$u35u4AveS425Q.7k8zGOROug7rP8IyUdzK0XgATfBoP9pHzczlA3a', TRUE, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'viewer');

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'trial1', '$2b$12$Ffa2qPD7Uokdus3pvtjbu.DBtFhVpG5GUFmpcEpIluo39NIEOgsrm', TRUE,
       (((CURRENT_DATE + 30)::timestamp + interval '1 day' - interval '1 millisecond') AT TIME ZONE 'Asia/Shanghai')
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'trial1');

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'trial2', '$2b$12$Ffa2qPD7Uokdus3pvtjbu.DBtFhVpG5GUFmpcEpIluo39NIEOgsrm', TRUE,
       (((CURRENT_DATE + 30)::timestamp + interval '1 day' - interval '1 millisecond') AT TIME ZONE 'Asia/Shanghai')
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'trial2');

-- C. user_roles
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'admin' AND r.name = 'admin'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'executor' AND r.name = 'executor'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'configurator' AND r.name = 'configurator'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'viewer' AND r.name = 'viewer'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'trial1' AND r.name = 'executor'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id FROM users u CROSS JOIN roles r
WHERE u.username = 'trial2' AND r.name = 'executor'
  AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = r.id);

-- D. RBAC menus (only if missing)
INSERT INTO system_settings (key, value)
SELECT 'rbac_menus', '{
  "admin": ["execute","incremental","profiles","jobs","seam","worker","health","settings","inspect","cli","users","roles","permissions","audit"],
  "configurator": ["profiles","jobs","seam","settings","inspect","cli"],
  "executor": ["execute","incremental","profiles","jobs","seam","inspect","cli"],
  "viewer": ["profiles","jobs","seam","inspect","cli"]
}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE key = 'rbac_menus');

-- E. default profiles
INSERT INTO param_profiles (name, description, version, preset, created_by)
SELECT '标准全波段',
       '正式生产默认: 8 波段全图, 步进扫描（不包含窄带扫描）',
       1, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM param_profiles WHERE name = '标准全波段');

INSERT INTO param_profiles (name, description, version, preset, created_by)
SELECT 'RGB 快速预览',
       '仅 Color 正射, 快速查看几何与拼接',
       1, 'rgb_preview', NULL
WHERE NOT EXISTS (SELECT 1 FROM param_profiles WHERE name = 'RGB 快速预览');

-- F. helper: insert profile value if param key exists and value missing
-- 标准全波段
INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, v.key, v.val
FROM param_profiles p
CROSS JOIN (VALUES
  ('bands', '["Color","450nm","550nm","650nm","720nm","750nm","800nm","850nm"]'::jsonb),
  ('benchmark_dir', 'null'::jsonb),
  ('cache_dir', 'null'::jsonb),
  ('calibrate_intrinsics', 'true'::jsonb),
  ('color_correction', '"off"'::jsonb),
  ('drop_white_panel', 'true'::jsonb),
  ('dsm_gsd', 'null'::jsonb),
  ('input_dir', 'null'::jsonb),
  ('log_dir', 'null'::jsonb),
  ('match_reference_color', 'false'::jsonb),
  ('max_fill_gap_m', '40.0'::jsonb),
  ('max_frames', 'null'::jsonb),
  ('max_index', 'null'::jsonb),
  ('max_tilt_deg', '60.0'::jsonb),
  ('min_agl_m', '5.0'::jsonb),
  ('n_layers', '48'::jsonb),
  ('outlier_threshold_px', '6.0'::jsonb),
  ('output_dir', 'null'::jsonb),
  ('preset', 'null'::jsonb),
  ('primary_band', '"Color"'::jsonb),
  ('process_dir', 'null'::jsonb),
  ('products_dir_name', '"拼图结果"'::jsonb),
  ('require_pos', 'true'::jsonb),
  ('reuse_dsm', 'null'::jsonb),
  ('run_mode', '"full"'::jsonb),
  ('seamline_enabled', 'true'::jsonb),
  ('sigma_attitude_deg', '3.0'::jsonb),
  ('sigma_xy_m', '3.0'::jsonb),
  ('sigma_z_m', '5.0'::jsonb),
  ('spike_tolerance_m', '2.5'::jsonb),
  ('start_stage', '"S0_io"'::jsonb),
  ('stop_after_stage', 'null'::jsonb),
  ('workers_at', '12'::jsonb),
  ('workers_dense', 'null'::jsonb),
  ('workers_ortho', 'null'::jsonb),
  ('write_json_report', 'true'::jsonb),
  ('write_pdf_report', 'true'::jsonb),
  ('z_margin_m', 'null'::jsonb)
) AS v(key, val)
WHERE p.name = '标准全波段'
  AND EXISTS (SELECT 1 FROM param_definitions d WHERE d.key = v.key)
  AND NOT EXISTS (
    SELECT 1 FROM param_profile_values x
    WHERE x.profile_id = p.id AND x.param_key = v.key
  );

-- RGB 快速预览
INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, v.key, v.val
FROM param_profiles p
CROSS JOIN (VALUES
  ('bands', '["Color"]'::jsonb),
  ('at_bands', '["Color"]'::jsonb),
  ('benchmark_dir', 'null'::jsonb),
  ('cache_dir', 'null'::jsonb),
  ('calibrate_intrinsics', 'true'::jsonb),
  ('color_correction', '"off"'::jsonb),
  ('drop_white_panel', 'true'::jsonb),
  ('dsm_gsd', 'null'::jsonb),
  ('input_dir', 'null'::jsonb),
  ('log_dir', 'null'::jsonb),
  ('match_reference_color', 'false'::jsonb),
  ('max_fill_gap_m', '40.0'::jsonb),
  ('max_frames', 'null'::jsonb),
  ('max_index', 'null'::jsonb),
  ('max_tilt_deg', '60.0'::jsonb),
  ('min_agl_m', '5.0'::jsonb),
  ('n_layers', '48'::jsonb),
  ('outlier_threshold_px', '6.0'::jsonb),
  ('output_dir', 'null'::jsonb),
  ('preset', '"rgb_preview"'::jsonb),
  ('primary_band', '"Color"'::jsonb),
  ('process_dir', 'null'::jsonb),
  ('products_dir_name', '"拼图结果"'::jsonb),
  ('require_pos', 'true'::jsonb),
  ('reuse_dsm', 'null'::jsonb),
  ('run_mode', '"full"'::jsonb),
  ('seamline_enabled', 'true'::jsonb),
  ('sigma_attitude_deg', '3.0'::jsonb),
  ('sigma_xy_m', '3.0'::jsonb),
  ('sigma_z_m', '5.0'::jsonb),
  ('spike_tolerance_m', '2.5'::jsonb),
  ('start_stage', '"S0_io"'::jsonb),
  ('stop_after_stage', 'null'::jsonb),
  ('workers_at', '12'::jsonb),
  ('workers_dense', 'null'::jsonb),
  ('workers_ortho', 'null'::jsonb),
  ('write_json_report', 'true'::jsonb),
  ('write_pdf_report', 'true'::jsonb),
  ('z_margin_m', 'null'::jsonb)
) AS v(key, val)
WHERE p.name = 'RGB 快速预览'
  AND EXISTS (SELECT 1 FROM param_definitions d WHERE d.key = v.key)
  AND NOT EXISTS (
    SELECT 1 FROM param_profile_values x
    WHERE x.profile_id = p.id AND x.param_key = v.key
  );
