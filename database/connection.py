"""Caminhos do arquivo SQLite e fábrica de conexão (único lugar para PRAGMA e commit)."""
from __future__ import annotations

import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from . import product_search

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(_ROOT_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "gosell.sqlite3")

# Estoque mínimo padrão para produtos novos (biblioteca e eventos).
DEFAULT_MIN_STOCK = 5


def _ensure_dir() -> None:
    os.makedirs(DB_DIR, exist_ok=True)


def fold_search_text(text: Optional[str]) -> str:
    """Minúsculas e sem acento — para busca textual tolerante a caixa/acentuação.

    O ``LOWER()`` nativo do SQLite só normaliza ASCII: "GALVÃO" vira "galvÃo"
    (o "Ã" permanece intacto), então comparar com "galvão" digitado pelo
    usuário falha. Aqui usamos o casefold do Python (via NFD) antes de
    remover os acentos, cobrindo tanto o valor da coluna quanto o termo de
    busca.
    """
    normalized = unicodedata.normalize("NFD", (text or "").strip().casefold())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _connect() -> sqlite3.Connection:
    _ensure_dir()
    # Espera a vez de escrever (worker do ERP, sincronização do catálogo) em vez
    # de falhar com "database is locked" após os 5 s padrão.
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Em WAL (``enable_wal``), NORMAL só sincroniza o disco no checkpoint: seguro
    # contra queda do processo e é o recomendado pelo Litestream.
    conn.execute("PRAGMA synchronous = NORMAL")
    # Busca de produtos: product_search_score(busca, nome, variante, sku, apelidos).
    conn.create_function(
        "product_search_score", 5, product_search.sql_score, deterministic=True
    )

    def _search_fold_sql(value) -> str:
        return fold_search_text(value)

    conn.create_function("search_fold", 1, _search_fold_sql, deterministic=True)
    return conn


def enable_wal() -> str:
    """Põe o banco em modo WAL (fica gravado no arquivo; basta uma vez).

    O backup contínuo (Litestream, ``backup_nuvem.py``) só funciona em WAL, e
    o WAL também deixa as leituras do painel correrem durante as gravações do
    worker do ERP. Com WAL, o banco são três arquivos (``totem.sqlite3``,
    ``-wal`` e ``-shm``): copiar só o primeiro com o sistema aberto pode perder
    as últimas vendas. Devolve o modo em vigor.
    """
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        return str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
    finally:
        conn.close()


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
