\set ON_ERROR_STOP on
\set QUIET on
-- Pruebas del DDL de facturación. Requiere agecare_admin_ddl.sql + agecare_billing_ddl.sql aplicados.
-- Es autosuficiente: crea (si faltan) los usuarios de prueba, los tenants y un admin.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='api_test') THEN CREATE ROLE api_test LOGIN IN ROLE agecare_admin_api; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ro_test')  THEN CREATE ROLE ro_test  LOGIN IN ROLE agecare_admin_ro;  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='job_test') THEN CREATE ROLE job_test LOGIN BYPASSRLS IN ROLE agecare_admin_jobs; END IF;
END $$;

INSERT INTO admin.tenants (id, code, name, country_code, currency_code, timezone, region)
VALUES ('11111111-1111-1111-1111-111111111111','cl-prod','AgeCare Chile','CL','CLP','America/Santiago','Azure Chile Central'),
       ('22222222-2222-2222-2222-222222222222','mx-prod','AgeCare México','MX','MXN','America/Mexico_City','Azure Mexico Central')
ON CONFLICT (code) DO NOTHING;
INSERT INTO admin.admin_users (id, tenant_id, full_name, email, role_code, password_hash, activated_at)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','Max K.','max@wellq.co.uk','admin','$argon2id$fake','2026-09-01')
ON CONFLICT DO NOTHING;

-- Limpieza para poder re-ejecutar (como propietario, sin RLS ni grants)
ALTER TABLE admin.billing_refunds DISABLE TRIGGER trg_immutable;
ALTER TABLE admin.billing_events  DISABLE TRIGGER trg_immutable;
DELETE FROM admin.billing_refunds; DELETE FROM admin.billing_events;
ALTER TABLE admin.billing_refunds ENABLE TRIGGER trg_immutable;
ALTER TABLE admin.billing_events  ENABLE TRIGGER trg_immutable;
DELETE FROM admin.billing_charges; DELETE FROM admin.billing_subscriptions; DELETE FROM admin.billing_payment_methods;
-- El historial es de solo inserción (trigger por fila); TRUNCATE no lo dispara
TRUNCATE admin.billing_charges_history, admin.billing_subscriptions_history, admin.billing_payment_methods_history;

-- Catálogo: permisos del módulo billing
SELECT role_code, module, access FROM admin.admin_role_permissions WHERE module LIKE 'billing%' ORDER BY 1, 2;

-- ===== Como API en el tenant 1 =====
SET ROLE api_test;
SET app.tenant_id = '11111111-1111-1111-1111-111111111111';
SET app.actor_id  = 'aaaaaaaa-0000-0000-0000-000000000001';

-- Medio de pago: no se activa sin tbk_user cifrado
INSERT INTO admin.billing_payment_methods (id, tenant_id, user_id, user_email, tbk_username, inscription_token_hash, inscription_expires_at)
VALUES ('eeeeeeee-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001',
        'ana@example.com','u-99999999000000000000000000000001', repeat('a',64), now() + interval '5 min');
DO $$ BEGIN
  UPDATE admin.billing_payment_methods SET status='active', activated_at=now() WHERE id='eeeeeeee-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: tarjeta activa sin tbk_user';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK tarjeta activa exige tbk_user y últimos 4'; END $$;
UPDATE admin.billing_payment_methods SET status='active', activated_at=now(), tbk_user_enc='\x00ff'::bytea,
       card_type='Visa', card_last4='6623', authorization_code='1213'
 WHERE id='eeeeeeee-0000-0000-0000-000000000001';

-- Una sola tarjeta activa por usuario
DO $$ BEGIN
  INSERT INTO admin.billing_payment_methods (tenant_id, user_id, user_email, tbk_username, status, tbk_user_enc, card_last4, activated_at)
  VALUES ('11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001','ana@example.com','u-x','active','\x01'::bytea,'0568',now());
  RAISE EXCEPTION 'FALLO: segunda tarjeta activa aceptada';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK una tarjeta activa por usuario'; END $$;

-- Plan y tipo de beneficiario deben coincidir con plans.billing_unit
DO $$ BEGIN
  INSERT INTO admin.billing_subscriptions (tenant_id, user_id, user_email, plan_code, beneficiary_type, beneficiary_id, price_amount)
  VALUES ('11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001','ana@example.com','gold','family',gen_random_uuid(),1000);
  RAISE EXCEPTION 'FALLO: Dorado por familia aceptado';
EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'OK plan/unidad facturable coherentes'; END $$;
DO $$ BEGIN
  INSERT INTO admin.billing_subscriptions (tenant_id, user_id, user_email, plan_code, beneficiary_type, beneficiary_id, price_amount)
  VALUES ('11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001','ana@example.com','free','none',gen_random_uuid(),1);
  RAISE EXCEPTION 'FALLO: suscripción al plan gratuito aceptada';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK el plan gratuito no se suscribe'; END $$;

INSERT INTO admin.billing_subscriptions (id, tenant_id, user_id, user_email, user_name, plan_code, beneficiary_type, beneficiary_id, price_amount)
VALUES ('ffffffff-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001',
        'ana@example.com','Ana Pérez','gold','user','99999999-0000-0000-0000-000000000001',1000);

-- Una suscripción viva por beneficiario
DO $$ BEGIN
  INSERT INTO admin.billing_subscriptions (tenant_id, user_id, user_email, plan_code, beneficiary_type, beneficiary_id, price_amount)
  VALUES ('11111111-1111-1111-1111-111111111111','99999999-0000-0000-0000-000000000001','ana@example.com','gold','user','99999999-0000-0000-0000-000000000001',1000);
  RAISE EXCEPTION 'FALLO: segunda suscripción viva aceptada';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK una suscripción viva por beneficiario'; END $$;

-- Máquina de estados
DO $$ BEGIN
  UPDATE admin.billing_subscriptions SET status='past_due' WHERE id='ffffffff-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: incomplete→past_due aceptado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK INVALID_TRANSITION incomplete→past_due'; END $$;
DO $$ BEGIN
  UPDATE admin.billing_subscriptions SET status='active' WHERE id='ffffffff-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: activa sin periodo ni medio de pago';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK activa exige periodo y medio de pago'; END $$;

-- Cobro inicial: pending antes de llamar a Transbank
INSERT INTO admin.billing_charges (id, tenant_id, subscription_id, payment_method_id, user_id, plan_code, kind, buy_order, detail_buy_order,
                                   commerce_code, amount, period_start, period_end)
VALUES ('cccccccc-1111-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','ffffffff-0000-0000-0000-000000000001',
        'eeeeeeee-0000-0000-0000-000000000001','99999999-0000-0000-0000-000000000001','gold','initial',
        'AG261004A1B2C3D4E5','AG261004A1B2C3D4E5-S','597055555542',1000,'2026-10-04','2026-11-04');
DO $$ BEGIN
  UPDATE admin.billing_charges SET status='authorized', response_code=0, resolved_at=now() WHERE id='cccccccc-1111-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: cobro autorizado sin código de autorización';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK autorizado exige authorization_code'; END $$;
UPDATE admin.billing_charges SET status='authorized', response_code=0, authorization_code='1415', card_last4='6623',
       resolved_at=now(), transaction_date=now() WHERE id='cccccccc-1111-0000-0000-000000000001';
DO $$ BEGIN
  UPDATE admin.billing_charges SET amount=1 WHERE id='cccccccc-1111-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: monto de cobro modificado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK CHARGE_LOCKED'; END $$;
DO $$ BEGIN
  UPDATE admin.billing_charges SET status='pending' WHERE id='cccccccc-1111-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: cobro autorizado volvió a pending';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK cobro resuelto no vuelve a pending'; END $$;

UPDATE admin.billing_subscriptions
   SET status='active', payment_method_id='eeeeeeee-0000-0000-0000-000000000001', billing_anchor_day=4,
       current_period_start='2026-10-04', current_period_end='2026-11-04', next_charge_at='2026-11-04', started_at=now()
 WHERE id='ffffffff-0000-0000-0000-000000000001';
DO $$ BEGIN
  UPDATE admin.billing_subscriptions SET price_amount=1 WHERE id='ffffffff-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: precio cambiado sin cambio de plan';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK PRICE_LOCKED'; END $$;

-- Reembolsos: parcial, exceso y total
INSERT INTO admin.billing_refunds (tenant_id, charge_id, amount, status, refund_type, response_code, reason, requested_by)
VALUES ('11111111-1111-1111-1111-111111111111','cccccccc-1111-0000-0000-000000000001',400,'succeeded','NULLIFIED',0,
        'Compensación por caída del servicio','aaaaaaaa-0000-0000-0000-000000000001');
