"""Dados iniciais da demonstração Go Sell.

Monta, com as funções reais de ``database.*`` e o ERP simulado
(``sankhya_api.py``):

- usuários de cada tipo (vendedores, marketing, personalizado);
- dois eventos: a feira em andamento e um congresso já encerrado;
- catálogo vinculado ao ERP, promoções e brindes;
- carteira de clientes com recompras, vendas em todas as formas de pagamento,
  retiradas pendentes, movimentações manuais e um pedido em aberto;
- integração ERP configurada, com a fila já processada (inclusive um erro
  para mostrar o reenvio).

SQL direto só para datas retroativas e para marcar o erro "final" da fila.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

from data import erp_mock
from data.erp_mock import customer_by_key, format_cpf
from database.connection import get_conn
from database.events import (
    add_product_to_event,
    add_seller_to_event,
    create_event,
    replace_seller_event_assignment,
    set_event_operations_closed,
    update_event_goals,
    update_event_product_backorder_limit,
)
from database.event_stock import register_event_stock_entry, register_event_stock_exit
from database.gifts import create_gift_item, create_gift_rule
from database.products import create_local_product
from database.promotions import create_promotion
from database.schema import init_db
from database.sellers import create_seller_account, update_seller_account
from database.transactions import confirm_transaction_with_aut, create_transaction
from database.users import set_user_access

DEMO_PASSWORD = "demo"
EVENT_NAME = "Feira Demo Go Sell"
EVENT_BADGE = "#0e7490"
CONGRESS_NAME = "Congresso Demo Go Sell"
CONGRESS_BADGE = "#7c3aed"

_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTS_IMG = _ROOT / "static" / "images" / "products"

#: Tabelas que o reset preserva (controle de migrações do schema).
_KEEP_TABLES = {"schema_migrations", "sqlite_sequence"}


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def write_product_placeholders() -> None:
    _PRODUCTS_IMG.mkdir(parents=True, exist_ok=True)
    for p in erp_mock.CATALOG:
        path = _PRODUCTS_IMG / f"{p['sku'].lower()}.svg"
        path.write_text(
            erp_mock.product_svg(p["name"], p["sku"], p["bg"], p["accent"]), encoding="utf-8"
        )


def _image_url(sku: str) -> str:
    return f"/static/images/products/{sku.lower()}.svg"


def _wipe_all() -> None:
    """Apaga todas as tabelas de dados (e as fotos que o ERP simulado gravou)."""
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            if table not in _KEEP_TABLES:
                conn.execute(f'DELETE FROM "{table}"')
        try:
            conn.execute("DELETE FROM sqlite_sequence")
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys = ON")
    for img in _PRODUCTS_IMG.glob("erp-*"):
        try:
            img.unlink()
        except OSError:
            pass


def _shift_transaction_created_at(tx_id: int, when: datetime) -> None:
    stamp = when.isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("UPDATE transactions SET created_at = ? WHERE id = ?", (stamp, int(tx_id)))
        conn.execute(
            "UPDATE stock_movements SET created_at = ? WHERE transaction_id = ?",
            (stamp, int(tx_id)),
        )


def _shift_last_movement(event_id: int, product_id: int, when: datetime) -> None:
    stamp = when.isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(id) AS id FROM stock_movements WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(product_id)),
        ).fetchone()
        if row and row["id"]:
            conn.execute(
                "UPDATE stock_movements SET created_at = ? WHERE id = ?", (stamp, int(row["id"]))
            )


@contextmanager
def _fast_erp():
    """Sem a espera simulada do ERP enquanto o seed processa a fila."""
    old = os.environ.get("DEMO_ERP_LATENCY")
    os.environ["DEMO_ERP_LATENCY"] = "0"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DEMO_ERP_LATENCY", None)
        else:
            os.environ["DEMO_ERP_LATENCY"] = old


# ---------------------------------------------------------------------------
# Blocos do seed
# ---------------------------------------------------------------------------

def _create_users() -> dict:
    pw = generate_password_hash(DEMO_PASSWORD)
    specs = [
        ("ana", "Ana Costa", 11),
        ("bruno", "Bruno Lima", 12),
        ("marina", "Marina Rocha", None),
        ("carlos", "Carlos Teixeira", None),
    ]
    users = {}
    for username, name, codvend in specs:
        row = create_seller_account(name, username, pw, erp_codvend=codvend)
        update_seller_account(
            int(row["id"]),
            name=name,
            username=username,
            active=True,
            email=f"{username}@gosell.demo",
            erp_codvend=codvend,
        )
        users[username] = row
    return users


def _setup_user_access(users: dict, event_id: int) -> None:
    set_user_access(int(users["ana"]["id"]), role="vendedor")
    set_user_access(int(users["bruno"]["id"]), role="vendedor")
    set_user_access(int(users["marina"]["id"]), role="marketing", event_ids=[event_id])
    set_user_access(
        int(users["carlos"]["id"]),
        role="personalizado",
        permissions=["estoque", "transacoes_ver", "dashboard"],
        event_ids=[event_id],
    )


def _create_products() -> dict:
    write_product_placeholders()
    by_sku: dict = {}
    for spec in erp_mock.CATALOG:
        created = create_local_product(
            sku=spec["sku"],
            name=spec["name"],
            category=spec["category"],
            price=spec["price"],
            image=_image_url(spec["sku"]),
            min_stock=spec["min_stock"],
            brand=spec["brand"],
            erp_codprod=spec["codprod"],
            erp_codvol=spec["codvol"],
        )
        by_sku[spec["sku"]] = {**spec, "id": int(created["id"])}
    return by_sku


def _link_products(event_id: int, by_sku: dict, *, stock_factor: float = 1.0) -> None:
    for spec in by_sku.values():
        stock = int(round(spec["stock"] * stock_factor))
        add_product_to_event(
            event_id,
            spec["id"],
            stock,
            spec["min_stock"],
            link_audit_reason="Estoque inicial da demonstração",
            created_by="seed",
        )
        # Permite vender além do saldo (vira "retirada pendente").
        update_event_product_backorder_limit(event_id, spec["id"], 10)


def _create_promotions(event_id: int, p: dict) -> None:
    create_promotion(event_id, "15% na luva nitrilo", "percent",
                     rule_value=15, product_ids=[p["GS-CS-001"]["id"]])
    create_promotion(event_id, "R$ 10 off na resina A2", "fixed",
                     rule_value=10, product_ids=[p["GS-CS-002"]["id"]])
    create_promotion(event_id, "Fio dental a partir de 3 un. — R$ 10 cada", "min_bundle",
                     rule_value=10, min_qty=3, product_ids=[p["GS-CS-004"]["id"]])
    create_promotion(
        event_id, "Compre 1 óculos, leve 1 espelho", "bogo",
        min_qty=1, free_qty=1,
        product_ids=[p["GS-AC-001"]["id"], p["GS-AC-003"]["id"]],
        bogo_buy_product_id=p["GS-AC-001"]["id"],
        bogo_free_product_id=p["GS-AC-003"]["id"],
    )
    create_promotion(event_id, "Na compra de 2 aventais", "exact_bundle",
                     rule_value=50, min_qty=2, product_ids=[p["GS-AC-002"]["id"]])


def _create_gifts(event_id: int, p: dict) -> None:
    caneca = create_gift_item(event_id, "Caneca Go Sell", stock=30,
                              description="Caneca térmica com a marca do estande.")
    create_gift_item(event_id, "Ecobag Go Sell", stock=50)
    create_gift_rule(
        event_id, "Pedidos a partir de R$ 500 ganham uma caneca", "min_total",
        min_value=500, gift_item_ids=[int(caneca["id"])], gift_qty=1,
    )
    create_gift_rule(
        event_id, "A cada 3 caixas de luva, 1 espelho clínico", "kit",
        min_qty=3, gift_product_ids=[p["GS-AC-003"]["id"]], gift_qty=1,
        max_per_order=3, product_ids=[p["GS-CS-001"]["id"]],
    )


def _setup_erp() -> None:
    """Configura a integração e faz a primeira sincronização com o ERP simulado."""
    import erp_sync
    import sankhya_api
    from database import erp as erp_db

    sankhya_api.rearm_queues(arm_flaky=True)
    erp_db.save_erp_settings({
        "codemp": str(erp_mock.CODEMP),
        "codvend": "10",
        "codlocalorig": str(erp_mock.CODLOCAL),
        "orders_enabled": "1",
    })
    with _fast_erp():
        erp_sync.sync_catalog()
    grid = erp_db.payment_type_grid()
    erp_db.save_payment_types([
        {
            "payment_method": row["payment_method"],
            "installments": row["installments"],
            "codtipvenda": row["suggested"],
        }
        for row in grid
        if row["suggested"]
    ])
    for c in erp_mock.all_erp_customers():
        erp_db.save_customer_codparc(c["cpf"], int(c["codparc"]), c["name"])
    # Novidades para a primeira sincronização feita na apresentação.
    sankhya_api.stage_followup_updates()


def _client_kwargs(key: str | None) -> dict:
    if key is None:
        return {}
    c = customer_by_key(key)
    return {
        "client_name": c["name"],
        "client_cpf": format_cpf(c["cpf"]),
        "client_email": c["email"],
        "client_phone": f"({c['phone'][:2]}) {c['phone'][2:7]}-{c['phone'][7:]}",
        "client_zipcode": f"{c['zipcode'][:5]}-{c['zipcode'][5:]}",
        "client_address": c["address"],
        "client_number": c["number"],
        "client_complement": c["complement"] or None,
        "client_neighborhood": c["neighborhood"],
        "client_city": c["city"],
        "client_state": c["state"],
    }


def _sell(event_id: int, seller: dict, sale: dict, by_sku: dict, now: datetime) -> int:
    payload = [
        {
            "id": by_sku[sku]["id"],
            "nome": by_sku[sku]["name"],
            "preco": by_sku[sku]["price"],
            "quantidade": qty,
            "sku": sku,
            "categoria": by_sku[sku]["category"],
        }
        for sku, qty in sale["items"]
    ]
    client = _client_kwargs(sale.get("client"))
    if sale.get("anon"):
        client = {"client_name": sale["anon"]}
    pm = sale["payment"]
    tx = create_transaction(
        payload,
        created_by=f"vendedor:{seller['name']}",
        seller_id=int(seller["id"]),
        seller_name=seller["name"],
        event_id=event_id,
        payment_method=pm,
        card_installments=sale.get("installments", 1) if pm == "credito" else None,
        **client,
    )
    when = now - timedelta(days=sale["days_ago"], hours=sale.get("hours_ago", 0))
    if sale.get("pending"):
        _shift_transaction_created_at(int(tx["id"]), when)
        return int(tx["id"])
    aut = sale.get("aut") or f"{pm[:3].upper()}{4100 + int(tx['id']) * 37}"
    confirm_transaction_with_aut(int(tx["id"]), aut, created_by="seed")
    _shift_transaction_created_at(int(tx["id"]), when)
    return int(tx["id"])


#: Vendas do congresso (encerrado, ~45 dias atrás).
_CONGRESS_SALES = [
    {"seller": "ana", "client": "carla", "payment": "credito", "installments": 3,
     "items": [("GS-EQ-003", 1), ("GS-CS-001", 4)], "days_ago": 47, "hours_ago": 5},
    {"seller": "bruno", "client": "rafael", "payment": "pix",
     "items": [("GS-AC-001", 2), ("GS-AC-003", 2)], "days_ago": 47, "hours_ago": 3},
    {"seller": "ana", "client": "paulo", "payment": "faturado",
     "items": [("GS-EQ-002", 1)], "days_ago": 46, "hours_ago": 6},
    {"seller": "bruno", "client": "fernanda", "payment": "debito",
     "items": [("GS-CS-004", 6), ("GS-AC-002", 2)], "days_ago": 46, "hours_ago": 2},
    {"seller": "ana", "client": "juliana", "payment": "credito", "installments": 10,
     "items": [("GS-EQ-001", 1)], "days_ago": 45, "hours_ago": 7},
    {"seller": "bruno", "client": "thiago", "payment": "dinheiro",
     "items": [("GS-CS-002", 3)], "days_ago": 45, "hours_ago": 4},
    {"seller": "ana", "client": "helena", "payment": "pix",
     "items": [("GS-CS-001", 2), ("GS-CS-004", 3)], "days_ago": 45, "hours_ago": 1},
]

#: Vendas da feira em andamento (últimos 8 dias).
_FAIR_SALES = [
    {"seller": "ana", "client": "carla", "payment": "pix",
     "items": [("GS-CS-001", 3)], "days_ago": 8, "hours_ago": 6},
    {"seller": "bruno", "client": "rafael", "payment": "credito", "installments": 1,
     "items": [("GS-AC-001", 4)], "days_ago": 7, "hours_ago": 5},
    {"seller": "ana", "client": "helena", "payment": "credito", "installments": 6,
     "items": [("GS-EQ-002", 1)], "days_ago": 7, "hours_ago": 2},
    {"seller": "bruno", "client": "paulo", "payment": "debito",
     "items": [("GS-EQ-003", 1), ("GS-AC-002", 2)], "days_ago": 6, "hours_ago": 7},
    {"seller": "ana", "client": "juliana", "payment": "pix",
     "items": [("GS-CS-004", 3)], "days_ago": 6, "hours_ago": 3},
    {"seller": "bruno", "anon": "Cliente balcão", "payment": "dinheiro",
     "items": [("GS-AC-003", 2), ("GS-CS-004", 1)], "days_ago": 5, "hours_ago": 6},
    {"seller": "ana", "client": "thiago", "payment": "credito", "installments": 12,
     "items": [("GS-EQ-001", 1)], "days_ago": 5, "hours_ago": 2},
    {"seller": "bruno", "client": "fernanda", "payment": "faturado",
     "items": [("GS-CS-001", 6), ("GS-AC-002", 2)], "days_ago": 4, "hours_ago": 8},
    {"seller": "ana", "client": "carla", "payment": "credito", "installments": 2,
     "items": [("GS-EQ-003", 1)], "days_ago": 4, "hours_ago": 3},
    {"seller": "bruno", "client": "eduardo", "payment": "pix",
     "items": [("GS-AC-001", 2), ("GS-CS-003", 2)], "days_ago": 3, "hours_ago": 6},
    {"seller": "ana", "client": "rafael", "payment": "debito",
     "items": [("GS-CS-002", 5)], "days_ago": 3, "hours_ago": 2},
    {"seller": "bruno", "client": "helena", "payment": "pix",
     "items": [("GS-CS-001", 2), ("GS-CS-004", 4)], "days_ago": 2, "hours_ago": 7},
    {"seller": "ana", "client": "paulo", "payment": "credito", "installments": 4,
     "items": [("GS-EQ-002", 1), ("GS-AC-003", 2)], "days_ago": 2, "hours_ago": 4},
    {"seller": "bruno", "anon": "Cliente balcão", "payment": "dinheiro",
     "items": [("GS-AC-002", 1)], "days_ago": 1, "hours_ago": 8},
    {"seller": "ana", "client": "juliana", "payment": "credito", "installments": 1,
     "items": [("GS-CS-001", 3), ("GS-AC-001", 1)], "days_ago": 1, "hours_ago": 5},
    {"seller": "bruno", "client": "carla", "payment": "pix",
     "items": [("GS-CS-004", 6)], "days_ago": 1, "hours_ago": 2},
    {"seller": "ana", "client": "eduardo", "payment": "debito",
     "items": [("GS-AC-003", 3)], "days_ago": 0, "hours_ago": 5},
    {"seller": "bruno", "client": "thiago", "payment": "pix",
     "items": [("GS-CS-001", 2)], "days_ago": 0, "hours_ago": 3},
    # Cliente que o ERP simulado recusa na 1ª tentativa (fila com erro).
    {"seller": "ana", "client": "beatriz", "payment": "credito", "installments": 5,
     "items": [("GS-EQ-003", 1), ("GS-CS-002", 2)], "days_ago": 0, "hours_ago": 2},
]

#: Vendas de hoje que ficam na fila do ERP (o worker envia ao abrir o sistema).
_QUEUED_SALES = [
    {"seller": "bruno", "client": "fernanda", "payment": "pix",
     "items": [("GS-AC-001", 1), ("GS-AC-003", 1)], "days_ago": 0, "hours_ago": 1},
]


def _process_erp_queue() -> None:
    """Envia a fila ao ERP simulado e deixa o erro do cliente "em análise" visível."""
    import erp_sync
    from database import erp as erp_db

    with _fast_erp():
        while erp_sync.process_outbox_once()["processed"]:
            pass
    flaky_cpf = format_cpf(customer_by_key(erp_mock.FLAKY_CUSTOMER_KEY)["cpf"])
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT o.* FROM erp_outbox o JOIN transactions t ON t.id = o.transaction_id
             WHERE o.status = 'pendente' AND o.last_error IS NOT NULL AND t.client_cpf = ?
            """,
            (flaky_cpf,),
        ).fetchall()
    for row in rows:
        job = dict(row)
        job["attempts"] = 0
        erp_db.mark_job_failed(job, job["last_error"], transient=False, final=True)


