-- =====================================================================
--  AgeCare · Consola de Administración — Modelo físico PostgreSQL 16
--  Esquema: admin            Versión 1 · Wellq Co · septiembre 2026
--
--  Deriva del modelo lógico agecare_admin.dbml. Idempotente en lo posible
--  (IF NOT EXISTS) y ejecutable con psql en Azure Database for PostgreSQL
--  Flexible Server (o en local). Pensado para envolverse en la migración
--  Alembic 0001_initial como op.execute() por bloques.
--
--  Bloques:  0 extensiones y esquema · 1 roles de BD · 2 funciones comunes
--            3 tenants y catálogos · 4 staff y auditoría · 5 métricas
--            6 perfiles y adopción · 7 estado operativo · 8 soporte
--            9 contenido y marketplace · 10 moderación · 11 configuración
--            y legales · 12 jobs · 13 historial (*_history) · 14 RLS
--            15 grants · 16 particiones y retención · 17 datos semilla
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- 0 · Extensiones y esquema
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS citext;    -- correos sin distinguir mayúsculas
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- búsqueda ILIKE (q) con índices GIN

CREATE SCHEMA IF NOT EXISTS admin;
COMMENT ON SCHEMA admin IS 'Consola de Administración de AgeCare (staff, métricas agregadas, operación). Separado de las tablas de la app.';

SET search_path = admin, public;

-- ---------------------------------------------------------------------
-- 1 · Roles de base de datos (NOLOGIN; los usuarios reales heredan)
--     agecare_admin_api  : servicio FastAPI. Sujeto a RLS. Solo INSERT en audit_log.
--     agecare_admin_jobs : workers de agregación/monitor. Sin RLS (multi-tenant).
--     agecare_admin_ro   : BI / analistas SQL. Solo lectura, sujeto a RLS.
--   Los usuarios de login se crean aparte, p. ej.:
--     CREATE ROLE api_prod  LOGIN PASSWORD '…' IN ROLE agecare_admin_api;
--     CREATE ROLE jobs_prod LOGIN PASSWORD '…' BYPASSRLS IN ROLE agecare_admin_jobs;
--   Nota: BYPASSRLS es un atributo de rol y NO se hereda por pertenencia;
--   el usuario de login de los jobs debe declararlo explícitamente.
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_api') THEN
    CREATE ROLE agecare_admin_api NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_jobs') THEN
    CREATE ROLE agecare_admin_jobs NOLOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agecare_admin_ro') THEN
    CREATE ROLE agecare_admin_ro NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2 · Funciones comunes
-- ---------------------------------------------------------------------

-- Tenant de la petición actual (lo fija la API con SET LOCAL app.tenant_id = '…').
CREATE OR REPLACE FUNCTION admin.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

-- Actor de la petición actual (SET LOCAL app.actor_id = '…'); NULL en jobs.
CREATE OR REPLACE FUNCTION admin.current_actor_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.actor_id', true), '')::uuid
$$;

-- Valida nombres IANA de zona horaria (declarada IMMUTABLE para poder usarse en CHECK)
CREATE OR REPLACE FUNCTION admin.is_valid_timezone(p_tz text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_tz)
$$;

-- updated_at automático
CREATE OR REPLACE FUNCTION admin.tg_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- Impide UPDATE/DELETE (tablas inmutables)
CREATE OR REPLACE FUNCTION admin.tg_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'La tabla %.% es inmutable: no admite %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- Historial genérico: copia cada versión de la fila (I/U → NEW, D → OLD)
-- en admin.<tabla>_history, que tiene las mismas columnas más metadatos.
CREATE OR REPLACE FUNCTION admin.tg_write_history() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = admin, pg_temp AS $$
DECLARE
  v_cols text;
  v_row  jsonb;
BEGIN
  v_row := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  -- Los secretos nunca se copian al historial
  v_row := v_row - 'password_hash' - 'mfa_secret_enc' - 'refresh_token_hash' - 'token_hash';
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

-- Crea la tabla <t>_history a partir de <t> y engancha el trigger.
CREATE OR REPLACE PROCEDURE admin.enable_history(p_table text)
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS admin.%1$I (
      history_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      op          char(1)     NOT NULL CHECK (op IN ('I','U','D')),
      changed_at  timestamptz NOT NULL DEFAULT now(),
      changed_by  uuid,
      LIKE admin.%2$I
    )$f$, p_table || '_history', p_table);
  EXECUTE format('COMMENT ON TABLE admin.%I IS %L',
    p_table || '_history', 'Historial de versiones de admin.' || p_table || ' (una fila por INSERT/UPDATE/DELETE; changed_by = admin_users.id).');
  EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON admin.%I (tenant_id, changed_at)',
    p_table || '_history_tenant_time_idx', p_table || '_history');
  EXECUTE format('CREATE OR REPLACE TRIGGER trg_history AFTER INSERT OR UPDATE OR DELETE ON admin.%I FOR EACH ROW EXECUTE FUNCTION admin.tg_write_history()', p_table);
  -- El historial también es inmutable
  EXECUTE format('CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.%I FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change()', p_table || '_history');
END $$;

-- ---------------------------------------------------------------------
-- 3 · Tenants y catálogos globales
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.tenants (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  code          varchar(20) NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9-]+$'),
  name          varchar(120) NOT NULL,
  country_code  char(2)     NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
  currency_code char(3)     NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
  timezone      varchar(40) NOT NULL,
  region        varchar(60),
  environment   varchar(20) NOT NULL DEFAULT 'production' CHECK (environment IN ('production','staging','development')),
  is_active     boolean     NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tenants_timezone_valid CHECK (admin.is_valid_timezone(timezone))
);
COMMENT ON TABLE admin.tenants IS 'Despliegue/mercado administrado por la consola. Aísla sus datos por RLS (tenant_id).';
COMMENT ON COLUMN admin.tenants.currency_code IS 'ISO 4217. Todos los importes (mrr_amount, price_amount) del tenant se expresan en esta moneda, en unidades enteras (CLP sin decimales).';
COMMENT ON COLUMN admin.tenants.timezone IS 'Zona horaria de negocio con la que los jobs cortan los días y semanas (America/Santiago).';

CREATE TABLE IF NOT EXISTS admin.tenant_counters (
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  counter_name varchar(40) NOT NULL,
  next_value   bigint      NOT NULL DEFAULT 1,
  PRIMARY KEY (tenant_id, counter_name)
);
COMMENT ON TABLE admin.tenant_counters IS 'Correlativos por tenant (ticket_number). Se incrementa con bloqueo de fila en el trigger.';

