-- =====================================================================
--  AgeCare · Pasarela de pago y suscripciones — Modelo físico PostgreSQL 16
--  Esquema: admin (prefijo billing_)      Versión 1 · Wellq Co · octubre 2026
--
--  Se aplica DESPUÉS de agecare_admin_ddl.sql: reutiliza sus funciones
--  comunes (current_tenant_id, current_actor_id, tg_set_updated_at,
--  tg_forbid_change, enable_history), la política RLS por tenant y los
--  roles agecare_admin_api / agecare_admin_jobs / agecare_admin_ro.
--
--  Proveedor: Transbank Oneclick Mall (API REST v1.2).
--    · La tarjeta se inscribe una vez (tbk_user) y AgeCare cobra cada mes
--      desde un job: Transbank no ofrece suscripciones ni webhooks.
--    · Ningún dato de tarjeta se almacena salvo tipo y últimos 4 dígitos.
--    · tbk_user se guarda cifrado en aplicación (Key Vault) y nunca se
--      copia al historial ni es visible para el rol de solo lectura.
--
--  Bloques:  B0 permisos y parámetros · B1 catálogos · B2 medios de pago
--            B3 suscripciones · B4 cobros · B5 reembolsos · B6 eventos
--            B7 historial · B8 RLS · B9 grants · B10 retención
-- =====================================================================

\set ON_ERROR_STOP on
SET search_path = admin, public;

-- ---------------------------------------------------------------------
-- B0 · Módulos de permisos y parámetros de facturación
-- ---------------------------------------------------------------------
ALTER TABLE admin.admin_role_permissions DROP CONSTRAINT IF EXISTS admin_role_permissions_module_check;
ALTER TABLE admin.admin_role_permissions ADD CONSTRAINT admin_role_permissions_module_check
  CHECK (module IN ('metrics','ops','support','content','marketplace','moderation','settings',
                    'legal','staff','audit','billing','billing_refunds'));

INSERT INTO admin.admin_role_permissions (role_code, module, access) VALUES
  ('admin','billing','write'), ('admin','billing_refunds','write'),
  ('analyst','billing','read'),
  ('support','billing','write')
ON CONFLICT (role_code, module) DO UPDATE SET access = EXCLUDED.access;

INSERT INTO admin.setting_definitions (key, description, value_schema, default_value, category, is_secret) VALUES
  ('billing_dunning', 'Reintentos de cobro tras un rechazo (días desde el fallo anterior) antes de cancelar la suscripción',
   '{"type":"object","properties":{"retry_days":{"type":"array","items":{"type":"integer","minimum":1,"maximum":15},"minItems":0,"maxItems":6}},"required":["retry_days"],"additionalProperties":false}',
   '{"retry_days":[1,3,5]}', 'plans', false)
ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description, value_schema = EXCLUDED.value_schema,
  default_value = EXCLUDED.default_value, category = EXCLUDED.category;

-- La unidad facturable del plan determina el tipo de beneficiario de la suscripción
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'plans_code_unit_uq') THEN
    ALTER TABLE admin.plans ADD CONSTRAINT plans_code_unit_uq UNIQUE (code, billing_unit);
  END IF;
END $$;

-- ---------------------------------------------------------------------
-- B1 · Catálogo de transiciones de suscripción
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_subscription_transitions (
  from_status varchar(12) NOT NULL,
  to_status   varchar(12) NOT NULL,
  PRIMARY KEY (from_status, to_status)
);
COMMENT ON TABLE admin.billing_subscription_transitions IS 'Transiciones válidas de suscripción. El trigger tg_billing_subscription_transition rechaza cualquier otra.';