def _manual_movements(event_id: int, by_sku: dict, now: datetime) -> None:
    luva = by_sku["GS-CS-001"]["id"]
    avental = by_sku["GS-AC-002"]["id"]
    sugador = by_sku["GS-CS-003"]["id"]
    register_event_stock_entry(event_id, luva, 24, reason="Reposição do CD (NF 4471)",
                               created_by="admin")
    _shift_last_movement(event_id, luva, now - timedelta(days=3, hours=9))
    register_event_stock_entry(event_id, sugador, 6, reason="Reposição do CD (NF 4471)",
                               created_by="admin")
    _shift_last_movement(event_id, sugador, now - timedelta(days=3, hours=9))
    register_event_stock_exit(event_id, avental, 2, reason="Avaria no transporte",
                              created_by="admin")
    _shift_last_movement(event_id, avental, now - timedelta(days=2, hours=10))


def apply_demo_seed() -> dict:
    """Popula o banco com eventos, usuários, catálogo, vendas e a integração ERP."""
    now = datetime.now()
    users = _create_users()
    by_sku = _create_products()

    # Configuração do ERP antes das vendas: cada venda confirmada entra na fila.
    _setup_erp()

    # Congresso encerrado (histórico de clientes e vendedores).
    congress_id = create_event(
        CONGRESS_NAME,
        "Congresso regional atendido pela equipe Go Sell (encerrado).",
        badge_color=CONGRESS_BADGE,
        operation_type="congresso",
    )
    update_event_goals(congress_id, revenue_goal=40000.0, volume_goal=60)
    _link_products(congress_id, by_sku, stock_factor=0.5)
    for username in ("ana", "bruno"):
        add_seller_to_event(congress_id, int(users[username]["id"]))
    for sale in _CONGRESS_SALES:
        _sell(congress_id, users[sale["seller"]], sale, by_sku, now)
    set_event_operations_closed(congress_id, True)

    # Feira em andamento.
    event_id = create_event(
        EVENT_NAME,
        "Evento de demonstração do Go Sell para reuniões comerciais.",
        badge_color=EVENT_BADGE,
        operation_type="evento",
    )
    update_event_goals(event_id, revenue_goal=60000.0, volume_goal=200)
    _link_products(event_id, by_sku)
    for username in ("ana", "bruno"):
        replace_seller_event_assignment(int(users[username]["id"]), event_id)
    _setup_user_access(users, event_id)
    _create_promotions(event_id, by_sku)
    _create_gifts(event_id, by_sku)
    _manual_movements(event_id, by_sku, now)

    confirmed = len(_CONGRESS_SALES)
    for sale in _FAIR_SALES:
        _sell(event_id, users[sale["seller"]], sale, by_sku, now)
        confirmed += 1
    _process_erp_queue()
    for sale in _QUEUED_SALES:
        _sell(event_id, users[sale["seller"]], sale, by_sku, now)
        confirmed += 1

    # Pedido em aberto (para mostrar a retomada do checkout).
    _sell(event_id, users["ana"], {
        "client": None, "anon": "Pedido em aberto (demo)", "payment": "pix",
        "items": [("GS-EQ-001", 1)], "days_ago": 0, "pending": True,
    }, by_sku, now)

    return {
        "event_id": event_id,
        "products": len(erp_mock.CATALOG),
        "users": len(users),
        "confirmed_sales": confirmed,
        "pending_sales": 1,
    }


def demo_already_seeded() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE name = ? LIMIT 1", (EVENT_NAME,)
        ).fetchone()
    return row is not None


def ensure_demo_data() -> None:
    """Garante o seed na primeira subida (banco vazio)."""
    if demo_already_seeded():
        write_product_placeholders()
        return
    # Banco vazio (ou seed interrompido no meio): começa do zero.
    reset_demo_data()


def reset_demo_data() -> dict:
    """Apaga tudo e recria o estado inicial da demonstração."""
    _wipe_all()
    init_db()
    return apply_demo_seed()
