"""Categorias do catálogo da demo (cadastro local, sem ERP)."""
from __future__ import annotations

from typing import Dict, List

CATEGORIES: List[str] = ["Equipamentos", "Consumíveis", "Acessórios"]


def get_seed_products() -> List[Dict]:
    return []


def get_products() -> List[Dict]:
    return []