INSERT INTO admin.billing_subscription_transitions (from_status, to_status) VALUES
  ('incomplete','active'), ('incomplete','expired'), ('incomplete','canceled'),
  ('active','past_due'),   ('active','canceled'),
  ('past_due','active'),   ('past_due','canceled')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- B2 · Medios de pago (inscripciones Oneclick)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_payment_methods (
  id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid        NOT NULL REFERENCES admin.tenants(id),
  user_id                uuid        NOT NULL,
  user_email             citext      NOT NULL,
  provider               varchar(24) NOT NULL DEFAULT 'transbank_oneclick'
                                     CHECK (provider IN ('transbank_oneclick','fake')),
  tbk_username           varchar(40) NOT NULL,
  inscription_token_hash char(64)    UNIQUE,
  inscription_expires_at timestamptz,
  tbk_user_enc           bytea,
  card_type              varchar(20),
  card_last4             char(4)     CHECK (card_last4 ~ '^[0-9]{4}$'),
  authorization_code     varchar(6),
  status                 varchar(10) NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending','active','failed','aborted','removed')),
  failure_code           varchar(40),
  activated_at           timestamptz,
  removed_at             timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT billing_pm_active_ck  CHECK (status <> 'active'  OR (tbk_user_enc IS NOT NULL AND card_last4 IS NOT NULL AND activated_at IS NOT NULL)),
  CONSTRAINT billing_pm_removed_ck CHECK ((status = 'removed') = (removed_at IS NOT NULL)),
  CONSTRAINT billing_pm_failed_ck  CHECK (status NOT IN ('failed','aborted') OR failure_code IS NOT NULL)
);
-- Una sola tarjeta activa por usuario: al inscribir una nueva, la anterior pasa a removed
CREATE UNIQUE INDEX IF NOT EXISTS billing_pm_one_active_uq ON admin.billing_payment_methods (tenant_id, user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS billing_pm_user_idx ON admin.billing_payment_methods (tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS billing_pm_pending_idx ON admin.billing_payment_methods (inscription_expires_at) WHERE status = 'pending';
COMMENT ON TABLE admin.billing_payment_methods IS 'Tarjetas inscritas en Transbank Oneclick Mall. Sin datos de tarjeta salvo tipo y últimos 4 dígitos. Historial en billing_payment_methods_history (sin tbk_user_enc ni hash del token).';
COMMENT ON COLUMN admin.billing_payment_methods.user_id IS 'Referencia lógica a app.users.id (pagador).';
COMMENT ON COLUMN admin.billing_payment_methods.tbk_username IS 'username enviado a Transbank (≤ 40). Debe repetirse en cada cobro y al eliminar la inscripción.';
COMMENT ON COLUMN admin.billing_payment_methods.inscription_token_hash IS 'SHA-256 del TBK_TOKEN de la inscripción: permite ubicarla en el retorno de Webpay sin guardar el token.';
COMMENT ON COLUMN admin.billing_payment_methods.tbk_user_enc IS 'tbk_user cifrado en aplicación (Fernet, llave en Key Vault). Nunca en claro.';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.billing_payment_methods
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- ---------------------------------------------------------------------
-- B3 · Suscripciones
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_subscriptions (
  id                       uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                uuid         NOT NULL REFERENCES admin.tenants(id),
  user_id                  uuid         NOT NULL,
  user_email               citext       NOT NULL,
  user_name                varchar(120),
  plan_code                varchar(12)  NOT NULL CHECK (plan_code <> 'free'),
  beneficiary_type         varchar(12)  NOT NULL,
  beneficiary_id           uuid         NOT NULL,
  status                   varchar(12)  NOT NULL DEFAULT 'incomplete'
                                        CHECK (status IN ('incomplete','active','past_due','canceled','expired')),
  payment_method_id        uuid         REFERENCES admin.billing_payment_methods(id),
  price_amount             integer      NOT NULL CHECK (price_amount > 0),
  currency_code            char(3)      NOT NULL DEFAULT 'CLP',
  billing_anchor_day       smallint     CHECK (billing_anchor_day BETWEEN 1 AND 31),
  current_period_start     timestamptz,
  current_period_end       timestamptz,
  next_charge_at           timestamptz,
  retry_count              smallint     NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 6),
  cancel_at_period_end     boolean      NOT NULL DEFAULT false,
  pending_plan_code        varchar(12)  CHECK (pending_plan_code <> 'free'),
  pending_beneficiary_type varchar(12),
  pending_beneficiary_id   uuid,
  canceled_at              timestamptz,
  cancel_reason            varchar(20)  CHECK (cancel_reason IN ('user_request','payment_failed','admin','fraud','plan_change')),
  cancel_note              varchar(500),
  started_at               timestamptz,
  created_at               timestamptz  NOT NULL DEFAULT now(),
  updated_at               timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT billing_sub_plan_fk FOREIGN KEY (plan_code, beneficiary_type)
    REFERENCES admin.plans(code, billing_unit),
  CONSTRAINT billing_sub_pending_plan_fk FOREIGN KEY (pending_plan_code, pending_beneficiary_type)
    REFERENCES admin.plans(code, billing_unit),
  CONSTRAINT billing_sub_pending_ck CHECK (
    (pending_plan_code IS NULL) = (pending_beneficiary_type IS NULL) AND
    (pending_plan_code IS NULL) = (pending_beneficiary_id IS NULL)),
  CONSTRAINT billing_sub_live_ck CHECK (status NOT IN ('active','past_due') OR (
    payment_method_id IS NOT NULL AND current_period_start IS NOT NULL AND current_period_end IS NOT NULL
    AND billing_anchor_day IS NOT NULL AND started_at IS NOT NULL)),
  CONSTRAINT billing_sub_period_ck CHECK (current_period_end IS NULL OR current_period_end > current_period_start),
  CONSTRAINT billing_sub_canceled_ck CHECK ((status = 'canceled') = (canceled_at IS NOT NULL AND cancel_reason IS NOT NULL)),
  CONSTRAINT billing_sub_retry_ck CHECK (status = 'past_due' OR retry_count = 0)
);
-- Una suscripción viva por beneficiario (usuario, familia o proveedor)
CREATE UNIQUE INDEX IF NOT EXISTS billing_sub_one_live_uq ON admin.billing_subscriptions (tenant_id, beneficiary_type, beneficiary_id)
  WHERE status IN ('incomplete','active','past_due');
CREATE INDEX IF NOT EXISTS billing_sub_due_idx    ON admin.billing_subscriptions (next_charge_at) WHERE status IN ('active','past_due');
CREATE INDEX IF NOT EXISTS billing_sub_status_idx ON admin.billing_subscriptions (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS billing_sub_user_idx   ON admin.billing_subscriptions (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS billing_sub_plan_idx   ON admin.billing_subscriptions (tenant_id, plan_code, status);
CREATE INDEX IF NOT EXISTS billing_sub_search_idx ON admin.billing_subscriptions USING gin ((user_email::text || ' ' || coalesce(user_name, '')) gin_trgm_ops);
COMMENT ON TABLE admin.billing_subscriptions IS 'Suscripciones mensuales a planes de pago (Dorado por usuario, Platino por familia, Proveedor por proveedor). Fuente de MRR y de la mezcla de planes. Historial en billing_subscriptions_history.';
COMMENT ON COLUMN admin.billing_subscriptions.beneficiary_id IS 'Referencia lógica a app.users / app.families / app.providers según beneficiary_type (= plans.billing_unit).';
COMMENT ON COLUMN admin.billing_subscriptions.price_amount IS 'Precio mensual congelado al suscribirse (plan_prices). Los cambios de precio aplican a suscripciones nuevas.';
COMMENT ON COLUMN admin.billing_subscriptions.billing_anchor_day IS 'Día del mes del primer pago; los periodos siguientes se anclan a él (acotado al último día del mes).';
COMMENT ON COLUMN admin.billing_subscriptions.next_charge_at IS 'Próximo intento de cobro: fin de periodo o fecha del reintento si está en mora. NULL si no se cobrará más.';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.billing_subscriptions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE OR REPLACE FUNCTION admin.tg_billing_subscription_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> OLD.status THEN
    IF NOT EXISTS (SELECT 1 FROM admin.billing_subscription_transitions t
                    WHERE t.from_status = OLD.status AND t.to_status = NEW.status) THEN
      RAISE EXCEPTION 'INVALID_TRANSITION: suscripción % → % no permitido', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IN ('canceled','expired') THEN
      NEW.next_charge_at := NULL;
      NEW.retry_count := 0;
      NEW.pending_plan_code := NULL; NEW.pending_beneficiary_type := NULL; NEW.pending_beneficiary_id := NULL;
    END IF;
    IF NEW.status = 'active' THEN NEW.retry_count := 0; END IF;
  END IF;
  -- Datos económicos inmutables: el precio no se reescribe salvo al aplicar un cambio de plan
  IF NEW.price_amount <> OLD.price_amount AND NEW.plan_code = OLD.plan_code THEN
    RAISE EXCEPTION 'PRICE_LOCKED: el precio de una suscripción solo cambia junto con el plan'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_transition BEFORE UPDATE ON admin.billing_subscriptions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_billing_subscription_transition();

-- ---------------------------------------------------------------------
-- B4 · Cobros (transacciones Oneclick Mall)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_charges (
  id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  subscription_id     uuid         NOT NULL REFERENCES admin.billing_subscriptions(id),
  payment_method_id   uuid         NOT NULL REFERENCES admin.billing_payment_methods(id),
  user_id             uuid         NOT NULL,
  plan_code           varchar(12)  NOT NULL REFERENCES admin.plans(code),
  kind                varchar(10)  NOT NULL CHECK (kind IN ('initial','renewal','retry','manual')),
  attempt             smallint     NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 7),
  buy_order           varchar(26)  NOT NULL UNIQUE CHECK (buy_order ~ '^[A-Za-z0-9-]{1,26}$'),
  detail_buy_order    varchar(26)  NOT NULL UNIQUE CHECK (detail_buy_order ~ '^[A-Za-z0-9-]{1,26}$'),
  commerce_code       varchar(12)  NOT NULL,
  amount              integer      NOT NULL CHECK (amount > 0),
  currency_code       char(3)      NOT NULL DEFAULT 'CLP',
  status              varchar(20)  NOT NULL DEFAULT 'pending'
                                   CHECK (status IN ('pending','authorized','failed','unknown','refunded','partially_refunded')),
  refunded_amount     integer      NOT NULL DEFAULT 0,
  period_start        timestamptz  NOT NULL,
  period_end          timestamptz  NOT NULL,
  response_code       smallint,
  authorization_code  varchar(6),
  payment_type_code   varchar(3),
  installments_number smallint,
  card_last4          char(4),
  transaction_date    timestamptz,
  accounting_date     char(4),
  failure_code        varchar(40),
  failure_message     varchar(300),
  gateway_response    jsonb,
  requested_by        uuid         REFERENCES admin.admin_users(id),
  resolved_at         timestamptz,
  created_at          timestamptz  NOT NULL DEFAULT now(),
  updated_at          timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT billing_charge_period_ck   CHECK (period_end > period_start),
  CONSTRAINT billing_charge_refund_ck   CHECK (refunded_amount BETWEEN 0 AND amount),
  CONSTRAINT billing_charge_paid_ck     CHECK (status NOT IN ('authorized','refunded','partially_refunded')
                                               OR (response_code = 0 AND authorization_code IS NOT NULL AND resolved_at IS NOT NULL)),
  CONSTRAINT billing_charge_failed_ck   CHECK (status <> 'failed' OR (failure_code IS NOT NULL AND resolved_at IS NOT NULL)),
  CONSTRAINT billing_charge_refunded_ck CHECK (
    (status = 'refunded' AND refunded_amount = amount) OR
    (status = 'partially_refunded' AND refunded_amount > 0 AND refunded_amount < amount) OR
    (status NOT IN ('refunded','partially_refunded') AND refunded_amount = 0)),
  CONSTRAINT billing_charge_manual_ck   CHECK (kind <> 'manual' OR requested_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS billing_charges_sub_idx       ON admin.billing_charges (subscription_id, created_at DESC);
CREATE INDEX IF NOT EXISTS billing_charges_status_idx    ON admin.billing_charges (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS billing_charges_created_idx   ON admin.billing_charges (tenant_id, created_at DESC);
-- Como máximo un cobro en vuelo por suscripción: dos workers no pueden cobrar la misma renovación
CREATE UNIQUE INDEX IF NOT EXISTS billing_charges_one_inflight_uq ON admin.billing_charges (subscription_id) WHERE status IN ('pending','unknown');
CREATE INDEX IF NOT EXISTS billing_charges_unresolved_idx ON admin.billing_charges (created_at) WHERE status IN ('pending','unknown');
COMMENT ON TABLE admin.billing_charges IS 'Cada intento de cobro a Transbank (una transacción Oneclick Mall con un detalle). Se inserta en pending ANTES de llamar a Transbank; si la llamada no responde queda unknown y el job la concilia con GET /transactions/{buy_order}.';
COMMENT ON COLUMN admin.billing_charges.buy_order IS 'Orden de compra del mall (≤ 26). Única: Transbank la usa para consultar estado y reembolsar.';
COMMENT ON COLUMN admin.billing_charges.gateway_response IS 'Respuesta de Transbank saneada (sin tbk_user). Evidencia ante contracargos.';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.billing_charges
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Identidad y monto del cobro son inmutables; un cobro resuelto no vuelve a pending
CREATE OR REPLACE FUNCTION admin.tg_billing_charge_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.buy_order, NEW.detail_buy_order, NEW.amount, NEW.subscription_id, NEW.commerce_code)
     IS DISTINCT FROM (OLD.buy_order, OLD.detail_buy_order, OLD.amount, OLD.subscription_id, OLD.commerce_code) THEN
    RAISE EXCEPTION 'CHARGE_LOCKED: orden de compra, monto y suscripción de un cobro son inmutables'
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status IN ('authorized','failed','refunded','partially_refunded') AND NEW.status IN ('pending','unknown') THEN
    RAISE EXCEPTION 'INVALID_TRANSITION: cobro % → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'failed' AND NEW.status <> 'failed' THEN
    RAISE EXCEPTION 'INVALID_TRANSITION: un cobro rechazado no cambia de estado' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_guard BEFORE UPDATE ON admin.billing_charges
  FOR EACH ROW EXECUTE FUNCTION admin.tg_billing_charge_guard();

-- ---------------------------------------------------------------------
-- B5 · Reembolsos (Transbank responde REVERSED o NULLIFIED)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_refunds (
  id                 uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid         NOT NULL REFERENCES admin.tenants(id),
  charge_id          uuid         NOT NULL REFERENCES admin.billing_charges(id),
  amount             integer      NOT NULL CHECK (amount > 0),
  status             varchar(10)  NOT NULL CHECK (status IN ('succeeded','failed')),
  refund_type        varchar(10)  CHECK (refund_type IN ('REVERSED','NULLIFIED')),
  authorization_code varchar(6),
  response_code      smallint,
  reason             varchar(500) NOT NULL CHECK (length(reason) >= 5),
  failure_message    varchar(300),
  gateway_response   jsonb,
  requested_by       uuid         NOT NULL REFERENCES admin.admin_users(id),
  requested_by_name  varchar(120),
  created_at         timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT billing_refund_ok_ck CHECK (status <> 'succeeded' OR refund_type IS NOT NULL),
  CONSTRAINT billing_refund_failed_ck CHECK (status <> 'failed' OR failure_message IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS billing_refunds_charge_idx  ON admin.billing_refunds (charge_id, created_at);
CREATE INDEX IF NOT EXISTS billing_refunds_created_idx ON admin.billing_refunds (tenant_id, created_at DESC);
COMMENT ON TABLE admin.billing_refunds IS 'Reembolsos solicitados desde la consola (solo rol admin). Inmutable: un reembolso fallido se registra y se vuelve a intentar con una fila nueva.';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.billing_refunds
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- Un reembolso exitoso no puede superar lo cobrado; recalcula refunded_amount y el estado del cobro
CREATE OR REPLACE FUNCTION admin.tg_billing_refund_apply() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_charge admin.billing_charges%ROWTYPE;
  v_total  integer;
BEGIN
  SELECT * INTO v_charge FROM admin.billing_charges WHERE id = NEW.charge_id FOR UPDATE;
  IF v_charge.status NOT IN ('authorized','partially_refunded','refunded') THEN
    RAISE EXCEPTION 'CHARGE_NOT_REFUNDABLE: el cobro está en estado %', v_charge.status USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'succeeded' THEN
    SELECT COALESCE(SUM(amount), 0) + NEW.amount INTO v_total
      FROM admin.billing_refunds WHERE charge_id = NEW.charge_id AND status = 'succeeded';
    IF v_total > v_charge.amount THEN
      RAISE EXCEPTION 'REFUND_EXCEEDS_CHARGE: reembolsos % superan el cobro %', v_total, v_charge.amount
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE admin.billing_charges
       SET refunded_amount = v_total,
           status = CASE WHEN v_total = amount THEN 'refunded' ELSE 'partially_refunded' END
     WHERE id = NEW.charge_id;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_refund_apply BEFORE INSERT ON admin.billing_refunds
  FOR EACH ROW EXECUTE FUNCTION admin.tg_billing_refund_apply();

-- ---------------------------------------------------------------------
-- B6 · Eventos de facturación (línea de tiempo + fuente de notificaciones)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.billing_events (
  id              bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       uuid         NOT NULL REFERENCES admin.tenants(id),
  subscription_id uuid         REFERENCES admin.billing_subscriptions(id),
  charge_id       uuid         REFERENCES admin.billing_charges(id),
  user_id         uuid,
  type            varchar(40)  NOT NULL CHECK (type ~ '^[a-z_]+\.[a-z_]+$'),
  actor_type      varchar(10)  NOT NULL CHECK (actor_type IN ('user','admin','system')),
  actor_id        uuid,
  payload         jsonb        NOT NULL DEFAULT '{}',
  created_at      timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT billing_events_actor_ck CHECK (actor_type = 'system' OR actor_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS billing_events_sub_idx  ON admin.billing_events (subscription_id, created_at);
CREATE INDEX IF NOT EXISTS billing_events_type_idx ON admin.billing_events (tenant_id, type, created_at DESC);
COMMENT ON TABLE admin.billing_events IS 'Bitácora inmutable del ciclo de vida (subscription.activated, charge.failed, payment_method.removed…). La API de la app la consume para notificar al usuario.';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.billing_events
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- ---------------------------------------------------------------------
-- B7 · Historial: los secretos de pago nunca se copian
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION admin.tg_write_history() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = admin, pg_temp AS $$
DECLARE
  v_cols text;
  v_row  jsonb;
BEGIN
  v_row := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  -- Los secretos nunca se copian al historial
  v_row := v_row - 'password_hash' - 'mfa_secret_enc' - 'refresh_token_hash' - 'token_hash'
                 - 'tbk_user_enc' - 'inscription_token_hash';
  SELECT string_agg(quote_ident(attname), ',' ORDER BY attnum)
    INTO v_cols
    FROM pg_attribute
   WHERE attrelid = TG_RELID AND attnum > 0 AND NOT attisdropped;
  EXECUTE format(
    'INSERT INTO %I.%I (op, changed_at, changed_by, %s) SELECT $1, now(), $2, %s FROM jsonb_populate_record(NULL::%I.%I, $3) r',
    TG_TABLE_SCHEMA, TG_TABLE_NAME || '_history', v_cols,
    (SELECT string_agg('r.' || quote_ident(attname), ',' ORDER BY attnum)
       FROM pg_attribute WHERE attrelid = TG_RELID AND attnum > 0 AND NOT attisdropped),
    TG_TABLE_SCHEMA, TG_TABLE_NAME)
  USING left(TG_OP, 1), admin.current_actor_id(), v_row;
  RETURN NULL;
END $$;

CALL admin.enable_history('billing_payment_methods');
CALL admin.enable_history('billing_subscriptions');
CALL admin.enable_history('billing_charges');

-- ---------------------------------------------------------------------
-- B8 · Row-Level Security por tenant (misma política que el resto del esquema)
-- ---------------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
     WHERE n.nspname = 'admin' AND c.relkind IN ('r','p') AND NOT c.relispartition
       AND c.relname LIKE 'billing\_%'
  LOOP
    EXECUTE format('ALTER TABLE admin.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON admin.%I', r.relname);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON admin.%I
                     USING (tenant_id = admin.current_tenant_id())
                     WITH CHECK (tenant_id = admin.current_tenant_id())$p$, r.relname);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- B9 · Grants
-- ---------------------------------------------------------------------
GRANT SELECT ON admin.billing_subscription_transitions, admin.billing_payment_methods,
                admin.billing_subscriptions, admin.billing_charges, admin.billing_refunds,
                admin.billing_events, admin.billing_payment_methods_history,
                admin.billing_subscriptions_history, admin.billing_charges_history
  TO agecare_admin_api, agecare_admin_ro;
GRANT INSERT, UPDATE ON admin.billing_payment_methods, admin.billing_subscriptions, admin.billing_charges
  TO agecare_admin_api;
GRANT INSERT ON admin.billing_refunds, admin.billing_events TO agecare_admin_api;

-- Solo lectura (BI): sin el token cifrado ni el hash del token de inscripción
REVOKE SELECT ON admin.billing_payment_methods FROM agecare_admin_ro;
GRANT SELECT (id, tenant_id, user_id, user_email, provider, card_type, card_last4, status,
              failure_code, activated_at, removed_at, created_at, updated_at)
  ON admin.billing_payment_methods TO agecare_admin_ro;

-- Jobs (renovaciones, conciliación, expiración): sin borrar ni reescribir bitácoras
GRANT SELECT, INSERT, UPDATE, DELETE ON admin.billing_subscription_transitions, admin.billing_payment_methods,
      admin.billing_subscriptions, admin.billing_charges, admin.billing_refunds, admin.billing_events
  TO agecare_admin_jobs;
REVOKE UPDATE, DELETE ON admin.billing_refunds, admin.billing_events FROM agecare_admin_jobs;
REVOKE DELETE ON admin.billing_charges, admin.billing_subscriptions FROM agecare_admin_jobs;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs, agecare_admin_ro;

-- ---------------------------------------------------------------------
-- B10 · Retención
--   Cobros, reembolsos y eventos se conservan 6 años (obligación tributaria
--   chilena, art. 17 del Código Tributario); no hay borrado automático.
--   Solo se purgan inscripciones que nunca se completaron.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE admin.billing_maintenance()
LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM admin.billing_payment_methods
   WHERE status IN ('pending','aborted','failed')
     AND created_at < now() - interval '30 days'
     AND NOT EXISTS (SELECT 1 FROM admin.billing_subscriptions s WHERE s.payment_method_id = billing_payment_methods.id)
     AND NOT EXISTS (SELECT 1 FROM admin.billing_charges c WHERE c.payment_method_id = billing_payment_methods.id);
END $$;
COMMENT ON PROCEDURE admin.billing_maintenance() IS 'Ejecutar a diario junto con partition_maintenance: purga inscripciones abandonadas (sin datos de tarjeta).';
GRANT EXECUTE ON PROCEDURE admin.billing_maintenance() TO agecare_admin_jobs;

-- Fin del DDL de facturación
