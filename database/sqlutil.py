"""Helpers SQL seguros: identificadores allowlisted, placeholders bound e LIKE escapado."""
from __future__ import annotations

import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_ident(name: str) -> str:
    """Aceita só identificadores SQLite simples (tabela/coluna). Evita injeção em PRAGMA/ALTER."""
    ident = str(name or "")
    if not _IDENT_RE.fullmatch(ident):
        raise ValueError("identificador SQL inválido")
    return ident


def qmarks(count: int) -> str:
    """Lista ``?,?,?`` para ``IN (...)`` — nunca interpolar valores do usuário aqui."""
    n = int(count)
    if n < 1:
        raise ValueError("qmarks exige count >= 1")
    return ",".join("?" * n)


def like_pattern(text: str) -> str:
    """Padrão ``LIKE`` com ``%``/``_`` escapados (usar com ``ESCAPE '\\'``)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def digits(text: str | None) -> str:
    """Só os dígitos (CPF e telefone digitados com ou sem máscara)."""
    return "".join(ch for ch in (text or "") if ch.isdigit())
