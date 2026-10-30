"""Confere as regras de promoção (mesmo motor do Go Sell)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.promotions import (
    _compute_effective_subtotal,
    quote_cart_items_for_event,
)
from seed.demo import EVENT_NAME
from database.connection import get_conn


def _assert_close(got, expected, msg):
    if abs(float(got) - float(expected)) > 0.001:
        raise AssertionError(f"{msg}: esperado {expected}, obtido {got}")


def test_compute_effective_subtotal():
    # A partir de 3 un.: R$10 cada (item de R$12). O mínimo é conferido por quem chama.
    _assert_close(_compute_effective_subtotal("min_bundle", 10, 3, 0, 12, 3), 30, "min_bundle no mínimo")
    _assert_close(_compute_effective_subtotal("min_bundle", 10, 3, 0, 12, 4), 40, "min_bundle 4 un.")
    _assert_close(_compute_effective_subtotal("min_bundle", 10, 3, 0, 12, 6), 60, "min_bundle 6 un.")
    # Nunca encarece: preço da promoção acima do preço de lista mantém o de lista.
    _assert_close(_compute_effective_subtotal("min_bundle", 15, 3, 0, 12, 3), 36, "min_bundle sem encarecer")

    # Na compra de: só múltiplos completos no valor do kit
    _assert_close(_compute_effective_subtotal("exact_bundle", 50, 2, 0, 28, 1), 28, "exact_bundle 1 un.")
    _assert_close(_compute_effective_subtotal("exact_bundle", 50, 2, 0, 28, 2), 50, "exact_bundle 1 kit")
    _assert_close(_compute_effective_subtotal("exact_bundle", 50, 2, 0, 28, 3), 78, "exact_bundle kit + extra")
    _assert_close(_compute_effective_subtotal("exact_bundle", 50, 2, 0, 28, 4), 100, "exact_bundle 2 kits")

    # Compre X, Leve Y no mesmo SKU: pague X, leve X+Y
    _assert_close(_compute_effective_subtotal("bogo", 0, 2, 1, 10, 2), 20, "bogo ainda sem grátis")
    _assert_close(_compute_effective_subtotal("bogo", 0, 2, 1, 10, 3), 20, "bogo compre 2 leve 3")
    _assert_close(_compute_effective_subtotal("bogo", 0, 1, 1, 35, 2), 35, "bogo 1+1")
    print("compute_effective_subtotal ok")


def test_quote_on_seeded_event():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM events WHERE name = ? LIMIT 1",
            (EVENT_NAME,),
        ).fetchone()
        if row is None:
            print("seed ausente — pulando quote no evento")
            return
        event_id = int(row["id"])

        def pid(sku):
            r = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()
            return int(r["id"])

        fio = pid("GS-CS-004")
        avental = pid("GS-AC-002")
        oculos = pid("GS-AC-001")
        espelho = pid("GS-AC-003")

    q = quote_cart_items_for_event(event_id, [{"id": fio, "quantidade": 2, "preco": 12}])
    _assert_close(q["total"], 24, "quote min_bundle fio abaixo do mínimo")
    q = quote_cart_items_for_event(event_id, [{"id": fio, "quantidade": 3, "preco": 12}])
    _assert_close(q["total"], 30, "quote min_bundle fio")

    q = quote_cart_items_for_event(event_id, [{"id": avental, "quantidade": 2, "preco": 28}])
    _assert_close(q["total"], 50, "quote exact_bundle avental")

    q = quote_cart_items_for_event(
        event_id,
        [
            {"id": oculos, "quantidade": 1, "preco": 35},
            {"id": espelho, "quantidade": 1, "preco": 22},
        ],
    )
    # 1 óculos pago + 1 espelho grátis (bogo cross-SKU injeta/zera o Y)
    items_by_id = {int(i["id"]): i for i in q["items"]}
    _assert_close(items_by_id[oculos]["subtotal"], 35, "bogo óculos pago")
    if espelho in items_by_id:
        _assert_close(items_by_id[espelho]["subtotal"], 0, "bogo espelho grátis")
    _assert_close(q["total"], 35, "quote bogo compre óculos leve espelho")
    print("quote_cart_items_for_event ok")


if __name__ == "__main__":
    test_compute_effective_subtotal()
    test_quote_on_seeded_event()
    print("PROMO RULES OK")
