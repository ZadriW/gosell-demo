"""Trilha de navegação (breadcrumb) dos painéis admin e vendedor.

Mantém, em um **cookie assinado próprio** (separado da sessão Flask), o caminho
percorrido por cada escopo (``admin`` / ``seller``) para que o usuário veja todo
o trajeto até a página atual e volte a qualquer página anterior pela trilha.

Por que um cookie dedicado à trilha
-----------------------------------
- a sessão Flask continua pequena (só a trilha cresce com a navegação);
- trilha corrompida, adulterada ou grande demais nunca derruba login/CSRF —
  o pior caso é a trilha desaparecer;
- dá para limpar a trilha sem perder a sessão.

Robustez
--------
Este módulo é **puro**: não importa Flask, banco nem rede. Nenhuma função
levanta exceção — entrada inválida simplesmente não entra na trilha.

- Só URLs internas relativas (``/...``) são aceitas: nada de host externo,
  esquema, ``//``, ``\\``, quebras de linha ou segmentos ``.`` / ``..``.
- Limites por quantidade **e** por bytes; ao estourar, o item mais antigo sai.
- Itens são identificados pelo ``path``: a query string não duplica a trilha,
  mas é preservada na URL para restaurar filtros/paginação ao voltar.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

# Cookie dedicado (assinado pelo app com ``COOKIE_SALT``).
COOKIE_NAME = "totem_trail"
COOKIE_SALT = "totem-breadcrumb-trail"
COOKIE_MAX_AGE = 8 * 60 * 60  # 8 h — cobre um turno de evento
MAX_COOKIE_CHARS = 3000  # margem folgada sob o limite de ~4 KB por cookie

PAYLOAD_VERSION = 1
SCOPES = ("admin", "seller")

MAX_ITEMS = 8
MAX_URL_LEN = 180
MAX_LABEL_LEN = 48
MAX_PAYLOAD_BYTES = 1800

_ELLIPSIS = "…"
_CONTROL_CHARS = ("\\", "\n", "\r", "\t", "\x00")
_RELATIVE_SEGMENTS = ("", ".", "..")


def _clamp_scope(raw: Any) -> str:
    scope = str(raw if raw is not None else "").strip().lower()
    return scope if scope in SCOPES else ""


def clean_label(raw: Any) -> str:
    """Texto de uma linha, sem caracteres de controle, truncado com reticências."""
    text = str(raw if raw is not None else "")
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    text = "".join(ch for ch in text if ch.isprintable())
    if len(text) > MAX_LABEL_LEN:
        text = text[: MAX_LABEL_LEN - 1].rstrip() + _ELLIPSIS
    return text


def clean_url(raw: Any) -> str:
    """Aceita apenas caminho interno do próprio sistema; devolve ``""`` se inválido."""
    text = str(raw if raw is not None else "").strip()
    if not text or not text.startswith("/") or text.startswith("//"):
        return ""
    if any(ch in text for ch in _CONTROL_CHARS):
        return ""
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        return ""
    path = parsed.path or ""
    if not path.startswith("/") or path.startswith("//"):
        return ""
    if path != "/" and any(seg in _RELATIVE_SEGMENTS for seg in path.split("/")[1:]):
        return ""
    out = path + (f"?{parsed.query}" if parsed.query else "")
    if len(out) > MAX_URL_LEN:
        out = path
    return out[:MAX_URL_LEN]


def make_item(scope: Any, url: Any, label: Any) -> Optional[Dict[str, str]]:
    """Monta um item válido da trilha, ou ``None`` quando algo é inválido."""
    scope_key = _clamp_scope(scope)
    safe_url = clean_url(url)
    safe_label = clean_label(label)
    if not scope_key or not safe_url or not safe_label:
        return None
    return {
        "k": scope_key,                          # escopo (admin/seller)
        "u": safe_url,                           # URL interna (com filtros)
        "p": urlparse(safe_url).path or "/",     # identidade da página
        "l": safe_label,                         # rótulo exibido
    }


def normalize_item(raw: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    return make_item(raw.get("k"), raw.get("u"), raw.get("l"))



def payload_bytes(items: Iterable[Dict[str, str]]) -> int:
    """Tamanho aproximado do payload serializado (usado para caber no cookie)."""
    try:
        raw = json.dumps(
            {"v": PAYLOAD_VERSION, "items": list(items)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return 0
    return len(raw.encode("utf-8"))


def clamp(items: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """Limita por quantidade e por bytes; descarta sempre o item mais antigo."""
    out = list(items)
    if len(out) > MAX_ITEMS:
        out = out[-MAX_ITEMS:]
    while len(out) > 1 and payload_bytes(out) > MAX_PAYLOAD_BYTES:
        out.pop(0)
    return out


def normalize_items(raw: Any) -> List[Dict[str, str]]:
    """Sanitiza uma lista qualquer de itens (descarta o que não for válido)."""
    if not isinstance(raw, (list, tuple)):
        return []
    items: List[Dict[str, str]] = []
    for entry in raw:
        item = normalize_item(entry)
        if item is not None:
            items.append(item)
    return clamp(items)


def normalize_payload(raw: Any) -> List[Dict[str, str]]:
    """Interpreta o conteúdo do cookie (``{"v": 1, "items": [...]}``) ou uma lista."""
    if isinstance(raw, dict):
        try:
            version = int(raw.get("v") or 0)
        except (TypeError, ValueError):
            version = 0
        if version != PAYLOAD_VERSION:
            return []
        return normalize_items(raw.get("items"))
    return normalize_items(raw)


def payload(items: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    """Payload pronto para virar cookie assinado."""
    return {"v": PAYLOAD_VERSION, "items": clamp(items)}


def for_scope(items: Any, scope: Any) -> List[Dict[str, str]]:
    """Itens de um escopo, na ordem em que foram visitados."""
    key = _clamp_scope(scope)
    if not key:
        return []
    return [dict(item) for item in normalize_items(items) if item["k"] == key]


def drop_scope(items: Any, scope: Any) -> List[Dict[str, str]]:
    """Remove a trilha de um escopo (login/logout) preservando a do outro painel."""
    key = _clamp_scope(scope)
    if not key:
        return normalize_items(items)
    return [item for item in normalize_items(items) if item["k"] != key]


def push(
    items: Any,
    *,
    scope: Any,
    url: Any,
    label: Any,
    ancestors: Optional[Iterable[Any]] = None,
) -> List[Dict[str, str]]:
    """Acrescenta a página atual à trilha do escopo.

    Comportamento de navegador: revisitar uma página já presente recorta a cauda
    (o "ir para frente" é descartado). ``ancestors`` (lista de ``{"u","l"}``)
    completa o caminho hierárquico quando o usuário chega por link direto,
    atalho ou URL digitada — a trilha nunca mostra uma página fora de contexto.
    """
    scope_key = _clamp_scope(scope)
    entry = make_item(scope_key, url, label)
    current = normalize_items(items)
    if not scope_key or entry is None:
        return current

    others = [item for item in current if item["k"] != scope_key]
    trail = [item for item in current if item["k"] == scope_key]

    same = [index for index, item in enumerate(trail) if item["p"] == entry["p"]]
    if same:
        trail = trail[: same[0]]

    for ancestor in ancestors or []:
        if not isinstance(ancestor, dict):
            continue
        anc_item = make_item(scope_key, ancestor.get("u"), ancestor.get("l"))
        if anc_item is None:
            continue
        if any(item["p"] == anc_item["p"] for item in trail):
            continue
        trail.append(anc_item)

    trail.append(entry)
    return clamp(others + trail)


def previous_url(items: Any, scope: Any) -> str:
    """URL da página anterior do escopo (``""`` quando não há para onde voltar)."""
    trail = for_scope(items, scope)
    if len(trail) < 2:
        return ""
    return trail[-2]["u"]
