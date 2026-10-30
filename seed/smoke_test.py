"""Smoke test do roteiro da demo (Flask test client).

Reinicia a demonstração antes de começar (mesmo efeito do botão "Reiniciar
sistema") e de novo no fim, para deixar o banco no estado inicial.

    python seed/smoke_test.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TOTEM_ERP_WORKER", "0")  # a fila é processada à mão aqui
os.environ["DEMO_ERP_LATENCY"] = "0"

from app import app  # noqa: E402
from database import get_conn  # noqa: E402
from data import erp_mock  # noqa: E402
from receipt_tokens import sign_receipt_token, verify_receipt_token  # noqa: E402
from seed.demo import CONGRESS_NAME, EVENT_NAME, reset_demo_data  # noqa: E402

import erp_sync  # noqa: E402

#: Textos que não podem aparecer em nenhuma página da demo.
FORBIDDEN = ("Odonto", "CNPJ", "Sankhya", "Aulas", "Pix Inter")


def _clean(html: str, where: str) -> None:
    for word in FORBIDDEN:
        assert word not in html, f"{where}: contém {word!r}"


def _csrf_meta(html: str) -> str:
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html) or re.search(
        r'content="([^"]+)"\s+name="csrf-token"', html
    )
    assert m, "csrf meta not found"
    return m.group(1)


def _login(c, username: str) -> str:
    r = c.post("/entrar", data={"username": username, "password": "demo"})
    assert r.status_code == 302, (username, r.status_code)
    return r.headers["Location"]


def _ids() -> dict:
    with get_conn() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "ev": one("SELECT id FROM events WHERE name = ?", EVENT_NAME),
            "cg": one("SELECT id FROM events WHERE name = ?", CONGRESS_NAME),
            "luva": one("SELECT id FROM products WHERE sku = 'GS-CS-001'"),
            "espelho": one("SELECT id FROM products WHERE sku = 'GS-AC-003'"),
            "tx": one("SELECT id FROM transactions WHERE status = 'confirmado' ORDER BY id DESC LIMIT 1"),
            "gift": one("SELECT id FROM gift_rules LIMIT 1"),
            "ana": one("SELECT id FROM sellers WHERE username = 'ana'"),
        }


def _outbox_status(tx_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM erp_outbox WHERE transaction_id = ? AND kind = 'pedido'", (tx_id,)
        ).fetchone()
    return row["status"] if row else ""


def main() -> None:
    app.config["WTF_CSRF_ENABLED"] = False
    result = reset_demo_data()
    print("0 reset", result)
    ids = _ids()
    ev, cg = ids["ev"], ids["cg"]

    # ------------------------------------------------------------------ boas-vindas
    c = app.test_client()
    html = c.get("/").get_data(as_text=True)
    _clean(html, "welcome")
    for account in ("admin / demo", "ana / demo", "marina / demo", "carlos / demo"):
        assert account in html, account
    print("1 welcome ok")

    # ------------------------------------------------------------------ administrador
    assert _login(c, "admin").startswith("/admin")
    pages = [
        "/admin/eventos", "/admin/produtos", "/admin/usuarios", f"/admin/usuarios/{ids['ana']}",
        f"/admin/financeiro?evento={ev}", f"/admin/financeiro/pdf?evento={ev}",
        "/admin/integracao-erp",
        f"/admin/eventos/{ev}", f"/admin/eventos/{cg}", f"/admin/eventos/{ev}/estoque",
        f"/admin/eventos/{ev}/transacoes", f"/admin/eventos/{ev}/transacoes/{ids['tx']}/pdf",
        f"/admin/eventos/{ev}/movimentacoes", f"/admin/eventos/{ev}/movimentacoes/pdf",
        f"/admin/eventos/{ev}/promocoes", f"/admin/eventos/{ev}/promocoes?modo=brindes",
        f"/admin/eventos/{ev}/promocoes/nova", f"/admin/eventos/{ev}/promocoes/brindes/{ids['gift']}",
        f"/admin/eventos/{ev}/clientes", f"/admin/eventos/{ev}/clientes/pdf",
    ]
    for url in pages:
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
        _clean(r.get_data(as_text=True), url)
    print("2 admin pages ok", len(pages))

    for url in (
        f"/admin/eventos/{ev}/clientes/export.xlsx",
        f"/admin/eventos/{ev}/movimentacoes/export.xlsx",
        f"/admin/eventos/{ev}/estoque/export.xlsx",
    ):
        r = c.get(url)
        assert r.status_code == 200 and r.data[:2] == b"PK", url
    print("3 xlsx exports ok")

    html = c.get(f"/admin/eventos/{ev}/clientes").get_data(as_text=True)
    assert "Carla Mendes" in html
    m = re.search(r'href="(/admin/eventos/\d+/clientes/cpf/\d+)"', html)
    assert m, "link do perfil do cliente"
    r = c.get(m.group(1))
    assert r.status_code == 200
    print("4 customers + profile ok")

    html = c.get(f"/admin/eventos/{ev}/promocoes?modo=brindes").get_data(as_text=True)
    assert "Caneca Go Sell" in html
    r = c.post(f"/admin/eventos/{ev}/promocoes/brindes/avulsos",
               data={"name": "Chaveiro Go Sell", "stock": "40"})
    assert r.status_code == 302
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM gift_items WHERE name = 'Chaveiro Go Sell'").fetchone()
    print("5 gifts ok")

    r = c.post(f"/admin/eventos/{ev}/produtos/saida-em-lote", data={
        "reason": "Amostras para palestra", "product_ids": [str(ids["luva"])],
        f"qty_{ids['luva']}": "2",
    })
    assert r.status_code in (200, 302), r.status_code
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM stock_movements WHERE reason LIKE 'Amostras para palestra%'"
        ).fetchone(), "saída em lote não registrada"
    print("6 bulk stock exit ok")

    r = c.post("/admin/usuarios", data={
        "name": "Paula Marketing", "username": "paula", "password": "demo1234",
        "role": "marketing", "panel_event_id": [str(ev)],
    })
    assert r.status_code in (200, 302), r.status_code
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM sellers WHERE username = 'paula'").fetchone()
    assert row and row["role"] == "marketing", row
    print("7 create marketing user ok")

    # ERP: erro semeado → reenviar → enviado
    with get_conn() as conn:
        job = conn.execute("SELECT id, transaction_id FROM erp_outbox WHERE status = 'erro'").fetchone()
    assert job, "fila sem o erro de demonstração"
    r = c.post(f"/admin/integracao-erp/fila/{job['id']}/reenviar")
    assert r.status_code in (200, 302)
    erp_sync.process_outbox_once()
    assert _outbox_status(int(job["transaction_id"])) == "enviado"
    r = c.post("/admin/integracao-erp/testar-conexao")
    assert r.status_code in (200, 302)
    stats = erp_sync.sync_catalog()
    assert stats["inserted"] >= 1, stats
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM products WHERE erp_codprod = 10404").fetchone()
        price = conn.execute("SELECT price FROM products WHERE sku = 'GS-AC-003'").fetchone()[0]
    assert abs(price - erp_mock.FOLLOWUP_PRICES[10303]) < 0.01, price
    print("8 ERP retry + connection + catalog sync ok")

    c.post("/sair")

    # ------------------------------------------------------------------ vendedora
    c = app.test_client()
    assert _login(c, "ana").startswith("/vendedor")
    html = c.get("/vendedor/venda").get_data(as_text=True)
    assert "Luva nitrilo" in html
    _clean(html, "catalog")
    headers = {"X-CSRFToken": _csrf_meta(html), "Content-Type": "application/json"}

    html = c.get("/vendedor/pagamento").get_data(as_text=True)
    assert "CRO" not in html and "Nome completo" in html
    _clean(html, "checkout")
    print("9 seller catalog + checkout ok")

    erp_only = erp_mock.ERP_ONLY_CUSTOMER
    data = c.get(f"/vendedor/api/clientes/consulta?cpf={erp_only['cpf']}").get_json()
    assert data["found"] and data["customer"]["name"] == erp_only["name"], data
    print("10 CPF lookup (ERP) ok")

    luva = ids["luva"]
    items = [{"id": luva, "nome": "Luva nitrilo (caixa 100)", "preco": 42, "quantidade": 3,
              "sku": "GS-CS-001"}]
    r = c.post("/vendedor/api/checkout/reserva", json={"items": items}, headers=headers)
    assert r.status_code == 200 and r.get_json()["ok"]
    q = c.post("/api/carrinho/cotacao", json={"items": items}, headers=headers).get_json()
    assert any(i.get("gift_rule_id") or i.get("brinde") for i in q.get("items", [])), q
    print("11 checkout hold + quote with gift ok")

    r = c.post("/api/transacoes", headers=headers, json={
        "items": items, "payment_method": "debito",
        "client": {"name": erp_only["name"], "cpf": erp_only["cpf"], "payment_method": "debito"},
    })
    assert r.status_code == 201, r.get_json()
    tx = r.get_json()
    r = c.patch(f"/api/transacoes/{tx['id']}/aut", json={"aut": "DEB7788"}, headers=headers)
    assert r.status_code == 200, r.get_json()
    assert _outbox_status(int(tx["id"])) == "pendente"
    erp_sync.process_outbox_once()
    assert _outbox_status(int(tx["id"])) == "enviado"
    print("12 sale confirmed and sent to ERP ok")

    order = tx["order_number"]
    assert order.startswith("GS"), order
    token = sign_receipt_token(order, app.secret_key)
    assert verify_receipt_token(order, token, app.secret_key)
    html = c.get(f"/nota/{order}?t={token}").get_data(as_text=True)
    assert erp_only["name"] in html and "(BRINDE)" in html
    _clean(html, "nota")
    bad = c.get(f"/nota/{order}?t=deadbeef").get_data(as_text=True)
    assert erp_only["name"] not in bad
    print("13 receipt with gift ok")

    assert c.get("/vendedor/dashboard").status_code == 200
    assert c.get(f"/admin/eventos/{ev}/estoque").status_code in (302, 401, 403)
    print("14 seller dashboard ok; admin blocked")

    # ------------------------------------------------------------------ marketing
    c = app.test_client()
    _login(c, "marina")
    assert c.get(f"/admin/eventos/{ev}").status_code == 200
    assert c.get(f"/admin/eventos/{ev}/promocoes").status_code == 200
    assert c.get(f"/admin/eventos/{ev}/estoque").status_code == 302
    assert c.get("/admin/integracao-erp").status_code == 302
    print("15 marketing permissions ok")

    print("16 reset", reset_demo_data())
    print("SMOKE OK")


if __name__ == "__main__":
    main()
