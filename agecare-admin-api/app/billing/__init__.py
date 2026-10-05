"""Pasarela de pago y suscripciones de AgeCare (Transbank Oneclick Mall).

Módulos:
    models   — tablas billing_* (portables: PostgreSQL en producción, SQLite en tests)
    gateway  — interfaz de pasarela + cliente Transbank Oneclick Mall + simulador
    service  — reglas de negocio: suscribir, cobrar, reintentar, cancelar, reembolsar
    jobs     — ciclo diario: conciliación, renovaciones, reintentos y expiración
    router_app / router_admin — endpoints para la app y para la consola
"""
