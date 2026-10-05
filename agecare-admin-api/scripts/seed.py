"""Seed de datos demo coherentes con el Wireframe de la Consola v1.

Uso:
    python -m scripts.seed          # crea tablas (si faltan) y siembra datos
Credenciales demo:
    admin@wellq.co.uk / Admin123!   (rol admin, sin MFA para facilitar la prueba)
    soporte@wellq.co.uk / Soporte123!
    analista@wellq.co.uk / Analista123!
"""
import asyncio
import random
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, select

from app import models
from app.billing import models as billing_models  # noqa: F401  (registra las tablas billing_*)
from app.database import Base, get_engine, get_session_factory
from app.enums import AppRole, ComponentKey, PlanCode
from app.security import hash_password
from scripts.seed_billing import seed_billing

random.seed(20260828)
TODAY = date(2026, 8, 28)
NOW = datetime.now(timezone.utc)

FEATURES = [
    # key, nombre, roles aplicables, expected_low, nota, orden
    ("home_status", "Inicio / semáforo", ["family", "caregiver", "doctor"], False, None, 1),
    ("alert_center", "Centro de alertas", ["family", "caregiver", "doctor"], False, None, 2),
    ("vitals", "Vitals del wearable", ["family", "caregiver", "doctor"], False, None, 3),
    ("medications", "Medicamentos y adherencia", ["family", "caregiver", "doctor"], False, None, 4),
    ("checkin", "Check-in diario", ["caregiver"], False, None, 5),
    ("logbook", "Bitácora y observaciones", ["family", "caregiver", "doctor"], False, None, 6),
    ("chat", "Chat de coordinación", ["family", "caregiver", "elder", "doctor"], False, None, 7),
    ("ai_assistant", "Asistente IA", ["family", "caregiver", "doctor"], False,
     "Evaluar resúmenes clínicos automáticos para médicos.", 8),
    ("photos", "Fotos compartidas", ["family", "caregiver", "elder"], False, None, 9),
    ("entertainment", "Entretenimiento", ["elder"], False, None, 10),
    ("music_director", "Director Musical", ["family", "elder"], False, None, 11),
    ("documents", "Documentos médicos", ["family", "caregiver", "doctor"], False, None, 12),
    ("premium_reports", "Reportes (premium)", ["family", "caregiver", "doctor"], False,
     "Función de pago poco descubierta; probar oferta contextual tras 30 días de uso.", 13),
    ("marketplace", "Marketplace", ["family", "caregiver"], False,
     "Vitrina Could de v1 con adopción marginal. Decidir: rediseñar el descubrimiento o posponer a fase 2.", 14),
    ("sos", "SOS", ["caregiver", "elder"], True,
     "Uso bajo por diseño: es un evento de emergencia, no una función de uso diario.", 15),
]

# Adopción (%) por rol [family, caregiver, elder, doctor] — igual que el wireframe
ADOPTION = {
    "home_status": [92, 88, None, 61], "alert_center": [84, 71, None, 33],
    "vitals": [77, 64, None, 58], "medications": [69, 91, None, 72],
    "checkin": [None, 86, None, None], "logbook": [48, 83, None, 39],
    "chat": [62, 55, 47, 12], "ai_assistant": [41, 18, None, 9],
    "photos": [58, 22, 66, None], "entertainment": [None, None, 54, None],
    "music_director": [21, None, 38, None], "documents": [34, 29, None, 44],
    "premium_reports": [9, 13, None, 7], "marketplace": [11, 6, None, None],
    "sos": [None, 4, 2, None],
}
ACTIVE_30D = {"family": 5310, "caregiver": 1470, "elder": 2640, "doctor": 420}
ROLE_ORDER = ["family", "caregiver", "elder", "doctor"]


