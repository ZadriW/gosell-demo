"""Assinatura HMAC para URLs públicas da nota de retirada (/nota/...?t=...)."""
from __future__ import annotations

import hashlib
import hmac

_SALT = b"totem-receipt-url-v1"


def sign_receipt_token(order_number: str, secret: str) -> str:
    """Gera token ``t`` determinístico para o número do pedido."""
    key = (secret or "").encode("utf-8")
    msg = _SALT + b":" + (order_number or "").strip().encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_receipt_token(order_number: str, token: str, secret: str) -> bool:
    """Valida ``t`` contra o número do pedido (comparação em tempo constante)."""
    supplied = (token or "").strip().lower()
    if not supplied or len(supplied) != 64:
        return False
    expected = sign_receipt_token(order_number, secret)
    return hmac.compare_digest(expected, supplied)
