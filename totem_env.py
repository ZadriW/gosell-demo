"""Carrega variáveis de ambiente de arquivos locais (não versionados).

Procura, na raiz do projeto, por ``.env`` e ``totem.env`` (nesta ordem).
Valores já definidos no ambiente do sistema **não são sobrescritos**.

Uso: importar este módulo o mais cedo possível em ``app.py``::

    import totem_env  # noqa: F401
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_ENV_FILES = (_ROOT / ".env", _ROOT / "totem.env")
_PERSIST_ENV = _ROOT / "totem.env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].strip()
    if "=" not in stripped:
        return None
    key, _, raw_val = stripped.partition("=")
    key = key.strip()
    if not key:
        return None
    val = raw_val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return key, val


def load_env_files() -> None:
    """Lê arquivos ``.env`` / ``totem.env`` e injeta em ``os.environ``."""
    for path in _ENV_FILES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            parsed = _parse_env_line(line)
            if parsed is None:
                continue
            key, val = parsed
            os.environ.setdefault(key, val)


def ensure_persistent_secret_key() -> str:
    """Garante ``TOTEM_SECRET_KEY`` estável entre restarts do Flask (debug reload).

    Sem isso, cada alteração de código gera uma chave nova, invalida cookies de
    login/CSRF e pode fazer o navegador reenviar o formulário de vendedor com a
    senha antiga preenchida automaticamente.
    """
    existing = (os.environ.get("TOTEM_SECRET_KEY") or "").strip()
    if existing:
        return existing
    key = secrets.token_hex(32)
    os.environ["TOTEM_SECRET_KEY"] = key
    try:
        prefix = ""
        if _PERSIST_ENV.is_file() and _PERSIST_ENV.stat().st_size > 0:
            text = _PERSIST_ENV.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                prefix = "\n"
        with _PERSIST_ENV.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}TOTEM_SECRET_KEY={key}\n")
    except OSError:
        pass
    return key


load_env_files()
ensure_persistent_secret_key()
