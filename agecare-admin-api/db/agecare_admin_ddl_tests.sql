\set ON_ERROR_STOP on
\set QUIET on
-- Usuarios de prueba que heredan los roles de BD
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='api_test') THEN CREATE ROLE api_test LOGIN IN ROLE agecare_admin_api; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ro_test')  THEN CREATE ROLE ro_test  LOGIN IN ROLE agecare_admin_ro;  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='job_test') THEN CREATE ROLE job_test LOGIN BYPASSRLS IN ROLE agecare_admin_jobs; END IF;
END $$;

-- Dos tenants
INSERT INTO admin.tenants (id, code, name, country_code, currency_code, timezone, region)
VALUES ('11111111-1111-1111-1111-111111111111','cl-prod','AgeCare Chile','CL','CLP','America/Santiago','Azure Chile Central'),
       ('22222222-2222-2222-2222-222222222222','mx-prod','AgeCare México','MX','MXN','America/Mexico_City','Azure Mexico Central')
ON CONFLICT (code) DO NOTHING;

-- Zona horaria inválida debe fallar
DO $$ BEGIN
  INSERT INTO admin.tenants (code,name,country_code,currency_code,timezone) VALUES ('bad','x','CL','CLP','Mars/Olympus');
  RAISE EXCEPTION 'FALLO: zona horaria inválida aceptada';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK timezone inválida rechazada'; END $$;

-- ===== Como API en el tenant 1 =====
SET ROLE api_test;
SET app.tenant_id = '11111111-1111-1111-1111-111111111111';

INSERT INTO admin.admin_users (id, tenant_id, full_name, email, role_code, password_hash, activated_at, mfa_required)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','Max K.','max@wellq.co.uk','admin','$argon2id$fake','2026-09-01', true),
       ('aaaaaaaa-0000-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','Sofía Soporte','soporte@wellq.co.uk','support','$argon2id$fake','2026-09-01', false);
SET app.actor_id = 'aaaaaaaa-0000-0000-0000-000000000001';

-- Email único por tenant (citext: distinto case = duplicado)
DO $$ BEGIN
  INSERT INTO admin.admin_users (tenant_id, full_name, email, role_code) VALUES ('11111111-1111-1111-1111-111111111111','Dup','MAX@wellq.co.uk','analyst');
  RAISE EXCEPTION 'FALLO: email duplicado aceptado';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK email duplicado (case-insensitive) rechazado'; END $$;

-- RLS: no puedo insertar en otro tenant
DO $$ BEGIN
  INSERT INTO admin.admin_users (tenant_id, full_name, email, role_code) VALUES ('22222222-2222-2222-2222-222222222222','Intruso','x@wellq.co.uk','admin');
  RAISE EXCEPTION 'FALLO: RLS permitió insertar en otro tenant';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK RLS bloquea INSERT en otro tenant'; END $$;

-- Último admin no se puede desactivar
DO $$ BEGIN
  UPDATE admin.admin_users SET is_active = false WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: último admin desactivado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK LAST_ADMIN'; END $$;

-- Tickets: correlativo por tenant, transiciones, first_response_at
INSERT INTO admin.support_tickets (tenant_id, subject, description, requester_name, requester_email, requester_role_code, category_code, channel, created_by)
VALUES ('11111111-1111-1111-1111-111111111111','Wearable no sincroniza desde ayer','Detalle…','Ana Pérez','ana@example.com','family','wearable_sync','console','aaaaaaaa-0000-0000-0000-000000000001'),
       ('11111111-1111-1111-1111-111111111111','No llegan las alertas de medicamentos','Detalle…','Rosa Díaz','rosa@example.com','caregiver','alerts_push','app',NULL);
SELECT number, subject FROM admin.support_tickets ORDER BY number;