DO $$ BEGIN
  IF (SELECT (status, refunded_amount) FROM admin.billing_charges WHERE id='cccccccc-1111-0000-0000-000000000001')
     IS DISTINCT FROM ROW('partially_refunded'::varchar, 400) THEN RAISE EXCEPTION 'FALLO: reembolso parcial no aplicado'; END IF;
  RAISE NOTICE 'OK reembolso parcial recalcula el cobro';
END $$;
DO $$ BEGIN
  INSERT INTO admin.billing_refunds (tenant_id, charge_id, amount, status, refund_type, reason, requested_by)
  VALUES ('11111111-1111-1111-1111-111111111111','cccccccc-1111-0000-0000-000000000001',700,'succeeded','NULLIFIED',
          'Intento de reembolso excesivo','aaaaaaaa-0000-0000-0000-000000000001');
  RAISE EXCEPTION 'FALLO: reembolso mayor al cobro aceptado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK REFUND_EXCEEDS_CHARGE'; END $$;
INSERT INTO admin.billing_refunds (tenant_id, charge_id, amount, status, refund_type, response_code, reason, requested_by)
VALUES ('11111111-1111-1111-1111-111111111111','cccccccc-1111-0000-0000-000000000001',600,'succeeded','NULLIFIED',0,
        'Saldo restante a pedido del cliente','aaaaaaaa-0000-0000-0000-000000000001');
DO $$ BEGIN
  IF (SELECT (status, refunded_amount) FROM admin.billing_charges WHERE id='cccccccc-1111-0000-0000-000000000001')
     IS DISTINCT FROM ROW('refunded'::varchar, 1000) THEN RAISE EXCEPTION 'FALLO: reembolso total no aplicado'; END IF;
  RAISE NOTICE 'OK reembolso total deja el cobro refunded';
END $$;
DO $$ BEGIN
  UPDATE admin.billing_refunds SET amount = 1;
  RAISE EXCEPTION 'FALLO: reembolso modificado';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK reembolsos inmutables'; END $$;

-- Un cobro rechazado no se reembolsa ni cambia de estado
INSERT INTO admin.billing_charges (id, tenant_id, subscription_id, payment_method_id, user_id, plan_code, kind, attempt, buy_order,
                                   detail_buy_order, commerce_code, amount, period_start, period_end, status, response_code,
                                   failure_code, resolved_at)
VALUES ('cccccccc-1111-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','ffffffff-0000-0000-0000-000000000001',
        'eeeeeeee-0000-0000-0000-000000000001','99999999-0000-0000-0000-000000000001','gold','renewal',1,
        'AG261104FFEEDDCCBB','AG261104FFEEDDCCBB-S','597055555542',1000,'2026-11-04','2026-12-04','failed',-97,'TBK_DAILY_LIMIT',now());
DO $$ BEGIN
  INSERT INTO admin.billing_refunds (tenant_id, charge_id, amount, status, refund_type, reason, requested_by)
  VALUES ('11111111-1111-1111-1111-111111111111','cccccccc-1111-0000-0000-000000000002',100,'succeeded','REVERSED',
          'No debería poder','aaaaaaaa-0000-0000-0000-000000000001');
  RAISE EXCEPTION 'FALLO: reembolso de un cobro rechazado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK CHARGE_NOT_REFUNDABLE'; END $$;
DO $$ BEGIN
  UPDATE admin.billing_charges SET status='authorized', response_code=0, authorization_code='1' WHERE id='cccccccc-1111-0000-0000-000000000002';
  RAISE EXCEPTION 'FALLO: cobro rechazado pasó a autorizado';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK cobro rechazado es definitivo'; END $$;

-- Mora y cancelación
UPDATE admin.billing_subscriptions SET status='past_due', retry_count=1, next_charge_at=now() + interval '1 day'
 WHERE id='ffffffff-0000-0000-0000-000000000001';
DO $$ BEGIN
  UPDATE admin.billing_subscriptions SET status='canceled' WHERE id='ffffffff-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: cancelación sin motivo';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK cancelación exige motivo'; END $$;
