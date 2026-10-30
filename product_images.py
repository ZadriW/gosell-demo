"""Imagens dos produtos na demo: fotos locais, sem download nem miniaturas.

Mantém a mesma API do módulo do produto (``resolve_image_url[s]``,
``store_image_bytes``, ``cache_images_*``, ``start_thumbnail_backfill``) para o
``app.py`` e a sincronização do ERP simulado funcionarem sem alteração.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

log = logging.getLogger(__name__)

IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "images", "products")
LOCAL_URL_PREFIX = "/static/images/products/"

_EMPTY_STATS = {"downloaded": 0, "skipped": 0, "failed": 0, "total": 0}


def resolve_image_url(_product_id: int, remote_url: Optional[str]) -> str:
    """Na demo as fotos já são locais; devolve a URL gravada no cadastro."""
    return (remote_url or "").strip()


def resolve_image_urls(product_id: int, remote_url: Optional[str]) -> Tuple[str, str]:
    """``(foto, miniatura do card)`` — na demo as duas são o mesmo arquivo."""
    url = resolve_image_url(product_id, remote_url)
    return url, url


def _ext_from_bytes(data: bytes) -> Optional[str]:
    head = data[:256].lstrip()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return ".svg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def store_image_bytes(product_id: int, data: bytes) -> Tuple[Optional[str], bool]:
    """Grava a imagem recebida do ERP simulado. Devolve ``(caminho público, gravou?)``."""
    pid = int(product_id)
    if pid <= 0 or not data:
        return None, False
    ext = _ext_from_bytes(data)
    if ext is None:
        log.warning("Imagem do produto %s em formato desconhecido; ignorada.", pid)
        return None, False
    filename = f"erp-{pid}{ext}"
    dest = os.path.join(IMAGES_DIR, filename)
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        if os.path.isfile(dest):
            with open(dest, "rb") as fh:
                if fh.read() == data:
                    return f"{LOCAL_URL_PREFIX}{filename}", False
        with open(dest, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        log.warning("Não foi possível gravar imagem do produto %s: %s", pid, exc)
        return None, False
    return f"{LOCAL_URL_PREFIX}{filename}", True


def start_thumbnail_backfill() -> bool:
    """Sem miniaturas na demo (as fotos são SVG leves)."""
    return False


def cache_images_for_event(_event_id: int) -> dict:
    return dict(_EMPTY_STATS)


def cache_images_for_catalog() -> dict:
    return dict(_EMPTY_STATS)
