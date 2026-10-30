"""Event badge color normalization (UI)."""
from __future__ import annotations

import re
from typing import Optional, Tuple

_HEX_BADGE_6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX_BADGE_3 = re.compile(r"^#[0-9a-fA-F]{3}$")


def normalize_event_badge_color(raw: Optional[str]) -> Optional[str]:
    """Aceita ``#RGB`` ou ``#RRGGBB``. Retorna ``None`` para usar o estilo padrão do tema."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if _HEX_BADGE_6.match(s):
        return s.lower()
    m = _HEX_BADGE_3.match(s)
    if m:
        h = m.group(0)[1:]
        return f"#{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}".lower()
    return None


def event_badge_fg_hex(bg_hex: str) -> str:
    """Cor de texto legível sobre ``bg_hex`` (#RRGGBB)."""
    h = (normalize_event_badge_color(bg_hex) or "#0e167a").lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def lin(x: float) -> float:
        c = x / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#0f172a" if lum > 0.55 else "#ffffff"


def event_badge_style_pairs(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Retorna (background_hex, foreground_hex) ou (None, None) para badge sem cor personalizada."""
    bg = normalize_event_badge_color(raw)
    if not bg:
        return None, None
    return bg, event_badge_fg_hex(bg)