UPDATE admin.billing_subscriptions SET status='canceled', canceled_at=now(), cancel_reason='payment_failed'
 WHERE id='ffffffff-0000-0000-0000-000000000001';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM admin.billing_subscriptions WHERE status = 'canceled' AND (next_charge_at IS NOT NULL OR retry_count <> 0))
  THEN RAISE EXCEPTION 'FALLO: cancelada conserva próximo cobro o reintentos'; END IF;
  RAISE NOTICE 'OK cancelar limpia próximo cobro y reintentos';
END $$;
DO $$ BEGIN
  UPDATE admin.billing_subscriptions SET status='active' WHERE id='ffffffff-0000-0000-0000-000000000001';
  RAISE EXCEPTION 'FALLO: suscripción cancelada reactivada';
EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK cancelada es terminal'; END $$;

-- Eventos inmutables
INSERT INTO admin.billing_events (tenant_id, subscription_id, user_id, type, actor_type, payload)
VALUES ('11111111-1111-1111-1111-111111111111','ffffffff-0000-0000-0000-000000000001','99999999-0000-0000-0000-000000000001',
        'subscription.canceled','system','{"reason":"payment_failed"}');
DO $$ BEGIN
  DELETE FROM admin.billing_events;
  RAISE EXCEPTION 'FALLO: evento borrado';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK billing_events inmutable'; END $$;

-- Historial sin secretos
DO $$ BEGIN
  IF (SELECT count(*) FROM admin.billing_payment_methods_history) < 2
     OR EXISTS (SELECT 1 FROM admin.billing_payment_methods_history WHERE tbk_user_enc IS NOT NULL OR inscription_token_hash IS NOT NULL)
  THEN RAISE EXCEPTION 'FALLO: historial de tarjetas ausente o con secretos'; END IF;
  IF (SELECT string_agg(status, ',' ORDER BY history_id) FROM admin.billing_subscriptions_history
       WHERE id = 'ffffffff-0000-0000-0000-000000000001') <> 'incomplete,active,past_due,canceled'
  THEN RAISE EXCEPTION 'FALLO: historial de suscripción incompleto'; END IF;
  RAISE NOTICE 'OK historial completo y sin secretos';
END $$;

-- ===== Tenant 2: aislamiento =====
SET app.tenant_id = '22222222-2222-2222-2222-222222222222';
DO $$ BEGIN
  IF (SELECT count(*) FROM admin.billing_subscriptions) + (SELECT count(*) FROM admin.billing_charges)
     + (SELECT count(*) FROM admin.billing_payment_methods) <> 0 THEN RAISE EXCEPTION 'FALLO: RLS deja ver otro tenant'; END IF;
  RAISE NOTICE 'OK RLS aísla facturación entre tenants';
END $$;

-- ===== Solo lectura =====
RESET ROLE; SET ROLE ro_test;
SET app.tenant_id = '11111111-1111-1111-1111-111111111111';
SELECT card_type, card_last4, status FROM admin.billing_payment_methods;
DO $$ BEGIN
  PERFORM tbk_user_enc FROM admin.billing_payment_methods;
  RAISE EXCEPTION 'FALLO: RO lee tbk_user';
EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'OK RO sin acceso a tbk_user'; END $$;

-- ===== Mantención: purga inscripciones abandonadas =====
RESET ROLE;
INSERT INTO admin.billing_payment_methods (tenant_id, user_id, user_email, tbk_username, status, failure_code, created_at)
VALUES ('11111111-1111-1111-1111-111111111111', gen_random_uuid(), 'viejo@example.com', 'u-viejo', 'aborted', 'TBK_ABORTED', now() - interval '40 days');
CALL admin.billing_maintenance();
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM admin.billing_payment_methods WHERE user_email = 'viejo@example.com')
  THEN RAISE EXCEPTION 'FALLO: inscripción abandonada no purgada'; END IF;
  IF NOT EXISTS (SELECT 1 FROM admin.billing_payment_methods WHERE id = 'eeeeeeee-0000-0000-0000-000000000001')
  THEN RAISE EXCEPTION 'FALLO: la purga borró una tarjeta con cobros'; END IF;
  RAISE NOTICE 'OK mantención purga solo inscripciones abandonadas';
END $$;
\echo TODAS LAS PRUEBAS DE FACTURACIÓN PASARON