CREATE OR REPLACE FUNCTION admin.next_counter(p_tenant uuid, p_name text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE v bigint;
BEGIN
  INSERT INTO admin.tenant_counters (tenant_id, counter_name, next_value)
       VALUES (p_tenant, p_name, 2)
  ON CONFLICT (tenant_id, counter_name)
  DO UPDATE SET next_value = admin.tenant_counters.next_value + 1
  RETURNING next_value - 1 INTO v;
  RETURN v;
END $$;

CREATE TABLE IF NOT EXISTS admin.admin_roles (
  code                 varchar(20) PRIMARY KEY,
  name                 varchar(60) NOT NULL,
  description          varchar(300),
  requires_mfa_default boolean     NOT NULL DEFAULT false,
  sort_order           smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.admin_roles IS 'Roles internos del staff (spec 2.3): admin, analyst, support, editor, moderator.';

CREATE TABLE IF NOT EXISTS admin.admin_role_permissions (
  role_code varchar(20) NOT NULL REFERENCES admin.admin_roles(code),
  module    varchar(30) NOT NULL CHECK (module IN ('metrics','ops','support','content','marketplace','moderation','settings','legal','staff','audit')),
  access    varchar(5)  NOT NULL CHECK (access IN ('read','write')),
  PRIMARY KEY (role_code, module)
);
COMMENT ON TABLE admin.admin_role_permissions IS 'Matriz de permisos por módulo (spec 2.3). read = L, write = E; sin fila = sin acceso.';

CREATE TABLE IF NOT EXISTS admin.app_roles (
  code       varchar(12) PRIMARY KEY,
  name       varchar(60) NOT NULL,
  sort_order smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.app_roles IS 'Perfiles de usuario final (AppRole): family, caregiver, elder, doctor.';

CREATE TABLE IF NOT EXISTS admin.plans (
  code         varchar(12) PRIMARY KEY,
  name         varchar(60) NOT NULL,
  billing_unit varchar(12) NOT NULL CHECK (billing_unit IN ('none','user','family','provider')),
  is_paid      boolean     NOT NULL,
  sort_order   smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.plans IS 'Catálogo de planes (PlanCode). Precios vigentes en system_settings.plan_prices; congelados en metrics_plan_snapshot.';

CREATE TABLE IF NOT EXISTS admin.components (
  key          varchar(30) PRIMARY KEY,
  name         varchar(80) NOT NULL,
  is_async     boolean     NOT NULL DEFAULT false,
  latency_note varchar(200),
  sort_order   smallint    NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.components IS 'Componentes monitorizados (ComponentKey). Umbrales en system_settings ops_thresholds.<key>.';

CREATE TABLE IF NOT EXISTS admin.critical_processes (
  key        varchar(40)  PRIMARY KEY,
  name       varchar(120) NOT NULL,
  chain      varchar(300) NOT NULL,
  sort_order smallint     NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.critical_processes IS 'Procesos críticos de negocio medidos extremo a extremo (spec 5.3).';

CREATE TABLE IF NOT EXISTS admin.ticket_categories (
  code       varchar(20) PRIMARY KEY,
  name       varchar(80) NOT NULL,
  sort_order smallint    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin.features (
  key          varchar(50)  PRIMARY KEY,
  name         varchar(120) NOT NULL,
  expected_low boolean      NOT NULL DEFAULT false,
  note         varchar(300),
  sort_order   smallint     NOT NULL DEFAULT 0
);
COMMENT ON TABLE admin.features IS 'Catálogo de funcionalidades instrumentadas; se administra por seed/migración (spec 7).';
COMMENT ON COLUMN admin.features.expected_low IS 'true en funciones de emergencia (SOS): uso bajo correcto por diseño.';

CREATE TABLE IF NOT EXISTS admin.feature_roles (
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key) ON DELETE CASCADE,
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  PRIMARY KEY (feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_roles IS 'Perfiles a los que aplica cada función. Sin fila = null (celda gris) en la matriz de adopción.';

CREATE TABLE IF NOT EXISTS admin.setting_definitions (
  key           varchar(80)  PRIMARY KEY CHECK (key ~ '^[a-z0-9_.]+$'),
  description   varchar(300) NOT NULL,
  value_schema  jsonb        NOT NULL,
  default_value jsonb        NOT NULL,
  category      varchar(40)  NOT NULL,
  is_secret     boolean      NOT NULL DEFAULT false
);
COMMENT ON TABLE admin.setting_definitions IS 'Catálogo de claves de configuración con su JSON Schema (spec 12). Sin DELETE por API.';

CREATE TABLE IF NOT EXISTS admin.ticket_status_transitions (
  from_status varchar(15) NOT NULL,
  to_status   varchar(15) NOT NULL,
  PRIMARY KEY (from_status, to_status)
);
COMMENT ON TABLE admin.ticket_status_transitions IS 'Transiciones válidas de ticket (spec 8.4), aplicadas por trigger.';

CREATE TABLE IF NOT EXISTS admin.incident_status_transitions (
  from_status    varchar(15) NOT NULL,
  to_status      varchar(15) NOT NULL,
  is_maintenance boolean     NOT NULL,
  PRIMARY KEY (from_status, to_status, is_maintenance)
);
COMMENT ON TABLE admin.incident_status_transitions IS 'Transiciones válidas de incidente (spec 5.6), aplicadas por trigger.';

-- ---------------------------------------------------------------------
-- 4 · Staff, sesiones y auditoría
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.admin_users (
  id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid         NOT NULL REFERENCES admin.tenants(id),
  full_name       varchar(120) NOT NULL CHECK (length(full_name) BETWEEN 2 AND 120),
  email           citext       NOT NULL CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  role_code       varchar(20)  NOT NULL REFERENCES admin.admin_roles(code),
  password_hash   varchar(255),
  is_active       boolean      NOT NULL DEFAULT true,
  activated_at    timestamptz,
  mfa_required    boolean      NOT NULL DEFAULT false,
  mfa_enabled     boolean      NOT NULL DEFAULT false,
  mfa_secret_enc  bytea,
  failed_attempts smallint     NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  locked_until    timestamptz,
  last_login_at   timestamptz,
  created_by      uuid         REFERENCES admin.admin_users(id),
  created_at      timestamptz  NOT NULL DEFAULT now(),
  updated_at      timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT admin_users_email_tenant_uq UNIQUE (tenant_id, email),
  CONSTRAINT admin_users_mfa_secret_ck CHECK (NOT mfa_enabled OR mfa_secret_enc IS NOT NULL),
  CONSTRAINT admin_users_activated_ck CHECK (activated_at IS NULL OR password_hash IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS admin_users_tenant_role_idx ON admin.admin_users (tenant_id, role_code, is_active);
CREATE INDEX IF NOT EXISTS admin_users_search_idx ON admin.admin_users USING gin ((full_name || ' ' || email::text) gin_trgm_ops);
COMMENT ON TABLE admin.admin_users IS 'Cuentas del personal de Wellq Co (spec 3). Separadas de los usuarios de la app.';
COMMENT ON COLUMN admin.admin_users.password_hash IS 'Argon2id. NULL = cuenta pendiente de activación (login → ADMIN_DISABLED).';
COMMENT ON COLUMN admin.admin_users.mfa_secret_enc IS 'Secreto TOTP cifrado por la aplicación (Key Vault). Nunca en claro ni en audit_log.';
COMMENT ON COLUMN admin.admin_users.locked_until IS 'Bloqueo de 15 min tras 5 intentos fallidos en 10 min (spec 3.1).';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.admin_users
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Debe existir al menos un admin activo por tenant (spec 3.7 LAST_ADMIN)
CREATE OR REPLACE FUNCTION admin.tg_admin_users_last_admin() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.role_code = 'admin' AND OLD.is_active
     AND (NEW.role_code <> 'admin' OR NOT NEW.is_active) THEN
    IF NOT EXISTS (SELECT 1 FROM admin.admin_users u
                    WHERE u.tenant_id = OLD.tenant_id AND u.id <> OLD.id
                      AND u.role_code = 'admin' AND u.is_active AND u.activated_at IS NOT NULL) THEN
      RAISE EXCEPTION 'LAST_ADMIN: debe existir al menos una cuenta activa con rol admin'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_last_admin BEFORE UPDATE OF role_code, is_active ON admin.admin_users
  FOR EACH ROW EXECUTE FUNCTION admin.tg_admin_users_last_admin();

CREATE TABLE IF NOT EXISTS admin.admin_invitations (
  id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid        NOT NULL REFERENCES admin.tenants(id),
  admin_id   uuid        NOT NULL REFERENCES admin.admin_users(id) ON DELETE CASCADE,
  token_hash char(64)    NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used_at    timestamptz,
  created_by uuid        REFERENCES admin.admin_users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_invitations_expiry_ck CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS admin_invitations_admin_idx ON admin.admin_invitations (admin_id);
COMMENT ON TABLE admin.admin_invitations IS 'Enlaces de activación de un solo uso, 24 h (spec 3.5). token_hash = SHA-256 del token enviado por correo.';

CREATE TABLE IF NOT EXISTS admin.admin_sessions (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid        NOT NULL REFERENCES admin.tenants(id),
  admin_id           uuid        NOT NULL REFERENCES admin.admin_users(id) ON DELETE CASCADE,
  refresh_token_hash char(64)    NOT NULL UNIQUE,
  family_id          uuid        NOT NULL,
  rotated_from       uuid        REFERENCES admin.admin_sessions(id),
  expires_at         timestamptz NOT NULL,
  revoked_at         timestamptz,
  revoked_reason     varchar(30) CHECK (revoked_reason IN ('logout','rotated','reuse_detected','admin_disabled','expired')),
  ip                 inet,
  user_agent         varchar(300),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_sessions_revoked_ck CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx  ON admin.admin_sessions (admin_id, revoked_at);
CREATE INDEX IF NOT EXISTS admin_sessions_family_idx ON admin.admin_sessions (family_id);
CREATE INDEX IF NOT EXISTS admin_sessions_expiry_idx ON admin.admin_sessions (expires_at);
COMMENT ON TABLE admin.admin_sessions IS 'Refresh tokens rotatorios (12 h). Reusar un token rotado revoca toda la familia (spec 3.2).';
COMMENT ON COLUMN admin.admin_sessions.family_id IS 'Todas las rotaciones derivadas de un mismo login comparten family_id.';

CREATE TABLE IF NOT EXISTS admin.admin_login_attempts (
  id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  email        citext      NOT NULL,
  admin_id     uuid        REFERENCES admin.admin_users(id) ON DELETE SET NULL,
  succeeded    boolean     NOT NULL,
  failure_code varchar(30) CHECK (failure_code IN ('INVALID_CREDENTIALS','OTP_REQUIRED','OTP_INVALID','ADMIN_DISABLED','ACCOUNT_LOCKED')),
  ip           inet,
  user_agent   varchar(300),
  attempted_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_login_attempts_failure_ck CHECK (succeeded OR failure_code IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS admin_login_attempts_lookup_idx ON admin.admin_login_attempts (tenant_id, email, attempted_at DESC);
COMMENT ON TABLE admin.admin_login_attempts IS 'Intentos de login persistidos para el bloqueo progresivo (spec 3.1). Retención 90 días (job).';

-- Auditoría: particionada por mes, inmutable
CREATE TABLE IF NOT EXISTS admin.audit_log (
  id          uuid        NOT NULL DEFAULT gen_random_uuid(),
  tenant_id   uuid        NOT NULL REFERENCES admin.tenants(id),
  actor_id    uuid        REFERENCES admin.admin_users(id),
  actor_name  varchar(120),
  actor_role  varchar(20),
  action      varchar(60) NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
  entity_type varchar(40),
  entity_id   uuid,
  before      jsonb,
  after       jsonb,
  ip          inet,
  user_agent  varchar(300),
  request_id  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX IF NOT EXISTS audit_log_tenant_time_idx   ON admin.audit_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx         ON admin.audit_log (tenant_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx        ON admin.audit_log (tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_log_action_idx        ON admin.audit_log (tenant_id, action, created_at DESC);
COMMENT ON TABLE admin.audit_log IS 'Registro inmutable de acciones del staff (spec 14). Solo INSERT; particionada por mes; retención 24 meses.';
COMMENT ON COLUMN admin.audit_log.action IS 'Acción tipificada modulo.verbo: ticket.update, settings.update, auth.login_failed…';
COMMENT ON COLUMN admin.audit_log.before IS 'Estado previo (diff) con campos sensibles enmascarados por la aplicación.';

CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.audit_log
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- ---------------------------------------------------------------------
-- 5 · Métricas comerciales (agregados por jobs)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.store_downloads_daily (
  tenant_id   uuid        NOT NULL REFERENCES admin.tenants(id),
  day         date        NOT NULL,
  store       varchar(10) NOT NULL CHECK (store IN ('ios','android')),
  downloads   integer     NOT NULL DEFAULT 0 CHECK (downloads >= 0),
  ingested_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, store)
);
COMMENT ON TABLE admin.store_downloads_daily IS 'Descargas diarias ingeridas de App Store Connect y Google Play Console (spec 2.8).';

CREATE TABLE IF NOT EXISTS admin.metrics_daily_users (
  tenant_id        uuid        NOT NULL REFERENCES admin.tenants(id),
  day              date        NOT NULL,
  signups          integer     NOT NULL DEFAULT 0 CHECK (signups >= 0),
  cancellations    integer     NOT NULL DEFAULT 0 CHECK (cancellations >= 0),
  churned_users    integer     NOT NULL DEFAULT 0 CHECK (churned_users >= 0),
  downloads        integer     NOT NULL DEFAULT 0 CHECK (downloads >= 0),
  active_users_eod integer     NOT NULL DEFAULT 0 CHECK (active_users_eod >= 0),
  paying_users_eod integer     NOT NULL DEFAULT 0 CHECK (paying_users_eod >= 0),
  mrr_amount       bigint      NOT NULL DEFAULT 0 CHECK (mrr_amount >= 0),
  is_final         boolean     NOT NULL DEFAULT false,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day)
);
COMMENT ON TABLE admin.metrics_daily_users IS 'Serie diaria de Uso comercial (spec 4). Job 03:00 consolida el día anterior (is_final); job horario refresca el día en curso.';
COMMENT ON COLUMN admin.metrics_daily_users.churned_users IS 'Bajas explícitas + inactivos según churn_definition (system_settings).';
COMMENT ON COLUMN admin.metrics_daily_users.mrr_amount IS 'MRR al cierre del día en la moneda del tenant (CLP en Chile).';

CREATE TABLE IF NOT EXISTS admin.metrics_hourly_users (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  ts_hour       timestamptz NOT NULL CHECK (date_trunc('hour', ts_hour) = ts_hour),
  signups       integer     NOT NULL DEFAULT 0 CHECK (signups >= 0),
  cancellations integer     NOT NULL DEFAULT 0 CHECK (cancellations >= 0),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, ts_hour)
);
COMMENT ON TABLE admin.metrics_hourly_users IS 'Buckets horarios para el periodo today (spec 4.2). Retención 7 días.';

CREATE TABLE IF NOT EXISTS admin.metrics_plan_snapshot (
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  as_of         date         NOT NULL,
  plan_code     varchar(12)  NOT NULL REFERENCES admin.plans(code),
  users         integer      NOT NULL CHECK (users >= 0),
  price_amount  integer      CHECK (price_amount >= 0),
  mrr_amount    bigint       NOT NULL DEFAULT 0 CHECK (mrr_amount >= 0),
  monthly_churn numeric(6,5) NOT NULL DEFAULT 0 CHECK (monthly_churn BETWEEN 0 AND 1),
  computed_at   timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, as_of, plan_code)
);
COMMENT ON TABLE admin.metrics_plan_snapshot IS 'Mezcla de planes (spec 4.3). Snapshot diario; price_amount congela el precio vigente.';

CREATE TABLE IF NOT EXISTS admin.metrics_funnel_snapshot (
  tenant_id       uuid        NOT NULL REFERENCES admin.tenants(id),
  as_of           date        NOT NULL,
  downloads_total bigint      NOT NULL CHECK (downloads_total >= 0),
  accounts_total  bigint      NOT NULL CHECK (accounts_total >= 0),
  active_30d      integer     NOT NULL CHECK (active_30d >= 0),
  paying          integer     NOT NULL CHECK (paying >= 0),
  computed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, as_of)
);
COMMENT ON TABLE admin.metrics_funnel_snapshot IS 'Embudo acumulado a la fecha (spec 4.4).';

-- ---------------------------------------------------------------------
-- 6 · Perfiles y adopción de funcionalidades
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.role_activity_window (
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  days_window         smallint     NOT NULL CHECK (days_window BETWEEN 7 AND 90),
  app_role_code       varchar(12)  NOT NULL REFERENCES admin.app_roles(code),
  active_users        integer      NOT NULL CHECK (active_users >= 0),
  growth_8w           numeric(7,4) NOT NULL DEFAULT 0,
  sessions_per_week   numeric(7,2) NOT NULL DEFAULT 0 CHECK (sessions_per_week >= 0),
  avg_session_seconds integer      NOT NULL DEFAULT 0 CHECK (avg_session_seconds >= 0),
  retention_30d       numeric(6,5) NOT NULL DEFAULT 0 CHECK (retention_30d BETWEEN 0 AND 1),
  computed_at         timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, days_window, app_role_code)
);
COMMENT ON TABLE admin.role_activity_window IS 'Intensidad de uso por perfil (spec 6.1). Ventanas precalculadas 7/14/30/60/90 días; la API elige la ventana ≥ days pedida.';

CREATE TABLE IF NOT EXISTS admin.role_weekly_active (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  week_start    date        NOT NULL CHECK (extract(isodow FROM week_start) = 1),
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  active_users  integer     NOT NULL CHECK (active_users >= 0),
  is_partial    boolean     NOT NULL DEFAULT false,
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, week_start, app_role_code)
);
COMMENT ON TABLE admin.role_weekly_active IS 'Activos semanales por perfil (spec 6.2). week_start = lunes ISO.';

CREATE TABLE IF NOT EXISTS admin.feature_usage_events (
  event_id      uuid        NOT NULL DEFAULT gen_random_uuid(),
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  occurred_at   timestamptz NOT NULL,
  user_id       uuid        NOT NULL,
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key),
  session_id    uuid,
  platform      varchar(10) CHECK (platform IN ('ios','android','web')),
  app_version   varchar(20),
  PRIMARY KEY (event_id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX IF NOT EXISTS feature_usage_events_feature_idx ON admin.feature_usage_events (tenant_id, occurred_at, feature_key);
CREATE INDEX IF NOT EXISTS feature_usage_events_user_idx    ON admin.feature_usage_events (tenant_id, user_id, occurred_at);
COMMENT ON TABLE admin.feature_usage_events IS 'Eventos crudos de uso emitidos por la app (spec 7). Particionada por mes; retención 13 meses. Solo INSERT.';
COMMENT ON COLUMN admin.feature_usage_events.user_id IS 'Referencia lógica a app.users.id (sin FK: otra BD/esquema).';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.feature_usage_events
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

CREATE TABLE IF NOT EXISTS admin.feature_usage_daily (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  day           date        NOT NULL,
  feature_key   varchar(50) NOT NULL REFERENCES admin.features(key),
  app_role_code varchar(12) NOT NULL REFERENCES admin.app_roles(code),
  unique_users  integer     NOT NULL CHECK (unique_users >= 0),
  events        integer     NOT NULL CHECK (events >= unique_users),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_usage_daily IS 'Usuarios únicos por función, rol y día (spec 2.8), derivada de feature_usage_events.';

CREATE TABLE IF NOT EXISTS admin.feature_usage_window (
  tenant_id         uuid         NOT NULL REFERENCES admin.tenants(id),
  days_window       smallint     NOT NULL CHECK (days_window IN (7,30,90)),
  feature_key       varchar(50)  NOT NULL REFERENCES admin.features(key),
  app_role_code     varchar(12)  NOT NULL REFERENCES admin.app_roles(code),
  users             integer      NOT NULL CHECK (users >= 0),
  role_active_users integer      NOT NULL CHECK (role_active_users >= 0),
  adoption          numeric(6,5) GENERATED ALWAYS AS (
                      CASE WHEN role_active_users > 0
                           THEN LEAST(users::numeric / role_active_users, 1) ELSE 0 END) STORED,
  computed_at       timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, days_window, feature_key, app_role_code),
  FOREIGN KEY (feature_key, app_role_code) REFERENCES admin.feature_roles(feature_key, app_role_code)
);
COMMENT ON TABLE admin.feature_usage_window IS 'Matriz de adopción (spec 7). Solo pares (función, rol) de feature_roles; ausencia de fila = null en la API.';
COMMENT ON COLUMN admin.feature_usage_window.adoption IS 'Generada: users / role_active_users (0–1).';

-- ---------------------------------------------------------------------
-- 7 · Estado operativo
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.ops_component_checks (
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30)  NOT NULL REFERENCES admin.components(key),
  checked_at    timestamptz  NOT NULL,
  ok            boolean      NOT NULL,
  latency_ms    integer      CHECK (latency_ms >= 0),
  error_rate    numeric(6,5) CHECK (error_rate BETWEEN 0 AND 1),
  detail        varchar(300),
  PRIMARY KEY (tenant_id, component_key, checked_at)
) PARTITION BY RANGE (checked_at);
COMMENT ON TABLE admin.ops_component_checks IS 'Health checks cada 60 s (spec 2.8). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_request_stats (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  minute        timestamptz NOT NULL CHECK (date_trunc('minute', minute) = minute),
  p50_ms        integer     NOT NULL CHECK (p50_ms >= 0),
  p95_ms        integer     NOT NULL CHECK (p95_ms >= p50_ms),
  p99_ms        integer     CHECK (p99_ms >= p95_ms),
  sample_count  integer     NOT NULL CHECK (sample_count > 0),
  error_count   integer     NOT NULL DEFAULT 0 CHECK (error_count BETWEEN 0 AND sample_count),
  PRIMARY KEY (tenant_id, component_key, minute)
) PARTITION BY RANGE (minute);
COMMENT ON TABLE admin.ops_request_stats IS 'Percentiles de latencia por componente y minuto desde OpenTelemetry (spec 2.8). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_component_state (
  tenant_id            uuid         NOT NULL REFERENCES admin.tenants(id),
  component_key        varchar(30)  NOT NULL REFERENCES admin.components(key),
  status               varchar(15)  NOT NULL CHECK (status IN ('operational','degraded','outage')),
  status_since         timestamptz  NOT NULL,
  consecutive_failures smallint     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  consecutive_breaches smallint     NOT NULL DEFAULT 0 CHECK (consecutive_breaches >= 0),
  uptime_30d           numeric(6,5) NOT NULL DEFAULT 1 CHECK (uptime_30d BETWEEN 0 AND 1),
  latency_p50_ms       integer      CHECK (latency_p50_ms >= 0),
  latency_p95_ms       integer      CHECK (latency_p95_ms >= 0),
  note                 varchar(200),
  checked_at           timestamptz  NOT NULL,
  PRIMARY KEY (tenant_id, component_key)
);
COMMENT ON TABLE admin.ops_component_state IS 'Última foto de cada componente mantenida por el monitor (spec 5.1). degraded tras 3 ciclos sobre umbral; outage tras 3 fallos.';

CREATE TABLE IF NOT EXISTS admin.ops_incidents (
  id             uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid         NOT NULL REFERENCES admin.tenants(id),
  title          varchar(160) NOT NULL CHECK (length(title) BETWEEN 5 AND 160),
  component_key  varchar(30)  REFERENCES admin.components(key),
  severity       varchar(15)  NOT NULL CHECK (severity IN ('degraded','outage')),
  status         varchar(15)  NOT NULL CHECK (status IN ('investigating','observing','resolved','completed')),
  is_maintenance boolean      NOT NULL DEFAULT false,
  auto_created   boolean      NOT NULL DEFAULT false,
  description    text         NOT NULL CHECK (length(description) <= 4000),
  resolution     text         CHECK (length(resolution) <= 2000),
  started_at     timestamptz  NOT NULL,
  resolved_at    timestamptz,
  created_by     uuid         REFERENCES admin.admin_users(id),
  updated_by     uuid         REFERENCES admin.admin_users(id),
  created_at     timestamptz  NOT NULL DEFAULT now(),
  updated_at     timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT ops_incidents_resolution_ck CHECK (status NOT IN ('resolved','completed') OR (resolution IS NOT NULL AND resolved_at IS NOT NULL)),
  CONSTRAINT ops_incidents_completed_ck  CHECK (status <> 'completed' OR is_maintenance),
  CONSTRAINT ops_incidents_creator_ck    CHECK (auto_created OR created_by IS NOT NULL),
  CONSTRAINT ops_incidents_dates_ck      CHECK (resolved_at IS NULL OR resolved_at >= started_at)
);
CREATE INDEX IF NOT EXISTS ops_incidents_tenant_started_idx ON admin.ops_incidents (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ops_incidents_tenant_status_idx  ON admin.ops_incidents (tenant_id, status);
CREATE INDEX IF NOT EXISTS ops_incidents_component_idx      ON admin.ops_incidents (tenant_id, component_key);
COMMENT ON TABLE admin.ops_incidents IS 'Incidentes y mantenimientos (spec 5.4–5.6). Historial en ops_incidents_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.ops_incidents
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE OR REPLACE FUNCTION admin.tg_incident_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> OLD.status AND NOT EXISTS (
       SELECT 1 FROM admin.incident_status_transitions t
        WHERE t.from_status = OLD.status AND t.to_status = NEW.status AND t.is_maintenance = NEW.is_maintenance) THEN
    RAISE EXCEPTION 'INVALID_TRANSITION: incidente % → % no permitido', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IN ('resolved','completed') THEN
    NEW.resolved_at := COALESCE(NEW.resolved_at, now());
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_transition BEFORE UPDATE OF status ON admin.ops_incidents
  FOR EACH ROW EXECUTE FUNCTION admin.tg_incident_transition();

CREATE TABLE IF NOT EXISTS admin.ops_component_status_history (
  id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  from_status   varchar(15) CHECK (from_status IN ('operational','degraded','outage')),
  to_status     varchar(15) NOT NULL CHECK (to_status IN ('operational','degraded','outage')),
  changed_at    timestamptz NOT NULL DEFAULT now(),
  reason        varchar(300),
  incident_id   uuid        REFERENCES admin.ops_incidents(id)
);
CREATE INDEX IF NOT EXISTS ops_component_status_history_idx ON admin.ops_component_status_history (tenant_id, component_key, changed_at DESC);
COMMENT ON TABLE admin.ops_component_status_history IS 'Transiciones de estado por componente: base del uptime histórico y de los incidentes abiertos automáticamente.';

CREATE TABLE IF NOT EXISTS admin.ops_latency_window (
  tenant_id     uuid        NOT NULL REFERENCES admin.tenants(id),
  "window"      varchar(4)  NOT NULL CHECK ("window" IN ('1h','24h','7d')),
  component_key varchar(30) NOT NULL REFERENCES admin.components(key),
  p50_ms        integer     NOT NULL CHECK (p50_ms >= 0),
  p95_ms        integer     NOT NULL CHECK (p95_ms >= p50_ms),
  sample_count  integer     NOT NULL CHECK (sample_count >= 0),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, "window", component_key)
);
COMMENT ON TABLE admin.ops_latency_window IS 'Percentiles agregados por ventana 1h/24h/7d desde ops_request_stats (spec 5.2). Refresco por minuto.';

CREATE TABLE IF NOT EXISTS admin.ops_critical_process_runs (
  tenant_id        uuid         NOT NULL REFERENCES admin.tenants(id),
  process_key      varchar(40)  NOT NULL REFERENCES admin.critical_processes(key),
  started_at       timestamptz  NOT NULL,
  duration_seconds numeric(9,3) NOT NULL CHECK (duration_seconds >= 0),
  succeeded        boolean      NOT NULL,
  is_synthetic     boolean      NOT NULL DEFAULT false,
  trace_id         varchar(64)
) PARTITION BY RANGE (started_at);
CREATE INDEX IF NOT EXISTS ops_critical_process_runs_idx ON admin.ops_critical_process_runs (tenant_id, process_key, started_at DESC);
COMMENT ON TABLE admin.ops_critical_process_runs IS 'Ejecuciones reales y sintéticas de procesos críticos (spec 5.3). Particionada por día; retención 90 días.';

CREATE TABLE IF NOT EXISTS admin.ops_critical_process_state (
  tenant_id          uuid         NOT NULL REFERENCES admin.tenants(id),
  process_key        varchar(40)  NOT NULL REFERENCES admin.critical_processes(key),
  p95_seconds        numeric(9,3) NOT NULL CHECK (p95_seconds >= 0),
  success_24h        numeric(6,5) NOT NULL CHECK (success_24h BETWEEN 0 AND 1),
  status             varchar(15)  NOT NULL CHECK (status IN ('operational','degraded','outage')),
  based_on_synthetic boolean      NOT NULL DEFAULT false,
  last_run_at        timestamptz,
  computed_at        timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, process_key)
);
COMMENT ON TABLE admin.ops_critical_process_state IS 'Estado calculado de cada proceso crítico (spec 5.3).';

-- ---------------------------------------------------------------------
-- 8 · Soporte
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.support_tickets (
  id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  number              bigint       NOT NULL,
  subject             varchar(160) NOT NULL CHECK (length(subject) BETWEEN 5 AND 160),
  description         text         NOT NULL CHECK (length(description) <= 8000),
  requester_user_id   uuid,
  requester_name      varchar(120) NOT NULL,
  requester_email     citext       NOT NULL,
  requester_role_code varchar(12)  REFERENCES admin.app_roles(code),
  requester_plan_code varchar(12)  REFERENCES admin.plans(code),
  requester_context   jsonb,
  category_code       varchar(20)  NOT NULL REFERENCES admin.ticket_categories(code),
  priority            varchar(10)  NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
  status              varchar(15)  NOT NULL DEFAULT 'open'   CHECK (status IN ('open','in_progress','waiting_user','resolved','closed')),
  channel             varchar(10)  NOT NULL DEFAULT 'app'    CHECK (channel IN ('app','email','phone','console')),
  assigned_to         uuid         REFERENCES admin.admin_users(id),
  first_response_at   timestamptz,
  resolved_at         timestamptz,
  closed_at           timestamptz,
  reopen_count        smallint     NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
  created_by          uuid         REFERENCES admin.admin_users(id),
  created_at          timestamptz  NOT NULL DEFAULT now(),
  updated_at          timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT support_tickets_number_uq   UNIQUE (tenant_id, number),
  CONSTRAINT support_tickets_resolved_ck CHECK (status NOT IN ('resolved','closed') OR resolved_at IS NOT NULL),
  CONSTRAINT support_tickets_closed_ck   CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
  CONSTRAINT support_tickets_console_ck  CHECK (channel <> 'console' OR created_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS support_tickets_status_idx    ON admin.support_tickets (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_assignee_idx  ON admin.support_tickets (tenant_id, assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS support_tickets_category_idx  ON admin.support_tickets (tenant_id, category_code, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_requester_idx ON admin.support_tickets (tenant_id, requester_user_id) WHERE requester_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS support_tickets_created_idx   ON admin.support_tickets (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_priority_idx  ON admin.support_tickets (tenant_id, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_search_idx    ON admin.support_tickets USING gin ((number::text || ' ' || subject || ' ' || requester_email::text) gin_trgm_ops);
COMMENT ON TABLE admin.support_tickets IS 'Cola de soporte (spec 8). number = correlativo visible por tenant (#1482). Historial en support_tickets_history.';
COMMENT ON COLUMN admin.support_tickets.requester_user_id IS 'Referencia lógica a app.users.id; NULL si el ticket se creó sin cuenta enlazada (confirm_unlinked).';
COMMENT ON COLUMN admin.support_tickets.requester_context IS 'Snapshot del contexto del solicitante: {patients:[{patient_id,display_name}], devices:[{platform,app_version,last_seen_at}]}.';
COMMENT ON COLUMN admin.support_tickets.first_response_at IS 'Fijado por trigger con la primera respuesta no interna de un admin; base del KPI de primera respuesta.';

CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Número correlativo por tenant
CREATE OR REPLACE FUNCTION admin.tg_ticket_number() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.number IS NULL THEN
    NEW.number := admin.next_counter(NEW.tenant_id, 'ticket_number');
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ticket_number BEFORE INSERT ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_number();

-- Máquina de estados + marcas temporales derivadas
CREATE OR REPLACE FUNCTION admin.tg_ticket_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> OLD.status THEN
    IF NOT EXISTS (SELECT 1 FROM admin.ticket_status_transitions t
                    WHERE t.from_status = OLD.status AND t.to_status = NEW.status) THEN
      RAISE EXCEPTION 'INVALID_TRANSITION: ticket % → % no permitido', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'resolved' THEN NEW.resolved_at := COALESCE(NEW.resolved_at, now()); END IF;
    IF NEW.status = 'closed'   THEN NEW.closed_at   := COALESCE(NEW.closed_at, now());   END IF;
    IF OLD.status = 'resolved' AND NEW.status = 'in_progress' THEN
      NEW.reopen_count := OLD.reopen_count + 1;
      NEW.resolved_at  := NULL;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_transition BEFORE UPDATE OF status ON admin.support_tickets
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_transition();

CREATE TABLE IF NOT EXISTS admin.support_ticket_replies (
  id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid         NOT NULL REFERENCES admin.tenants(id),
  ticket_id       uuid         NOT NULL REFERENCES admin.support_tickets(id) ON DELETE CASCADE,
  author_type     varchar(10)  NOT NULL CHECK (author_type IN ('admin','user','system')),
  author_admin_id uuid         REFERENCES admin.admin_users(id),
  author_user_id  uuid,
  author_name     varchar(120) NOT NULL,
  body            text         NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
  is_internal     boolean      NOT NULL DEFAULT false,
  created_at      timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT support_ticket_replies_author_ck CHECK (
    (author_type = 'admin'  AND author_admin_id IS NOT NULL) OR
    (author_type = 'user'   AND author_user_id  IS NOT NULL) OR
    (author_type = 'system')),
  CONSTRAINT support_ticket_replies_internal_ck CHECK (NOT is_internal OR author_type = 'admin')
);
CREATE INDEX IF NOT EXISTS support_ticket_replies_ticket_idx ON admin.support_ticket_replies (ticket_id, created_at);
COMMENT ON TABLE admin.support_ticket_replies IS 'Conversación del ticket (spec 8.5/8.6): respuestas de admin, mensajes del usuario y del sistema. Inmutable.';
CREATE OR REPLACE TRIGGER trg_immutable BEFORE UPDATE OR DELETE ON admin.support_ticket_replies
  FOR EACH ROW EXECUTE FUNCTION admin.tg_forbid_change();

-- Primera respuesta pública de un admin fija first_response_at; no se responde a tickets cerrados
CREATE OR REPLACE FUNCTION admin.tg_ticket_reply_after_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM admin.support_tickets WHERE id = NEW.ticket_id FOR UPDATE;
  IF v_status = 'closed' THEN
    RAISE EXCEPTION 'TICKET_CLOSED: el ticket está cerrado y no admite nuevas respuestas' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.author_type = 'admin' AND NOT NEW.is_internal THEN
    UPDATE admin.support_tickets SET first_response_at = NEW.created_at
     WHERE id = NEW.ticket_id AND first_response_at IS NULL;
  END IF;
  RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER trg_reply_after_insert AFTER INSERT ON admin.support_ticket_replies
  FOR EACH ROW EXECUTE FUNCTION admin.tg_ticket_reply_after_insert();

CREATE TABLE IF NOT EXISTS admin.support_csat_surveys (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES admin.tenants(id),
  ticket_id    uuid        NOT NULL UNIQUE REFERENCES admin.support_tickets(id) ON DELETE CASCADE,
  sent_at      timestamptz NOT NULL DEFAULT now(),
  responded_at timestamptz,
  score        smallint    CHECK (score BETWEEN 1 AND 5),
  comment      text,
  CONSTRAINT support_csat_surveys_response_ck CHECK ((responded_at IS NULL) = (score IS NULL))
);
CREATE INDEX IF NOT EXISTS support_csat_surveys_responded_idx ON admin.support_csat_surveys (tenant_id, responded_at) WHERE responded_at IS NOT NULL;
COMMENT ON TABLE admin.support_csat_surveys IS 'Encuesta CSAT enviada al resolver (spec 8.4). Una por ticket.';

CREATE TABLE IF NOT EXISTS admin.support_metrics_daily (
  tenant_id                  uuid        NOT NULL REFERENCES admin.tenants(id),
  day                        date        NOT NULL,
  tickets_created            integer     NOT NULL DEFAULT 0 CHECK (tickets_created >= 0),
  tickets_resolved           integer     NOT NULL DEFAULT 0 CHECK (tickets_resolved >= 0),
  tickets_closed             integer     NOT NULL DEFAULT 0 CHECK (tickets_closed >= 0),
  first_response_seconds_avg integer     CHECK (first_response_seconds_avg >= 0),
  resolution_seconds_avg     integer     CHECK (resolution_seconds_avg >= 0),
  csat_sum                   integer     NOT NULL DEFAULT 0 CHECK (csat_sum >= 0),
  csat_count                 integer     NOT NULL DEFAULT 0 CHECK (csat_count >= 0),
  computed_at                timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day)
);
COMMENT ON TABLE admin.support_metrics_daily IS 'Agregado diario de soporte (spec 2.8 / 6.3). Los contadores por estado se leen en vivo de support_tickets.';

-- ---------------------------------------------------------------------
-- 9 · Curación de contenido y marketplace
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.content_items (
  id            uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid         NOT NULL REFERENCES admin.tenants(id),
  type          varchar(10)  NOT NULL CHECK (type IN ('joke','news')),
  title         varchar(160) NOT NULL CHECK (length(title) BETWEEN 3 AND 160),
  body          text         NOT NULL CHECK (length(body) BETWEEN 10 AND 4000),
  tags          text[]       NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 10),
  status        varchar(12)  NOT NULL DEFAULT 'draft'   CHECK (status IN ('draft','published','archived')),
  tts_status    varchar(10)  NOT NULL DEFAULT 'pending' CHECK (tts_status IN ('pending','ready','failed')),
  tts_audio_url varchar(500),
  publish_at    timestamptz,
  published_at  timestamptz,
  archived_at   timestamptz,
  deleted_at    timestamptz,
  created_by    uuid         NOT NULL REFERENCES admin.admin_users(id),
  updated_by    uuid         REFERENCES admin.admin_users(id),
  created_at    timestamptz  NOT NULL DEFAULT now(),
  updated_at    timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT content_items_published_ck CHECK (status <> 'published' OR (published_at IS NOT NULL AND tts_status = 'ready')),
  CONSTRAINT content_items_archived_ck  CHECK (status <> 'archived'  OR archived_at IS NOT NULL),
  CONSTRAINT content_items_deleted_ck   CHECK (deleted_at IS NULL OR status <> 'published')
);
CREATE INDEX IF NOT EXISTS content_items_type_status_idx ON admin.content_items (tenant_id, type, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS content_items_schedule_idx    ON admin.content_items (tenant_id, publish_at) WHERE status = 'draft' AND publish_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS content_items_tags_idx        ON admin.content_items USING gin (tags);
CREATE INDEX IF NOT EXISTS content_items_search_idx      ON admin.content_items USING gin ((title || ' ' || body) gin_trgm_ops);
COMMENT ON TABLE admin.content_items IS 'Chistes y noticias curados (spec 9). El feed de la app lee status = published AND deleted_at IS NULL. Borrado lógico. Historial en content_items_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.content_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE TABLE IF NOT EXISTS admin.marketplace_caregivers (
  caregiver_id                  uuid         PRIMARY KEY,
  tenant_id                     uuid         NOT NULL REFERENCES admin.tenants(id),
  display_name                  varchar(120) NOT NULL,
  email                         citext       NOT NULL,
  zone                          varchar(80)  NOT NULL,
  specialties                   text[]       NOT NULL DEFAULT '{}',
  languages                     text[]       NOT NULL DEFAULT '{}',
  certifications_count          smallint     NOT NULL DEFAULT 0 CHECK (certifications_count >= 0),
  certifications_verified_count smallint     NOT NULL DEFAULT 0 CHECK (certifications_verified_count BETWEEN 0 AND certifications_count),
  rating_avg                    numeric(3,2) CHECK (rating_avg BETWEEN 1 AND 5),
  reviews_count                 integer      NOT NULL DEFAULT 0 CHECK (reviews_count >= 0),
  status                        varchar(12)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','suspended')),
  status_reason                 varchar(1000) CHECK (status_reason IS NULL OR length(status_reason) >= 10),
  internal_note                 text         CHECK (length(internal_note) <= 2000),
  submitted_at                  timestamptz  NOT NULL,
  reviewed_by                   uuid         REFERENCES admin.admin_users(id),
  reviewed_at                   timestamptz,
  synced_at                     timestamptz  NOT NULL DEFAULT now(),
  updated_at                    timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT marketplace_caregivers_approved_ck  CHECK (status <> 'approved'  OR certifications_verified_count >= 1),
  CONSTRAINT marketplace_caregivers_suspended_ck CHECK (status <> 'suspended' OR status_reason IS NOT NULL),
  CONSTRAINT marketplace_caregivers_reviewed_ck  CHECK (status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_status_idx ON admin.marketplace_caregivers (tenant_id, status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_zone_idx   ON admin.marketplace_caregivers (tenant_id, zone);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_spec_idx   ON admin.marketplace_caregivers USING gin (specialties);
CREATE INDEX IF NOT EXISTS marketplace_caregivers_search_idx ON admin.marketplace_caregivers USING gin ((display_name || ' ' || email::text) gin_trgm_ops);
COMMENT ON TABLE admin.marketplace_caregivers IS 'Estado de publicación en la vitrina de cada perfil de cuidadora (spec 10.1/10.2). caregiver_id = app.caregivers.id (referencia lógica). Campos descriptivos sincronizados desde la app. Historial en marketplace_caregivers_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.marketplace_caregivers
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

CREATE TABLE IF NOT EXISTS admin.marketplace_products (
  id           uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid         NOT NULL REFERENCES admin.tenants(id),
  name         varchar(160) NOT NULL CHECK (length(name) BETWEEN 3 AND 160),
  category     varchar(60)  NOT NULL CHECK (length(category) BETWEEN 2 AND 60),
  vendor       varchar(120) NOT NULL CHECK (length(vendor) BETWEEN 2 AND 120),
  price_amount integer      CHECK (price_amount >= 0),
  external_url varchar(500) NOT NULL CHECK (external_url ~* '^https://'),
  image_url    varchar(500) CHECK (image_url IS NULL OR image_url ~* '^https?://'),
  status       varchar(12)  NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  published_at timestamptz,
  archived_at  timestamptz,
  created_by   uuid         NOT NULL REFERENCES admin.admin_users(id),
  updated_by   uuid         REFERENCES admin.admin_users(id),
  created_at   timestamptz  NOT NULL DEFAULT now(),
  updated_at   timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT marketplace_products_published_ck CHECK (status <> 'published' OR published_at IS NOT NULL),
  CONSTRAINT marketplace_products_archived_ck  CHECK (status <> 'archived'  OR archived_at  IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS marketplace_products_status_idx ON admin.marketplace_products (tenant_id, status, category);
CREATE INDEX IF NOT EXISTS marketplace_products_search_idx ON admin.marketplace_products USING gin ((name || ' ' || vendor) gin_trgm_ops);
COMMENT ON TABLE admin.marketplace_products IS 'Catálogo de artículos de apoyo sin transacciones en v1 (spec 10.3–10.5). Historial en marketplace_products_history.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.marketplace_products
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- ---------------------------------------------------------------------
-- 10 · Moderación
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.moderation_items (
  id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid         NOT NULL REFERENCES admin.tenants(id),
  item_type           varchar(20)  NOT NULL CHECK (item_type IN ('review','caregiver_profile','photo','chat_message')),
  source_entity_id    uuid         NOT NULL,
  content_snapshot    jsonb        NOT NULL,
  author_user_id      uuid         NOT NULL,
  author_name         varchar(120) NOT NULL,
  author_role_code    varchar(12)  REFERENCES admin.app_roles(code),
  reported_by_user_id uuid,
  reported_by_name    varchar(120),
  report_reason       varchar(300),
  is_safety_report    boolean      NOT NULL DEFAULT false,
  status              varchar(10)  NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reject_reason_code  varchar(15)  CHECK (reject_reason_code IN ('offensive','spam','privacy','off_topic','other')),
  decision_note       varchar(1000),
  decided_by          uuid         REFERENCES admin.admin_users(id),
  decided_at          timestamptz,
  created_at          timestamptz  NOT NULL DEFAULT now(),
  updated_at          timestamptz  NOT NULL DEFAULT now(),
  CONSTRAINT moderation_items_source_uq    UNIQUE (tenant_id, item_type, source_entity_id),
  CONSTRAINT moderation_items_decided_ck   CHECK ((status = 'pending') = (decided_by IS NULL AND decided_at IS NULL)),
  CONSTRAINT moderation_items_reject_ck    CHECK ((status = 'rejected') = (reject_reason_code IS NOT NULL)),
  CONSTRAINT moderation_items_other_ck     CHECK (reject_reason_code IS DISTINCT FROM 'other' OR decision_note IS NOT NULL),
  CONSTRAINT moderation_items_report_ck    CHECK (reported_by_user_id IS NULL OR report_reason IS NOT NULL),
  CONSTRAINT moderation_items_safety_ck    CHECK (NOT is_safety_report OR reported_by_user_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS moderation_items_queue_idx  ON admin.moderation_items (tenant_id, is_safety_report DESC, created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS moderation_items_status_idx ON admin.moderation_items (tenant_id, status, decided_at DESC);
CREATE INDEX IF NOT EXISTS moderation_items_author_idx ON admin.moderation_items (tenant_id, author_user_id, decided_at) WHERE status = 'rejected';
COMMENT ON TABLE admin.moderation_items IS 'Cola única de moderación (spec 11): reseñas (previa) y contenido reportado. content_snapshot nunca guarda el binario ni URLs firmadas. Historial en moderation_items_history.';
COMMENT ON COLUMN admin.moderation_items.source_entity_id IS 'Referencia lógica al elemento de la app según item_type (app.reviews, app.photos, app.chat_messages, app.caregivers).';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.moderation_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Una decisión es definitiva (ALREADY_MODERATED)
CREATE OR REPLACE FUNCTION admin.tg_moderation_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'pending' AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'ALREADY_MODERATED: el elemento ya fue moderado' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_moderation_once BEFORE UPDATE OF status ON admin.moderation_items
  FOR EACH ROW EXECUTE FUNCTION admin.tg_moderation_once();

CREATE TABLE IF NOT EXISTS admin.moderation_escalations (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid        NOT NULL REFERENCES admin.tenants(id),
  author_user_id   uuid        NOT NULL,
  rejections_count smallint    NOT NULL CHECK (rejections_count >= 1),
  window_days      smallint    NOT NULL DEFAULT 90,
  triggered_at     timestamptz NOT NULL DEFAULT now(),
  is_open          boolean     NOT NULL DEFAULT true,
  reviewed_by      uuid        REFERENCES admin.admin_users(id),
  reviewed_at      timestamptz,
  review_note      text,
  CONSTRAINT moderation_escalations_review_ck CHECK (is_open OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS moderation_escalations_open_idx ON admin.moderation_escalations (tenant_id, is_open, triggered_at DESC);
COMMENT ON TABLE admin.moderation_escalations IS 'Casos escalados al admin por el job de reglas (3 rechazos ofensivos del mismo autor en 90 días, spec 11.3).';

-- ---------------------------------------------------------------------
-- 11 · Configuración y legales
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.system_settings (
  tenant_id   uuid         NOT NULL REFERENCES admin.tenants(id),
  key         varchar(80)  NOT NULL REFERENCES admin.setting_definitions(key),
  value       jsonb        NOT NULL,
  version     integer      NOT NULL DEFAULT 1 CHECK (version >= 1),
  change_note varchar(500) CHECK (change_note IS NULL OR length(change_note) >= 5),
  updated_by  uuid         REFERENCES admin.admin_users(id),
  updated_at  timestamptz,
  PRIMARY KEY (tenant_id, key)
);
COMMENT ON TABLE admin.system_settings IS 'Valor vigente de cada parámetro por tenant (spec 12). version = bloqueo optimista; cada cambio queda en system_settings_history.';

-- La versión avanza de uno en uno y el valor se valida en la API contra value_schema
CREATE OR REPLACE FUNCTION admin.tg_settings_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.value IS DISTINCT FROM OLD.value THEN
    IF NEW.version <> OLD.version + 1 THEN
      RAISE EXCEPTION 'VERSION_CONFLICT: versión esperada %, recibida %', OLD.version + 1, NEW.version
        USING ERRCODE = 'serialization_failure';
    END IF;
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_settings_version BEFORE UPDATE ON admin.system_settings
  FOR EACH ROW EXECUTE FUNCTION admin.tg_settings_version();

CREATE TABLE IF NOT EXISTS admin.legal_versions (
  id                    uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid          NOT NULL REFERENCES admin.tenants(id),
  doc_type              varchar(10)   NOT NULL CHECK (doc_type IN ('terms','privacy')),
  semver_major          smallint      NOT NULL CHECK (semver_major >= 0),
  semver_minor          smallint      NOT NULL CHECK (semver_minor >= 0),
  semver                varchar(10)   GENERATED ALWAYS AS (semver_major || '.' || semver_minor) STORED,
  content_md            text          NOT NULL CHECK (length(content_md) >= 100),
  changelog             varchar(2000) NOT NULL CHECK (length(changelog) >= 10),
  effective_date        date          NOT NULL,
  requires_reacceptance boolean       NOT NULL,
  status                varchar(10)   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','superseded')),
  published_by          uuid          REFERENCES admin.admin_users(id),
  published_at          timestamptz,
  created_by            uuid          NOT NULL REFERENCES admin.admin_users(id),
  created_at            timestamptz   NOT NULL DEFAULT now(),
  updated_at            timestamptz   NOT NULL DEFAULT now(),
  CONSTRAINT legal_versions_semver_uq    UNIQUE (tenant_id, doc_type, semver_major, semver_minor),
  CONSTRAINT legal_versions_published_ck CHECK (status = 'draft' OR (published_by IS NOT NULL AND published_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS legal_versions_current_idx ON admin.legal_versions (tenant_id, doc_type, effective_date DESC) WHERE status = 'published';
COMMENT ON TABLE admin.legal_versions IS 'Términos y política de privacidad versionados (spec 13). Nunca se borran: app.legal_acceptances referencia legal_versions.id. Historial en legal_versions_history.';
COMMENT ON COLUMN admin.legal_versions.status IS 'draft → published (vigente desde effective_date) → superseded cuando otra versión posterior entra en vigor.';
CREATE OR REPLACE TRIGGER trg_updated_at BEFORE UPDATE ON admin.legal_versions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at();

-- Solo los borradores se editan; lo publicado únicamente puede pasar a superseded
CREATE OR REPLACE FUNCTION admin.tg_legal_versions_lock() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    IF NEW.content_md <> OLD.content_md OR NEW.changelog <> OLD.changelog
       OR NEW.effective_date <> OLD.effective_date OR NEW.semver_major <> OLD.semver_major
       OR NEW.semver_minor <> OLD.semver_minor OR NEW.requires_reacceptance <> OLD.requires_reacceptance THEN
      RAISE EXCEPTION 'Una versión legal publicada es inmutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'draft' THEN
      RAISE EXCEPTION 'Una versión publicada no puede volver a borrador' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_legal_lock BEFORE UPDATE ON admin.legal_versions
  FOR EACH ROW EXECUTE FUNCTION admin.tg_legal_versions_lock();

-- ---------------------------------------------------------------------
-- 12 · Jobs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin.job_runs (
  id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid        REFERENCES admin.tenants(id),
  job_name      varchar(60) NOT NULL,
  status        varchar(10) NOT NULL CHECK (status IN ('running','succeeded','failed')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  watermark     timestamptz,
  rows_affected integer,
  error         text,
  CONSTRAINT job_runs_finished_ck CHECK ((status = 'running') = (finished_at IS NULL))
);
CREATE INDEX IF NOT EXISTS job_runs_name_idx   ON admin.job_runs (job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS job_runs_tenant_idx ON admin.job_runs (tenant_id, job_name, status);
COMMENT ON TABLE admin.job_runs IS 'Bitácora de jobs de agregación, monitor y mantenimiento: frescura de los datos y alerta de conectores caídos (spec 4.4).';

-- ---------------------------------------------------------------------
-- 13 · Historial (*_history) de las entidades mutables
-- ---------------------------------------------------------------------
CALL admin.enable_history('admin_users');
CALL admin.enable_history('ops_incidents');
CALL admin.enable_history('support_tickets');
CALL admin.enable_history('content_items');
CALL admin.enable_history('marketplace_caregivers');
CALL admin.enable_history('marketplace_products');
CALL admin.enable_history('moderation_items');
CALL admin.enable_history('system_settings');
CALL admin.enable_history('legal_versions');

-- ---------------------------------------------------------------------
-- 14 · Row-Level Security por tenant
--     La API ejecuta al inicio de cada transacción:
--       SET LOCAL app.tenant_id = '<uuid>'; SET LOCAL app.actor_id = '<uuid>';
--     Los jobs (agecare_admin_jobs, BYPASSRLS) ven todos los tenants.
--     El propietario del esquema (migraciones) queda exento (sin FORCE).
-- ---------------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
     WHERE n.nspname = 'admin' AND c.relkind IN ('r','p') AND NOT c.relispartition
  LOOP
    EXECUTE format('ALTER TABLE admin.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON admin.%I', r.relname);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON admin.%I
                     USING (tenant_id = admin.current_tenant_id())
                     WITH CHECK (tenant_id = admin.current_tenant_id())$p$, r.relname);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 15 · Grants
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA admin TO agecare_admin_api, agecare_admin_jobs, agecare_admin_ro;

-- API: CRUD en tablas operativas, lectura en agregados y catálogos
GRANT SELECT ON ALL TABLES IN SCHEMA admin TO agecare_admin_api, agecare_admin_ro;
GRANT INSERT, UPDATE ON
  admin.tenant_counters, admin.admin_users, admin.admin_invitations, admin.admin_sessions,
  admin.ops_incidents, admin.support_tickets, admin.support_csat_surveys,
  admin.content_items, admin.marketplace_caregivers, admin.marketplace_products,
  admin.moderation_items, admin.moderation_escalations, admin.system_settings, admin.legal_versions
  TO agecare_admin_api;
GRANT INSERT ON admin.audit_log, admin.admin_login_attempts, admin.support_ticket_replies TO agecare_admin_api;
GRANT DELETE ON admin.admin_sessions, admin.admin_invitations TO agecare_admin_api;
-- Las tablas *_history reciben filas solo vía trigger (SECURITY DEFINER): sin grants de escritura.

-- Solo lectura: sin acceso a credenciales ni sesiones
REVOKE SELECT ON admin.admin_users, admin.admin_users_history, admin.admin_sessions, admin.admin_invitations,
                 admin.admin_login_attempts FROM agecare_admin_ro;
GRANT SELECT (id, tenant_id, full_name, email, role_code, is_active, last_login_at, created_at)
  ON admin.admin_users TO agecare_admin_ro;

-- Jobs: todo salvo borrar auditoría
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA admin TO agecare_admin_jobs;
REVOKE UPDATE, DELETE ON admin.audit_log FROM agecare_admin_jobs;
REVOKE DELETE ON admin.support_ticket_replies FROM agecare_admin_jobs;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA admin TO agecare_admin_api, agecare_admin_jobs, agecare_admin_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT SELECT ON TABLES TO agecare_admin_api, agecare_admin_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agecare_admin_jobs;

-- ---------------------------------------------------------------------
-- 16 · Particiones y retención
--     Job diario partition_maintenance: crea particiones futuras y elimina
--     las que exceden la retención. En Azure puede sustituirse por pg_partman.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE admin.ensure_partitions(p_table text, p_granularity text, p_ahead integer)
LANGUAGE plpgsql AS $$
DECLARE
  v_step  interval := CASE p_granularity WHEN 'day' THEN interval '1 day' WHEN 'month' THEN interval '1 month' END;
  v_start timestamptz := date_trunc(p_granularity, now());
  v_from  timestamptz; v_to timestamptz; v_name text;
BEGIN
  IF v_step IS NULL THEN RAISE EXCEPTION 'granularidad % no soportada', p_granularity; END IF;
  FOR i IN -1 .. p_ahead LOOP
    v_from := v_start + i * v_step;
    v_to   := v_from + v_step;
    v_name := p_table || '_p' || to_char(v_from, CASE p_granularity WHEN 'day' THEN 'YYYYMMDD' ELSE 'YYYYMM' END);
    EXECUTE format('CREATE TABLE IF NOT EXISTS admin.%I PARTITION OF admin.%I FOR VALUES FROM (%L) TO (%L)',
                   v_name, p_table, v_from, v_to);
  END LOOP;
END $$;

CREATE OR REPLACE PROCEDURE admin.drop_expired_partitions(p_table text, p_retention interval)
LANGUAGE plpgsql AS $$
DECLARE r record; v_upper timestamptz;
BEGIN
  FOR r IN
    SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound
      FROM pg_inherits i
      JOIN pg_class c ON c.oid = i.inhrelid
      JOIN pg_class p ON p.oid = i.inhparent
      JOIN pg_namespace n ON n.oid = p.relnamespace
     WHERE n.nspname = 'admin' AND p.relname = p_table
  LOOP
    -- límite superior: FOR VALUES FROM ('…') TO ('…')
    v_upper := (regexp_match(r.bound, $re$TO \('([^']+)'\)$re$))[1]::timestamptz;
    IF v_upper < now() - p_retention THEN
      EXECUTE format('DROP TABLE admin.%I', r.relname);
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE PROCEDURE admin.partition_maintenance()
LANGUAGE plpgsql AS $$
BEGIN
  CALL admin.ensure_partitions('audit_log',                 'month', 2);
  CALL admin.ensure_partitions('feature_usage_events',      'month', 2);
  CALL admin.ensure_partitions('ops_component_checks',      'day',   7);
  CALL admin.ensure_partitions('ops_request_stats',         'day',   7);
  CALL admin.ensure_partitions('ops_critical_process_runs', 'day',   7);
  CALL admin.drop_expired_partitions('audit_log',                 interval '24 months');
  CALL admin.drop_expired_partitions('feature_usage_events',      interval '13 months');
  CALL admin.drop_expired_partitions('ops_component_checks',      interval '90 days');
  CALL admin.drop_expired_partitions('ops_request_stats',         interval '90 days');
  CALL admin.drop_expired_partitions('ops_critical_process_runs', interval '90 days');
  -- Retención por DELETE en tablas pequeñas no particionadas
  DELETE FROM admin.metrics_hourly_users    WHERE ts_hour      < now() - interval '7 days';
  DELETE FROM admin.admin_login_attempts    WHERE attempted_at < now() - interval '90 days';
  DELETE FROM admin.admin_sessions          WHERE expires_at   < now() - interval '30 days';
  DELETE FROM admin.job_runs                WHERE started_at   < now() - interval '180 days';
END $$;
COMMENT ON PROCEDURE admin.partition_maintenance() IS 'Ejecutar a diario (job partition_maintenance): crea particiones futuras y aplica la política de retención.';

CALL admin.partition_maintenance();

-- ---------------------------------------------------------------------
-- 17 · Datos semilla de catálogos (idempotentes)
-- ---------------------------------------------------------------------
INSERT INTO admin.admin_roles (code, name, description, requires_mfa_default, sort_order) VALUES
  ('admin',     'Administrador', 'Acceso total a la consola, gestión de staff y auditoría', true,  1),
  ('analyst',   'Analista',      'Lectura de métricas, estado operativo y tickets',          false, 2),
  ('support',   'Soporte',       'Métricas, incidentes y gestión de tickets',                false, 3),
  ('editor',    'Editor',        'Curación de contenido y catálogos del marketplace',        false, 4),
  ('moderator', 'Moderador',     'Cola de moderación y lectura del marketplace',             false, 5)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
  requires_mfa_default = EXCLUDED.requires_mfa_default, sort_order = EXCLUDED.sort_order;

INSERT INTO admin.admin_role_permissions (role_code, module, access) VALUES
  ('admin','metrics','read'), ('admin','ops','write'), ('admin','support','write'), ('admin','content','write'),
  ('admin','marketplace','write'), ('admin','moderation','write'), ('admin','settings','write'),
  ('admin','legal','write'), ('admin','staff','write'), ('admin','audit','write'),
  ('analyst','metrics','read'), ('analyst','ops','read'), ('analyst','support','read'),
  ('support','metrics','read'), ('support','ops','write'), ('support','support','write'),
  ('editor','content','write'), ('editor','marketplace','write'),
  ('moderator','marketplace','read'), ('moderator','moderation','write')
ON CONFLICT DO NOTHING;

INSERT INTO admin.app_roles (code, name, sort_order) VALUES
  ('family','Familiar',1), ('caregiver','Cuidadora',2), ('elder','Adulto mayor',3), ('doctor','Médico',4)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

INSERT INTO admin.plans (code, name, billing_unit, is_paid, sort_order) VALUES
  ('free','Gratuito','none',false,1), ('gold','Dorado','user',true,2),
  ('platinum','Platino','family',true,3), ('provider','Proveedor','provider',true,4)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, billing_unit = EXCLUDED.billing_unit, is_paid = EXCLUDED.is_paid;

INSERT INTO admin.components (key, name, is_async, latency_note, sort_order) VALUES
  ('api_core','API central',false,NULL,1),
  ('database','Base de datos',false,NULL,2),
  ('auth','Autenticación',false,NULL,3),
  ('push','Notificaciones push',true,'Latencia de entrega extremo a extremo',4),
  ('alert_engine','Motor de alertas',false,NULL,5),
  ('wearable_ingest','Ingesta wearables',false,NULL,6),
  ('ai_assistant','Asistente IA',true,'Latencia de generación extremo a extremo',7),
  ('storage','Almacenamiento',false,NULL,8),
  ('music_sync','Sync Director Musical',false,NULL,9)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, is_async = EXCLUDED.is_async, latency_note = EXCLUDED.latency_note;

INSERT INTO admin.critical_processes (key, name, chain, sort_order) VALUES
  ('fall_alert','Alerta de caída','Wearable → ingesta → motor de alertas → push familiar',1),
  ('sos','Botón SOS','App → API → push a círculo de cuidado',2),
  ('missed_medication','Alerta de medicamento omitido','Ventana de toma → job programado → push',3),
  ('ai_query','Consulta al asistente IA','App → API → LLM → respuesta',4),
  ('music_sync','Sincronización Director Musical','Tablet → API → almacenamiento → confirmación',5)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, chain = EXCLUDED.chain;

INSERT INTO admin.ticket_categories (code, name, sort_order) VALUES
  ('account_access','Acceso y cuenta',1), ('wearable_sync','Wearable y sincronización',2),
  ('alerts_push','Alertas y push',3), ('billing_plans','Pagos y planes',4),
  ('medications','Medicamentos',5), ('other','Otros',6)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO admin.ticket_status_transitions (from_status, to_status) VALUES
  ('open','in_progress'), ('in_progress','waiting_user'), ('in_progress','resolved'),
  ('waiting_user','in_progress'), ('resolved','closed'), ('resolved','in_progress')
ON CONFLICT DO NOTHING;

INSERT INTO admin.incident_status_transitions (from_status, to_status, is_maintenance) VALUES
  ('investigating','observing',false), ('investigating','resolved',false), ('observing','resolved',false),
  ('observing','completed',true), ('investigating','observing',true)
ON CONFLICT DO NOTHING;

INSERT INTO admin.features (key, name, expected_low, note, sort_order) VALUES
  ('home_traffic_light','Inicio / semáforo',false,'La promesa central del producto',1),
  ('medications','Medicamentos',false,NULL,2),
  ('checkin','Check-in diario',false,NULL,3),
  ('photos','Fotos',false,NULL,4),
  ('entertainment','Entretenimiento curado',false,NULL,5),
  ('ai_assistant','Asistente IA',false,NULL,6),
  ('marketplace','Marketplace',false,'Vitrina Could de v1',7),
  ('premium_reports','Reportes premium',false,'Función de pago',8),
  ('sos','SOS',true,'Uso bajo por diseño: evento de emergencia',9),
  ('music_director','Director Musical',false,NULL,10)
ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, expected_low = EXCLUDED.expected_low, note = EXCLUDED.note;

INSERT INTO admin.feature_roles (feature_key, app_role_code) VALUES
  ('home_traffic_light','family'), ('home_traffic_light','caregiver'),
  ('medications','family'), ('medications','caregiver'), ('medications','elder'), ('medications','doctor'),
  ('checkin','caregiver'), ('checkin','elder'),
  ('photos','family'), ('photos','elder'),
  ('entertainment','elder'),
  ('ai_assistant','family'), ('ai_assistant','caregiver'), ('ai_assistant','doctor'),
  ('marketplace','family'), ('marketplace','caregiver'),
  ('premium_reports','family'),
  ('sos','elder'), ('sos','caregiver'),
  ('music_director','caregiver'), ('music_director','elder')
ON CONFLICT DO NOTHING;

INSERT INTO admin.setting_definitions (key, description, value_schema, default_value, category, is_secret) VALUES
  ('plan_prices', 'Precio mensual por plan en la moneda del tenant',
   '{"type":"object","properties":{"gold":{"type":"integer","minimum":0},"platinum":{"type":"integer","minimum":0},"provider":{"type":"integer","minimum":0}},"required":["gold","platinum","provider"],"additionalProperties":false}',
   '{"gold":1000,"platinum":10000,"provider":5000}', 'plans', false),
  ('churn_definition', 'Días de inactividad para considerar churn además de la baja explícita',
   '{"type":"object","properties":{"inactive_days":{"type":"integer","minimum":7,"maximum":365}},"required":["inactive_days"]}',
   '{"inactive_days":60}', 'plans', false),
  ('allowed_email_domains', 'Dominios permitidos para cuentas de staff',
   '{"type":"array","items":{"type":"string","pattern":"^[a-z0-9.-]+\\.[a-z]{2,}$"},"minItems":1}',
   '["wellq.co.uk"]', 'security', false),
  ('ops_thresholds.api_core', 'Umbrales de degradación de API central',
   '{"type":"object","properties":{"p95_ms":{"type":"integer"},"error_rate":{"type":"number","minimum":0,"maximum":1}},"required":["p95_ms","error_rate"]}',
   '{"p95_ms":800,"error_rate":0.02}', 'ops', false),
  ('feature_adoption_low_threshold', 'Umbral de adopción baja por defecto',
   '{"type":"number","minimum":0.01,"maximum":0.5}', '0.15', 'flags', false)
ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description, value_schema = EXCLUDED.value_schema,
  default_value = EXCLUDED.default_value, category = EXCLUDED.category;

-- Fin del DDL
