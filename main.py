"""Ponto de entrada WSGI para PaaS (ex.: Railway) que usam ``gunicorn main:app`` por padrão."""

import totem_env  # noqa: F401 — carrega .env antes de importar app

from app import app

__all__ = ["app"]