DO $$ DECLARE t uuid; BEGIN
  SELECT id INTO t FROM admin.support_tickets WHERE number = 1;
  BEGIN
    UPDATE admin.support_tickets SET status = 'closed' WHERE id = t;
    RAISE EXCEPTION 'FALLO: transición open→closed aceptada';
  EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK INVALID_TRANSITION open→closed'; END;
  UPDATE admin.support_tickets SET status = 'in_progress', assigned_to = 'aaaaaaaa-0000-0000-0000-000000000002' WHERE id = t;
  INSERT INTO admin.support_ticket_replies (tenant_id, ticket_id, author_type, author_admin_id, author_name, body, is_internal)
    VALUES ('11111111-1111-1111-1111-111111111111', t, 'admin', 'aaaaaaaa-0000-0000-0000-000000000002', 'Sofía Soporte', 'Nota interna', true);
  IF (SELECT first_response_at FROM admin.support_tickets WHERE id = t) IS NOT NULL THEN RAISE EXCEPTION 'FALLO: nota interna fijó first_response_at'; END IF;
  INSERT INTO admin.support_ticket_replies (tenant_id, ticket_id, author_type, author_admin_id, author_name, body)
    VALUES ('11111111-1111-1111-1111-111111111111', t, 'admin', 'aaaaaaaa-0000-0000-0000-000000000002', 'Sofía Soporte', 'Hola Ana, revisa el Bluetooth…');
  IF (SELECT first_response_at FROM admin.support_tickets WHERE id = t) IS NULL THEN RAISE EXCEPTION 'FALLO: first_response_at no fijado'; END IF;
  RAISE NOTICE 'OK first_response_at fijado por la primera respuesta pública';
  UPDATE admin.support_tickets SET status = 'resolved' WHERE id = t;
  UPDATE admin.support_tickets SET status = 'closed' WHERE id = t;
  BEGIN
    INSERT INTO admin.support_ticket_replies (tenant_id, ticket_id, author_type, author_name, body) VALUES ('11111111-1111-1111-1111-111111111111', t, 'system', 'sistema', 'x');
    RAISE EXCEPTION 'FALLO: respuesta en ticket cerrado aceptada';
  EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK TICKET_CLOSED'; END;
END $$;

-- Historial: versiones del ticket 1
SELECT op, status, assigned_to IS NOT NULL AS assigned, changed_by = 'aaaaaaaa-0000-0000-0000-000000000001' AS by_actor
  FROM admin.support_tickets_history WHERE number = 1 ORDER BY history_id;

-- Historial de admin_users no contiene password_hash
SELECT count(*) AS versiones, bool_and(password_hash IS NULL) AS sin_hash FROM admin.admin_users_history;

-- Incidente: resolver exige resolución (CHECK) y transición válida
INSERT INTO admin.ops_incidents (id, tenant_id, title, component_key, severity, status, description, started_at, created_by)
VALUES ('bbbbbbbb-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','Latencia elevada en el Asistente IA','ai_assistant','degraded','investigating','p95 sobre 4 s','2026-08-26 10:00+00','aaaaaaaa-0000-0000-0000-000000000001');
DO $$ BEGIN
  UPDATE admin.ops_incidents SET status = 'resolved' WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: resolved sin resolución aceptado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK RESOLUTION_REQUIRED'; END $$;
UPDATE admin.ops_incidents SET status = 'observing' WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
UPDATE admin.ops_incidents SET status = 'resolved', resolution = 'Caché de respuestas frecuentes' WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
SELECT status, resolved_at IS NOT NULL AS resolved_at_set FROM admin.ops_incidents;

-- Settings: bloqueo optimista
INSERT INTO admin.system_settings (tenant_id, key, value) SELECT '11111111-1111-1111-1111-111111111111', key, default_value FROM admin.setting_definitions;
DO $$ BEGIN
  UPDATE admin.system_settings SET value = '{"gold":1200,"platinum":10000,"provider":5000}', version = 5 WHERE key = 'plan_prices';
  RAISE EXCEPTION 'FALLO: versión incorrecta aceptada';
EXCEPTION WHEN serialization_failure THEN RAISE NOTICE 'OK VERSION_CONFLICT'; END $$;
UPDATE admin.system_settings SET value = '{"gold":1200,"platinum":10000,"provider":5000}', version = 2, change_note = 'Ajuste de precio Dorado' WHERE key = 'plan_prices';
SELECT key, version, updated_at IS NOT NULL AS stamped FROM admin.system_settings WHERE key = 'plan_prices';
SELECT op, version, value->>'gold' AS gold FROM admin.system_settings_history WHERE key = 'plan_prices' ORDER BY history_id;

-- Auditoría inmutable
INSERT INTO admin.audit_log (tenant_id, actor_id, action, entity_type, entity_id, after, ip)
VALUES ('11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001','settings.update','system_setting',NULL,'{"key":"plan_prices"}','10.0.0.1');
DO $$ BEGIN
  UPDATE admin.audit_log SET action = 'x.y';
  RAISE EXCEPTION 'FALLO: UPDATE en audit_log aceptado';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK audit_log inmutable (UPDATE)'; END $$;
DO $$ BEGIN
  DELETE FROM admin.audit_log;
  RAISE EXCEPTION 'FALLO: DELETE en audit_log aceptado';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK audit_log inmutable (DELETE)'; END $$;

-- Moderación: rechazo con motivo, decisión única
INSERT INTO admin.moderation_items (id, tenant_id, item_type, source_entity_id, content_snapshot, author_user_id, author_name, author_role_code)
VALUES ('cccccccc-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','review',gen_random_uuid(),'{"rating":1,"text":"…"}',gen_random_uuid(),'Autor','family');
DO $$ BEGIN
  UPDATE admin.moderation_items SET status='rejected', decided_by='aaaaaaaa-0000-0000-0000-000000000001', decided_at=now() WHERE id='cccccccc-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: rechazo sin motivo aceptado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK rechazo exige reason_code'; END $$;