async def seed() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session_factory()() as db:
        # limpiar (idempotente)
        for table in (models.AuditLog, models.AdminSession, models.TicketReply, models.Ticket,
                      models.FeatureUsageWindow, models.Feature, models.RoleWeeklyActive,
                      models.RoleActivityWindow, models.MetricsPlanSnapshot,
                      models.MetricsHourlyUsers, models.MetricsDailyUsers,
                      models.LatencyWindow, models.ComponentState, models.CriticalProcessState,
                      models.Incident, models.ContentItem, models.CaregiverProfile,
                      models.Product, models.ModerationItem, models.SystemSetting,
                      models.LegalVersion, models.AdminUser):
            await db.execute(delete(table))

        # ---- Staff ----
        admins = [
            models.AdminUser(full_name="Max K.", email="admin@wellq.co.uk", role="admin",
                             password_hash=hash_password("Admin123!"), is_active=True),
            models.AdminUser(full_name="Sofía Rojas", email="soporte@wellq.co.uk", role="support",
                             password_hash=hash_password("Soporte123!"), is_active=True),
            models.AdminUser(full_name="Diego Paredes", email="analista@wellq.co.uk", role="analyst",
                             password_hash=hash_password("Analista123!"), is_active=True),
            models.AdminUser(full_name="Carla Núñez", email="editora@wellq.co.uk", role="editor",
                             password_hash=hash_password("Editora123!"), is_active=True),
        ]
        db.add_all(admins)
        await db.flush()
        admin, soporte = admins[0], admins[1]

        # ---- Métricas diarias (últimos 14 meses) ----
        start = TODAY - timedelta(days=425)
        active = 700.0
        paying = 40.0
        d = start
        while d <= TODAY:
            month_idx = (d.year - start.year) * 12 + d.month - start.month
            base = 10 + month_idx * 4.6
            signups = max(0, int(random.gauss(base, base * .18)))
            cancels = max(0, int(signups * random.uniform(.09, .16)))
            active = active + signups - cancels
            paying = min(active * .096, paying + signups * .045)
            mrr = int(paying * 3680)  # ARPU de pago combinado aprox. CLP
            db.add(models.MetricsDailyUsers(day=d, signups=signups, cancellations=cancels,
                                            downloads=int(signups * 1.9),
                                            active_users_eod=int(active),
                                            paying_users_eod=int(paying), mrr_clp_eod=mrr))
            d += timedelta(days=1)

        # ---- Métricas horarias de hoy (buckets de 3 h) ----
        for h, (s, c) in enumerate(zip([2, 1, 3, 9, 14, 11, 13, 9], [0, 0, 1, 2, 2, 1, 2, 1])):
            db.add(models.MetricsHourlyUsers(
                ts_hour=datetime.combine(TODAY, time(h * 3), tzinfo=timezone.utc), signups=s, cancellations=c))

        # ---- Snapshot de planes ----
        for code, users, price, churn in [(PlanCode.free, 8900, None, .029),
                                          (PlanCode.gold, 610, 1000, .041),
                                          (PlanCode.platinum, 240, 10000, .022),
                                          (PlanCode.provider, 90, 5000, .018)]:
            db.add(models.MetricsPlanSnapshot(as_of=TODAY, plan_code=code, users=users,
                                              price_clp=price,
                                              mrr_clp=users * price if price else 0,
                                              monthly_churn=churn))

        # ---- Actividad por rol ----
        sessions = {"family": 9.4, "caregiver": 22.6, "elder": 11.8, "doctor": 2.1}
        seconds = {"family": 250, "caregiver": 460, "elder": 545, "doctor": 200}
        retention = {"family": .78, "caregiver": .91, "elder": .64, "doctor": .55}
        growth = {"family": .149, "caregiver": .089, "elder": .195, "doctor": .20}
        for window in (7, 30, 90):
            factor = {7: .62, 30: 1.0, 90: 1.22}[window]
            for role in ROLE_ORDER:
                db.add(models.RoleActivityWindow(days_window=window, role=role,
                                                 active_users=int(ACTIVE_30D[role] * factor),
                                                 growth_8w=growth[role],
                                                 sessions_per_week=sessions[role],
                                                 avg_session_seconds=seconds[role],
                                                 retention_30d=retention[role]))
        weekly = {"family": [4620, 4750, 4890, 4980, 5040, 5150, 5230, 5310],
                  "caregiver": [1350, 1370, 1390, 1410, 1430, 1450, 1460, 1470],
                  "elder": [2210, 2290, 2340, 2400, 2460, 2520, 2580, 2640],
                  "doctor": [350, 360, 375, 380, 395, 400, 410, 420]}
        monday = TODAY - timedelta(days=TODAY.weekday())
        for i in range(8):
            week = monday - timedelta(weeks=7 - i)
            for role in ROLE_ORDER:
                db.add(models.RoleWeeklyActive(week_start=week, role=role,
                                               active_users=weekly[role][i]))

        # ---- Funcionalidades y adopción ----
        for key, name, roles, low, note, order in FEATURES:
            db.add(models.Feature(feature_key=key, name=name, applicable_roles=roles,
                                  expected_low=low, note=note, sort_order=order))
        for window in (7, 30, 90):
            wf = {7: .82, 30: 1.0, 90: 1.08}[window]
            active_f = {7: .62, 30: 1.0, 90: 1.22}[window]
            for key, pcts in ADOPTION.items():
                for role, pct in zip(ROLE_ORDER, pcts):
                    if pct is None:
                        continue
                    users = int(ACTIVE_30D[role] * active_f * min(pct * wf, 100) / 100)
                    db.add(models.FeatureUsageWindow(days_window=window, feature_key=key,
                                                     role=role, users=users))

        # ---- Estado operativo ----
        comps = [
            (ComponentKey.api_core, "operational", .9998, 84, 240, None),
            (ComponentKey.database, "operational", .9999, 6, 31, None),
            (ComponentKey.auth, "operational", .9997, 110, 320, None),
            (ComponentKey.push, "operational", .9990, None, 1900, "entrega extremo a extremo"),
            (ComponentKey.alert_engine, "operational", .9995, 220, 850, None),
            (ComponentKey.wearable_ingest, "operational", .9996, 95, 410, None),
            (ComponentKey.ai_assistant, "degraded", .9780, 1800, 4200, "latencia elevada"),
            (ComponentKey.storage, "operational", .9999, 120, 480, None),
            (ComponentKey.music_sync, "operational", .9920, 140, 620, None),
        ]
        for key, st, up, p50, p95, note in comps:
            db.add(models.ComponentState(key=key, status=st, uptime_30d=up,
                                         latency_p50_ms=p50, latency_p95_ms=p95, note=note))
            for window, mult in (("1h", 1.0), ("24h", 1.08), ("7d", 1.15)):
                if p50 is None:
                    continue
                db.add(models.LatencyWindow(window=window, component_key=key,
                                            p50_ms=int(p50 * mult), p95_ms=int(p95 * mult),
                                            sample_count={"1h": 3600, "24h": 82000, "7d": 560000}[window]))
        for key, name, chain, p95s, ok, st in [
            ("fall_alert", "Alerta de caída", "Wearable → ingesta → motor de alertas → push familiar", 6.8, .997, "operational"),
            ("sos", "Botón SOS", "App → API → push a círculo de cuidado", 3.1, .999, "operational"),
            ("missed_dose", "Alerta de medicamento omitido", "Ventana de toma → job programado → push", 48.0, .995, "operational"),
            ("ai_query", "Consulta al asistente IA", "App → API → LLM → respuesta", 4.2, .978, "degraded"),
            ("music_sync", "Sincronización Director Musical", "Tablet → API → S3 → confirmación", 11.4, .992, "operational"),
        ]:
            db.add(models.CriticalProcessState(key=key, name=name, chain=chain,
                                               p95_seconds=p95s, success_24h=ok, status=st))
        db.add_all([
            models.Incident(title="Latencia elevada en el Asistente IA", component_key="ai_assistant",
                            severity="degraded", status="observing",
                            description="p95 de generación sobre 4 s por saturación del proveedor LLM. "
                                        "Mitigación: caché de respuestas frecuentes en evaluación.",
                            started_at=NOW - timedelta(days=2), created_by=admin.id),
            models.Incident(title="Notificaciones push retrasadas 23 min", component_key="push",
                            severity="degraded", status="resolved",
                            description="Cola de Notification Hubs saturada tras pico de alertas.",
                            resolution="Se amplió el plan y se añadió alarma de profundidad de cola.",
                            started_at=NOW - timedelta(days=9), resolved_at=NOW - timedelta(days=9, hours=-1),
                            created_by=soporte.id),
            models.Incident(title="Mantenimiento programado de base de datos", component_key="database",
                            severity="degraded", status="completed", is_maintenance=True,
                            description="Actualización menor de PostgreSQL Flexible sin corte de servicio.",
                            resolution="Completado en ventana 02:00–02:40 con réplica + failover.",
                            started_at=NOW - timedelta(days=21), resolved_at=NOW - timedelta(days=21, hours=-1),
                            created_by=admin.id),
        ])

        # ---- Tickets ----
        subjects = [
            ("Wearable no sincroniza desde ayer", "wearable_sync", "high", "in_progress", "family"),
            ("No llegan las alertas de medicamentos", "alerts_push", "critical", "in_progress", "caregiver"),
            ("Error al digitalizar receta (OCR)", "medications", "medium", "open", "caregiver"),
            ("Cambio de plan Dorado → Platino", "billing_plans", "medium", "resolved", "family"),
            ("El médico no puede editar el plan", "account_access", "medium", "resolved", "doctor"),
            ("Audio del Director Musical no sube", "other", "low", "resolved", "caregiver"),
        ]
        n = 1482
        for i, (subj, cat, prio, st, role) in enumerate(subjects):
            created = NOW - timedelta(days=i, hours=3)
            t = models.Ticket(number=n - i, subject=subj, description=f"Detalle del caso: {subj}.",
                              requester_name=f"Usuario Demo {i + 1}",
                              requester_email=f"usuario{i + 1}@demo.cl", requester_role=role,
                              requester_plan="gold" if i % 2 else "free",
                              category=cat, priority=prio, status=st, channel="app",
                              assigned_to=soporte.id if st != "open" else None,
                              created_at=created,
                              first_response_at=created + timedelta(hours=2) if st != "open" else None,
                              resolved_at=created + timedelta(hours=20) if st == "resolved" else None,
                              csat_score=5 if st == "resolved" and i % 2 else None,
                              requester_context={"patients": [{"display_name": "Paciente Demo"}],
                                                 "devices": [{"platform": "android", "app_version": "1.0.3"}]})
            db.add(t)
        # histórico para KPIs (resueltos 30 d ≈ 214)
        cats = ["account_access"] * 32 + ["wearable_sync"] * 24 + ["alerts_push"] * 14 + \
               ["billing_plans"] * 12 + ["medications"] * 9 + ["other"] * 9
        hist = 0
        for j in range(245):
            created = NOW - timedelta(days=random.uniform(0, 30))
            resolved = random.random() < .87
            t = models.Ticket(number=n - 6 - j, subject=f"Caso histórico {j + 1}",
                              description="Ticket histórico de demo.",
                              requester_name="Usuario Demo", requester_email=f"hist{j}@demo.cl",
                              requester_role=random.choice(ROLE_ORDER),
                              category=cats[j % len(cats)],
                              priority=random.choice(["low", "medium", "medium", "high"]),
                              status="resolved" if resolved else random.choice(
                                  ["open", "in_progress", "waiting_user"]),
                              channel="app", created_at=created,
                              first_response_at=created + timedelta(hours=random.uniform(.5, 5)),
                              resolved_at=created + timedelta(hours=random.uniform(4, 60)) if resolved else None,
                              csat_score=random.choice([4, 5, 5, 5, 3]) if resolved and random.random() < .55 else None)
            db.add(t)
            hist += 1

        # ---- Contenido ----
        db.add_all([
            models.ContentItem(type="joke", title="El loro políglota",
                               body="—Doctor, mi loro habla tres idiomas. —¿Y cuál prefiere? —El silencio, "
                                    "cuando le toca la siesta.", tags=["humor blanco"], status="published",
                               published_at=NOW - timedelta(days=3), tts_ready=True,
                               created_by=admins[3].id, created_by_name=admins[3].full_name),
            models.ContentItem(type="news", title="Chile lidera adopción de telemedicina en la región",
                               body="Un estudio regional destaca el crecimiento de las consultas a distancia "
                                    "entre personas mayores de 60 años, con foco en el seguimiento crónico.",
                               tags=["actualidad", "salud"], status="published",
                               published_at=NOW - timedelta(days=1), tts_ready=True,
                               created_by=admins[3].id, created_by_name=admins[3].full_name),
            models.ContentItem(type="joke", title="La receta de la abuela",
                               body="La nieta le pregunta a la abuela por la receta secreta de sus empanadas. "
                                    "—Fácil: se hacen con calma y se comen con familia.",
                               tags=["humor blanco", "familia"], status="draft", tts_ready=True,
                               created_by=admins[3].id, created_by_name=admins[3].full_name),
        ])

        # ---- Marketplace ----
        db.add_all([
            models.CaregiverProfile(name="María Torres", email="maria@cuidado.cl", zone="Providencia",
                                    specialties=["Alzheimer", "Movilidad reducida"], languages=["es"],
                                    certifications_count=3, certifications_verified=True,
                                    rating_avg=4.8, reviews_count=26, status="approved"),
            models.CaregiverProfile(name="Paula Fuentes", email="paula@cuidado.cl", zone="Ñuñoa",
                                    specialties=["Posoperatorio"], languages=["es", "en"],
                                    certifications_count=1, certifications_verified=False,
                                    rating_avg=None, reviews_count=0, status="pending"),
            models.Product(name="Andador plegable con asiento", category="Movilidad",
                           vendor="OrtoChile", price_clp=64990,
                           external_url="https://ortochile.cl/andador-plegable", status="published"),
            models.Product(name="Pastillero semanal electrónico", category="Medicación",
                           vendor="SaludHogar", price_clp=29990,
                           external_url="https://saludhogar.cl/pastillero", status="published"),
        ])

        # ---- Moderación ----
        db.add_all([
            models.ModerationItem(type="review", author_name="Familia Pérez", author_role="family",
                                  content={"rating": 5, "text": "Excelente cuidadora, muy puntual y cariñosa."},
                                  status="pending"),
            models.ModerationItem(type="photo", author_name="Usuario Demo 4", author_role="family",
                                  content={"url": "https://storage.demo/signed/foto123", "album": "Cumpleaños"},
                                  reported_by={"name": "Usuario Demo 7", "role": "caregiver"},
                                  report_reason="Contenido que expone datos personales", is_safety=True,
                                  status="pending"),
        ])

        # ---- Configuración ----
        db.add_all([
            models.SystemSetting(
                key="plan_prices", description="Precios mensuales por plan (CLP).",
                value={"gold": 1000, "platinum": 10000, "provider": 5000},
                value_schema={"type": "object",
                              "properties": {"gold": {"type": "integer", "minimum": 0},
                                             "platinum": {"type": "integer", "minimum": 0},
                                             "provider": {"type": "integer", "minimum": 0}},
                              "required": ["gold", "platinum", "provider"],
                              "additionalProperties": False}),
            models.SystemSetting(
                key="churn_definition", description="Definición de churn para métricas comerciales.",
                value={"explicit_cancellation": True, "inactivity_days": 60},
                value_schema={"type": "object",
                              "properties": {"explicit_cancellation": {"type": "boolean"},
                                             "inactivity_days": {"type": "integer", "minimum": 15,
                                                                 "maximum": 180}},
                              "required": ["explicit_cancellation", "inactivity_days"],
                              "additionalProperties": False}),
            models.SystemSetting(
                key="ops_thresholds.api_core", description="Umbrales de salud del componente API central.",
                value={"p95_ms": 500, "error_rate": 0.02},
                value_schema={"type": "object",
                              "properties": {"p95_ms": {"type": "integer", "minimum": 50},
                                             "error_rate": {"type": "number", "minimum": 0, "maximum": 1}},
                              "required": ["p95_ms", "error_rate"],
                              "additionalProperties": False}),
        ])

        # ---- Legales ----
        db.add_all([
            models.LegalVersion(doc_type="terms", semver="1.0", status="published",
                                content_md="# Términos de servicio de AgeCare\n\nVersión inicial." + " Lorem." * 30,
                                changelog="Versión inicial de lanzamiento.",
                                effective_date=date(2026, 6, 1), requires_reacceptance=True,
                                published_at=NOW - timedelta(days=88),
                                created_by=admin.id, created_by_name=admin.full_name),
            models.LegalVersion(doc_type="privacy", semver="1.0", status="published",
                                content_md="# Política de privacidad de AgeCare\n\nVersión inicial." + " Lorem." * 30,
                                changelog="Versión inicial de lanzamiento.",
                                effective_date=date(2026, 6, 1), requires_reacceptance=True,
                                published_at=NOW - timedelta(days=88),
                                created_by=admin.id, created_by_name=admin.full_name),
        ])

        # ---- Facturación (pasarela de pago y suscripciones) ----
        n_subs = await seed_billing(db, admin)

        await db.commit()
        total_tickets = (await db.execute(select(models.Ticket))).scalars().all()
        print(f"Seed completado: {len(admins)} cuentas de staff, {len(total_tickets)} tickets, "
              f"{len(FEATURES)} funcionalidades, métricas de 14 meses, {n_subs} suscripciones demo.")


if __name__ == "__main__":
    asyncio.run(seed())