UPDATE admin.moderation_items SET status='rejected', reject_reason_code='offensive', decided_by='aaaaaaaa-0000-0000-0000-000000000001', decided_at=now() WHERE id='cccccccc-0000-0000-0000-000000000001';
DO $$ BEGIN
  UPDATE admin.moderation_items SET status='approved' WHERE id='cccccccc-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: segunda decisión aceptada';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK ALREADY_MODERATED'; END $$;

-- Legales: semver generado y bloqueo tras publicar
INSERT INTO admin.legal_versions (id, tenant_id, doc_type, semver_major, semver_minor, content_md, changelog, effective_date, requires_reacceptance, created_by)
VALUES ('dddddddd-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','terms',2,0,repeat('Lorem ipsum ',20),'Nueva versión mayor','2026-10-15',true,'aaaaaaaa-0000-0000-0000-000000000001');
UPDATE admin.legal_versions SET status='published', published_by='aaaaaaaa-0000-0000-0000-000000000001', published_at=now() WHERE id='dddddddd-0000-0000-0000-000000000001';
DO $$ BEGIN
  UPDATE admin.legal_versions SET content_md = repeat('Cambio ',30) WHERE id='dddddddd-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: versión publicada editada';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK versión legal publicada inmutable'; END $$;
SELECT semver, status FROM admin.legal_versions;

-- Adopción: columna generada y FK compuesta a feature_roles
RESET ROLE; SET ROLE job_test;
INSERT INTO admin.feature_usage_window (tenant_id, days_window, feature_key, app_role_code, users, role_active_users)
VALUES ('11111111-1111-1111-1111-111111111111',30,'home_traffic_light','family',4885,5310);
SELECT feature_key, app_role_code, adoption FROM admin.feature_usage_window;
DO $$ BEGIN
  INSERT INTO admin.feature_usage_window (tenant_id, days_window, feature_key, app_role_code, users, role_active_users)
  VALUES ('11111111-1111-1111-1111-111111111111',30,'home_traffic_light','doctor',1,1);
  RAISE EXCEPTION 'FALLO: par función/rol no aplicable aceptado';
EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'OK adopción solo para pares de feature_roles'; END $$;

-- Particiones: inserción cae en la partición del día y jobs ven todos los tenants
INSERT INTO admin.ops_component_checks (tenant_id, component_key, checked_at, ok, latency_ms)
VALUES ('11111111-1111-1111-1111-111111111111','api_core',now(),true,120),
       ('22222222-2222-2222-2222-222222222222','api_core',now(),true,140);
SELECT tableoid::regclass AS particion, count(*) FROM admin.ops_component_checks GROUP BY 1;
SELECT count(*) AS tenants_visibles_por_jobs FROM admin.tenants;

-- Retención: una partición antigua se elimina
RESET ROLE;
CREATE TABLE admin.ops_component_checks_p20250101 PARTITION OF admin.ops_component_checks FOR VALUES FROM ('2025-01-01') TO ('2025-01-02');
CALL admin.drop_expired_partitions('ops_component_checks', interval '90 days');
SELECT count(*) AS particiones_2025 FROM pg_tables WHERE schemaname='admin' AND tablename='ops_component_checks_p20250101';

-- ===== Como API en el tenant 2: aislamiento =====
SET ROLE api_test;
SET app.tenant_id = '22222222-2222-2222-2222-222222222222';
SELECT count(*) AS tickets_visibles_tenant2 FROM admin.support_tickets;
SELECT count(*) AS checks_visibles_tenant2 FROM admin.ops_component_checks;
INSERT INTO admin.support_tickets (tenant_id, subject, description, requester_name, requester_email, category_code)
VALUES ('22222222-2222-2222-2222-222222222222','Primer ticket MX','…','Luis','luis@example.com','other');
SELECT number AS numero_primer_ticket_mx FROM admin.support_tickets;

-- ===== Solo lectura: sin credenciales =====
RESET ROLE; SET ROLE ro_test;
SET app.tenant_id = '11111111-1111-1111-1111-111111111111';
SELECT full_name, role_code FROM admin.admin_users ORDER BY full_name;
DO $$ BEGIN
  PERFORM password_hash FROM admin.admin_users;
  RAISE EXCEPTION 'FALLO: RO lee password_hash';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK RO sin acceso a password_hash'; END $$;
DO $$ BEGIN
  UPDATE admin.support_tickets SET priority = 'high';
  RAISE EXCEPTION 'FALLO: RO escribe';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK RO no escribe'; END $$;
RESET ROLE;
\echo TODAS LAS PRUEBAS PASARON
