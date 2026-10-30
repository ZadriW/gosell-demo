"""Initial DDL and idempotent migrations."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime


from . import connection
from .connection import DEFAULT_MIN_STOCK, _now_iso, get_conn
from .sku_helpers import _default_sku_for_id
from .sqlutil import sql_ident

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    sku          TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    description  TEXT,
    price        REAL    NOT NULL DEFAULT 0,
    image        TEXT,
    stock        INTEGER NOT NULL DEFAULT 0,
    min_stock    INTEGER NOT NULL DEFAULT 5,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE (sku)
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number    TEXT    UNIQUE NOT NULL,
    created_at      TEXT    NOT NULL,
    total           REAL    NOT NULL,
    items_count     INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'confirmado',
    client_name     TEXT,
    client_cpf      TEXT,
    client_zipcode  TEXT,
    client_address  TEXT,
    client_number   TEXT,
    client_complement TEXT,
    client_city     TEXT,
    client_state    TEXT,
    seller_id       INTEGER,
    seller_name     TEXT,
    FOREIGN KEY (seller_id) REFERENCES sellers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    product_id     TEXT,
    product_name   TEXT    NOT NULL,
    category       TEXT,
    unit_price     REAL    NOT NULL,
    quantity       INTEGER NOT NULL,
    subtotal       REAL    NOT NULL,
    product_sku    TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL,
    movement_type  TEXT    NOT NULL
        CHECK (movement_type IN ('entrada','saida','venda','ajuste','inicial')),
    quantity       INTEGER NOT NULL,
    delta          INTEGER NOT NULL,
    balance_after  INTEGER NOT NULL,
    unit_cost      REAL,
    reason         TEXT,
    reference      TEXT,
    transaction_id INTEGER,
    created_by     TEXT,
    created_at     TEXT    NOT NULL,
    FOREIGN KEY (product_id)     REFERENCES products(id),
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sellers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    email          TEXT    UNIQUE NOT NULL,
    username       TEXT    UNIQUE,
    password_hash  TEXT    NOT NULL,
    pin_hash       TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    description  TEXT,
    badge_color  TEXT,
    operation_type TEXT NOT NULL DEFAULT 'evento',
    revenue_goal REAL,
    volume_goal  INTEGER,
    operations_closed INTEGER NOT NULL DEFAULT 0,
    operations_closed_at TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS event_products (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL,
    product_id       INTEGER NOT NULL,
    stock            INTEGER NOT NULL DEFAULT 0,
    min_stock        INTEGER NOT NULL DEFAULT 5,
    backorder_limit  INTEGER NOT NULL DEFAULT -1,
    price            REAL,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    UNIQUE (event_id, product_id),
    FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category);
CREATE INDEX IF NOT EXISTS idx_stock_movements_product_created
    ON stock_movements(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_at
    ON stock_movements(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sellers_email
    ON sellers(email);
CREATE INDEX IF NOT EXISTS idx_event_products_event
    ON event_products(event_id);

CREATE TABLE IF NOT EXISTS checkout_holds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL,
    seller_id    INTEGER NOT NULL,
    seller_name  TEXT    NOT NULL DEFAULT 'Vendedor',
    product_id   INTEGER NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE (event_id, seller_id, product_id),
    FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
    FOREIGN KEY (seller_id)  REFERENCES sellers(id)  ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_checkout_holds_event_product
    ON checkout_holds(event_id, product_id);
CREATE INDEX IF NOT EXISTS idx_checkout_holds_updated
    ON checkout_holds(updated_at);

CREATE TABLE IF NOT EXISTS checkout_hold_sync (
    event_id  INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    sync_seq  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_id, seller_id)
);

CREATE TABLE IF NOT EXISTS promotions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    rule_type   TEXT    NOT NULL
        CHECK (rule_type IN ('percent', 'fixed', 'bogo', 'min_bundle', 'exact_bundle', 'combo_bundle')),
    rule_value  REAL    NOT NULL DEFAULT 0,
    min_qty     INTEGER NOT NULL DEFAULT 1,
    free_qty    INTEGER NOT NULL DEFAULT 0,
    bogo_buy_product_id  INTEGER,
    bogo_free_product_id INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS promotion_products (
    promotion_id INTEGER NOT NULL,
    product_id   INTEGER NOT NULL,
    PRIMARY KEY (promotion_id, product_id),
    FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id)   REFERENCES products(id)  ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotions_event
    ON promotions(event_id, active);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    ident = sql_ident(table)
    return {r[1] for r in conn.execute(f"PRAGMA table_info({ident})").fetchall()}

def _ensure_products_sku_column(conn: sqlite3.Connection) -> None:
    """Bases antigas: adiciona ``sku``; preenche valores; garante índice único."""
    cols = _table_columns(conn, "products")
    if "sku" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN sku TEXT")
    for row in conn.execute("SELECT id, sku FROM products").fetchall():
        pid = int(row["id"])
        s = (row["sku"] or "").strip() if row["sku"] is not None else ""
        if not s:
            conn.execute(
                "UPDATE products SET sku = ?, updated_at = ? WHERE id = ?",
                (_default_sku_for_id(pid), _now_iso(), pid),
            )
    # Índice único (não conflita com UNIQUE de tabelas novas — idempotente)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku ON products(sku)")


def _ensure_transaction_items_product_sku_column(conn: sqlite3.Connection) -> None:
    if "product_sku" in _table_columns(conn, "transaction_items"):
        return
    conn.execute("ALTER TABLE transaction_items ADD COLUMN product_sku TEXT")


def _ensure_transactions_payment_method(conn: sqlite3.Connection) -> None:
    if "payment_method" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT")


def _ensure_transactions_card_installments(conn: sqlite3.Connection) -> None:
    if "card_installments" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN card_installments INTEGER")


def _ensure_transactions_aut(conn: sqlite3.Connection) -> None:
    if "aut" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN aut TEXT")


def _ensure_transactions_event_id(conn: sqlite3.Connection) -> None:
    """Adiciona event_id em transactions para rastrear vendas de eventos."""
    if "event_id" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN event_id INTEGER")


def _ensure_transactions_client_columns(conn: sqlite3.Connection) -> None:
    """Adiciona colunas de dados do cliente/vendedor em transactions."""
    cols = _table_columns(conn, "transactions")
    client_fields = [
        "client_name", "client_cpf", "client_email", "client_phone",
        "client_zipcode", "client_address",
        "client_number", "client_complement", "client_city", "client_state"
    ]
    for field in client_fields:
        if field not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {sql_ident(field)} TEXT")
    if "seller_id" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN seller_id INTEGER")
    if "seller_name" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN seller_name TEXT")


def _ensure_transactions_cro_columns(conn: sqlite3.Connection) -> None:
    """Garante colunas de UF/número CRO e campos legados (categoria/validação) em transactions."""
    cols = _table_columns(conn, "transactions")
    cro_fields = {
        "client_cro_uf": "TEXT",
        "client_cro_numero": "TEXT",
        "client_cro_categoria": "TEXT",
        "client_cro_validated": "INTEGER DEFAULT 0",
        "client_cro_validation_data": "TEXT",
    }
    for field, ddl in cro_fields.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {sql_ident(field)} {ddl}")


def _ensure_events_tables(conn: sqlite3.Connection) -> None:
    """Cria tabelas de eventos caso a base seja anterior à sua introdução."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            description  TEXT,
            badge_color  TEXT,
            operation_type TEXT NOT NULL DEFAULT 'evento',
            revenue_goal REAL,
            volume_goal  INTEGER,
            operations_closed INTEGER NOT NULL DEFAULT 0,
            operations_closed_at TEXT,
            active       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_products (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id         INTEGER NOT NULL,
            product_id       INTEGER NOT NULL,
            stock            INTEGER NOT NULL DEFAULT 0,
            min_stock        INTEGER NOT NULL DEFAULT 5,
            backorder_limit  INTEGER NOT NULL DEFAULT -1,
            price            REAL,
            created_at       TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL,
            UNIQUE (event_id, product_id),
            FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_event_products_event
            ON event_products(event_id);
    """)


def _ensure_events_badge_color(conn: sqlite3.Connection) -> None:
    """Acrescenta ``badge_color`` (hex) em ``events`` em bases antigas."""
    if "badge_color" in _table_columns(conn, "events"):
        return
    conn.execute("ALTER TABLE events ADD COLUMN badge_color TEXT")


def _ensure_events_goals(conn: sqlite3.Connection) -> None:
    """Metas comerciais do evento: faturamento (R$) e volume (unidades)."""
    cols = _table_columns(conn, "events")
    if "revenue_goal" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN revenue_goal REAL")
    if "volume_goal" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN volume_goal INTEGER")


def _ensure_events_operations_closed(conn: sqlite3.Connection) -> None:
    """Modo consulta: evento permanece ativo, mas sem vendas nem edições."""
    cols = _table_columns(conn, "events")
    if "operations_closed" not in cols:
        conn.execute(
            "ALTER TABLE events ADD COLUMN operations_closed INTEGER NOT NULL DEFAULT 0"
        )
    if "operations_closed_at" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN operations_closed_at TEXT")


def _ensure_events_operation_type(conn: sqlite3.Connection) -> None:
    """Classificação operacional: evento, congresso ou stand."""
    cols = _table_columns(conn, "events")
    if "operation_type" in cols:
        return
    conn.execute(
        "ALTER TABLE events ADD COLUMN operation_type TEXT NOT NULL DEFAULT 'evento'"
    )


def _ensure_checkout_holds_table(conn: sqlite3.Connection) -> None:
    """Reservas temporárias de carrinho na tela de pagamento (bases antigas)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS checkout_holds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     INTEGER NOT NULL,
            seller_id    INTEGER NOT NULL,
            seller_name  TEXT    NOT NULL DEFAULT 'Vendedor',
            product_id   INTEGER NOT NULL,
            quantity     INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT    NOT NULL DEFAULT '',
            updated_at   TEXT    NOT NULL,
            UNIQUE (event_id, seller_id, product_id),
            FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
            FOREIGN KEY (seller_id)  REFERENCES sellers(id)  ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_checkout_holds_event_product
            ON checkout_holds(event_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_checkout_holds_updated
            ON checkout_holds(updated_at);
        CREATE TABLE IF NOT EXISTS checkout_hold_sync (
            event_id  INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            sync_seq  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, seller_id)
        );
    """)
    cols = _table_columns(conn, "checkout_holds")
    if "created_at" not in cols:
        conn.execute("ALTER TABLE checkout_holds ADD COLUMN created_at TEXT")
        cols = _table_columns(conn, "checkout_holds")
    if "created_at" in cols:
        conn.execute(
            """
            UPDATE checkout_holds
               SET created_at = updated_at
             WHERE created_at IS NULL OR TRIM(created_at) = ''
            """
        )


def _ensure_event_products_price(conn: sqlite3.Connection) -> None:
    """Preço de venda do produto no evento. ``NULL`` herda ``products.price``."""
    if "price" in _table_columns(conn, "event_products"):
        return
    conn.execute("ALTER TABLE event_products ADD COLUMN price REAL")


def _ensure_event_products_backorder_limit(conn: sqlite3.Connection) -> None:
    """Acrescenta ``backorder_limit`` em ``event_products`` em bases antigas.

    ``-1`` (padrão/"sem limite") permite entrega pendente livremente.
    ``0`` bloqueia qualquer entrega pendente do produto no evento.
    Um valor ``> 0`` é o total de unidades pendentes permitidas.
    """
    if "backorder_limit" not in _table_columns(conn, "event_products"):
        conn.execute(
            "ALTER TABLE event_products ADD COLUMN backorder_limit INTEGER NOT NULL DEFAULT -1"
        )
    _ensure_schema_migrations_table(conn)
    migration_name = "event_products_backorder_limit_unlimited_sentinel"
    if _migration_applied(conn, migration_name):
        return
    # A coluna foi introduzida com ``0`` significando "sem limite"; agora ``0``
    # passa a significar "bloqueado", então valores antigos de ``0`` precisam
    # ser convertidos para o novo sentinel de "sem limite" (``-1``).
    conn.execute("UPDATE event_products SET backorder_limit = -1 WHERE backorder_limit = 0")
    _mark_migration_applied(conn, migration_name)


def _ensure_event_products_backorder_omit_insert_unlimited(conn: sqlite3.Connection) -> None:
    """INSERTs antigos omitiam ``backorder_limit`` e o SQLite usava DEFAULT 0.

    Nesta base a coluna foi criada com ``DEFAULT 0`` (quando 0 ainda significava
    'sem limite'). Depois 0 passou a bloquear vendas futuras, então produtos
    importados por planilha (estoque 0 + limite 0) apareciam como bloqueados.
    """
    _ensure_schema_migrations_table(conn)
    migration_name = "event_products_backorder_omit_insert_unlimited"
    if _migration_applied(conn, migration_name):
        return
    conn.execute("UPDATE event_products SET backorder_limit = -1 WHERE backorder_limit = 0")
    _mark_migration_applied(conn, migration_name)


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name        TEXT PRIMARY KEY,
            applied_at  TEXT NOT NULL
        )
        """
    )


def _migration_applied(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,),
    ).fetchone()
    return row is not None


def _mark_migration_applied(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, _now_iso()),
    )


def _ensure_event_extensions(conn: sqlite3.Connection) -> None:
    """Adiciona event_id a stock_movements e cria event_sellers (idempotente)."""
    # Adicionar event_id a stock_movements se ainda não existir
    sm_cols = _table_columns(conn, "stock_movements")
    if "event_id" not in sm_cols:
        conn.execute(
            "ALTER TABLE stock_movements ADD COLUMN event_id INTEGER "
            "REFERENCES events(id) ON DELETE SET NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_event "
        "ON stock_movements(event_id)"
    )
    # Tabela de associação evento ↔ vendedor
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS event_sellers (
            event_id   INTEGER NOT NULL,
            seller_id  INTEGER NOT NULL,
            added_at   TEXT    NOT NULL,
            PRIMARY KEY (event_id, seller_id),
            FOREIGN KEY (event_id)  REFERENCES events(id)  ON DELETE CASCADE,
            FOREIGN KEY (seller_id) REFERENCES sellers(id) ON DELETE CASCADE
        );
    """)
    _ensure_event_sellers_one_event_per_seller(conn)


def _ensure_event_sellers_one_event_per_seller(conn: sqlite3.Connection) -> None:
    """Remove vínculos duplicados por vendedor e garante índice único em ``seller_id``.

    Regra de negócio: cada vendedor pode estar associado a no máximo um evento.
    Em caso de histórico inconsistente, mantém o vínculo mais recente (``added_at``).
    """
    dup_rows = conn.execute(
        """
        SELECT seller_id FROM event_sellers GROUP BY seller_id HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in dup_rows:
        sid = int(row["seller_id"])
        keep = conn.execute(
            """
            SELECT rowid FROM event_sellers
             WHERE seller_id = ?
             ORDER BY datetime(added_at) DESC, event_id DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if not keep:
            continue
        conn.execute(
            "DELETE FROM event_sellers WHERE seller_id = ? AND rowid != ?",
            (sid, int(keep["rowid"])),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_event_sellers_seller_unique "
        "ON event_sellers(seller_id)"
    )


def _ensure_transaction_items_promo_columns(conn: sqlite3.Connection) -> None:
    """Adiciona colunas de auditoria de promoção em transaction_items (idempotente)."""
    cols = _table_columns(conn, "transaction_items")
    if "original_price" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN original_price REAL")
    if "promotion_id" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN promotion_id INTEGER")


def _ensure_delivery_columns(conn: sqlite3.Connection) -> None:
    """Colunas de controle de entrega por item (venda com estoque parcial).

    - ``transaction_items.quantity_delivered``: unidades já entregues (estoque baixado).
    - ``transactions.delivery_status``: ``completa`` | ``parcial`` | ``pendente``.

    Backfill: transações já confirmadas/estornadas antes desta migração tiveram
    baixa integral de estoque, logo ``quantity_delivered = quantity``.
    """
    ti_cols = _table_columns(conn, "transaction_items")
    tx_cols = _table_columns(conn, "transactions")
    is_new = "quantity_delivered" not in ti_cols
    if is_new:
        conn.execute(
            "ALTER TABLE transaction_items "
            "ADD COLUMN quantity_delivered INTEGER NOT NULL DEFAULT 0"
        )
    if "delivery_status" not in tx_cols:
        conn.execute(
            "ALTER TABLE transactions "
            "ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'completa'"
        )
    if is_new:
        conn.execute(
            """
            UPDATE transaction_items
               SET quantity_delivered = quantity
             WHERE transaction_id IN (
                       SELECT id FROM transactions
                        WHERE status IN ('confirmado', 'estornado')
                   )
            """
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_delivery_status "
        "ON transactions(event_id, delivery_status)"
    )


def _ensure_transactions_receipt_note(conn: sqlite3.Connection) -> None:
    """Texto livre impresso no rodapé da nota não fiscal."""
    cols = _table_columns(conn, "transactions")
    if "receipt_note" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN receipt_note TEXT")


def _ensure_transactions_handover_status(conn: sqlite3.Connection) -> None:
    """Confirmação geral de entrega do pedido (retirada no balcão pelo cliente).

    - ``transactions.handover_status``: ``pendente`` | ``entregue``.
    - ``transactions.handover_confirmed_at``: data/hora da confirmação.

    Independente do controle de retirada pendente por item (``delivery_status``
    / ``quantity_delivered``), que continua exigindo baixa de estoque. Este
    campo apenas sinaliza, para fins de acompanhamento, que o pedido como um
    todo já foi entregue ao cliente.
    """
    cols = _table_columns(conn, "transactions")
    if "handover_status" not in cols:
        conn.execute(
            "ALTER TABLE transactions "
            "ADD COLUMN handover_status TEXT NOT NULL DEFAULT 'pendente'"
        )
    if "handover_confirmed_at" not in cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN handover_confirmed_at TEXT"
        )


def _ensure_sellers_columns(conn: sqlite3.Connection) -> None:
    """Migrações leves para contas de vendedores."""
    cols = _table_columns(conn, "sellers")
    for field, ddl in {
        "name": "TEXT NOT NULL DEFAULT 'Vendedor'",
        "email": "TEXT",
        "username": "TEXT",
        "password_hash": "TEXT",
        "pin_hash": "TEXT",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "last_login_at": "TEXT",
    }.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE sellers ADD COLUMN {sql_ident(field)} {ddl}")
    used: set[str] = set()
    rows = conn.execute("SELECT id, email, username FROM sellers").fetchall()
    for row in rows:
        current = (row["username"] or "").strip().lower()
        if current:
            used.add(current)
            continue
        email = (row["email"] or "").strip().lower()
        base = email.split("@", 1)[0] if "@" in email else email
        base = "".join(ch for ch in base if ch.isalnum() or ch in "._-") or f"vendedor{int(row['id'])}"
        if len(base) < 3:
            base = f"{base}{int(row['id'])}"
        candidate = base[:40]
        n = 2
        while candidate in used:
            suffix = str(n)
            candidate = f"{base[: max(1, 40 - len(suffix))]}{suffix}"
            n += 1
        used.add(candidate)
        conn.execute(
            "UPDATE sellers SET username = ? WHERE id = ?",
            (candidate, int(row["id"])),
        )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_email ON sellers(email)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_username ON sellers(username)")
    # Código do vendedor no ERP: um por vendedor, nunca compartilhado.
    if "erp_codvend" not in cols:
        conn.execute("ALTER TABLE sellers ADD COLUMN erp_codvend INTEGER")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_erp_codvend "
        "ON sellers(erp_codvend) WHERE erp_codvend IS NOT NULL"
    )


def _ensure_products_family_columns(conn: sqlite3.Connection) -> None:
    """Famílias de variantes em ``products`` (produto-pai, nome da variante, subtítulo).

    ``family_id`` é o ``id`` local do produto-pai da família. Nasceu como
    ``wake_product_id`` (``productId`` da Wake) e foi renomeada na troca para o
    ERP, preservando as famílias já existentes.
    """
    cols = _table_columns(conn, "products")
    if "family_id" not in cols:
        if "wake_product_id" in cols:
            conn.execute("ALTER TABLE products RENAME COLUMN wake_product_id TO family_id")
        else:
            conn.execute("ALTER TABLE products ADD COLUMN family_id INTEGER")
    cols = _table_columns(conn, "products")
    for field, ddl in {
        "variant_name": "TEXT",
        "main_variant": "INTEGER NOT NULL DEFAULT 0",
        "subtitle": "TEXT",
    }.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE products ADD COLUMN {sql_ident(field)} {ddl}")


#: Migração que marca a entrada do ERP (usada também para o backup prévio).
ERP_MIGRATION_NAME = "erp_sankhya_v1"


def _ensure_products_erp_columns(conn: sqlite3.Connection) -> None:
    """Vínculo do catálogo com o ERP (``CODPROD``) e dados de cadastro do ERP.

    Backfill único: o SKU herdado da Wake já é o ``CODPROD`` (6 dígitos). Os
    produtos fora desse padrão ficam sem vínculo até o admin informar o código.
    """
    cols = _table_columns(conn, "products")
    for field, ddl in {
        "erp_codprod": "INTEGER",
        "erp_codvol": "TEXT",
        "brand": "TEXT",
        "erp_group_code": "INTEGER",
        "anvisa_code": "TEXT",
        "supplier_ref": "TEXT",
        "supplier_name": "TEXT",
        "erp_synced_at": "TEXT",
        # 1 = veio do ERP sem preço e foi cadastrado inativo; a sincronização
        # o ativa quando o preço chegar (a menos que o admin mexa antes).
        "erp_awaiting_price": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE products ADD COLUMN {sql_ident(field)} {ddl}")
    _ensure_schema_migrations_table(conn)
    migration_name = "products_erp_codprod_backfill"
    if not _migration_applied(conn, migration_name):
        conn.execute(
            """
            UPDATE products SET erp_codprod = CAST(sku AS INTEGER)
             WHERE erp_codprod IS NULL
               AND sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
               AND NOT EXISTS (
                    SELECT 1 FROM products o
                     WHERE o.id != products.id AND o.sku = products.sku
               )
            """
        )
        _mark_migration_applied(conn, migration_name)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_erp_codprod "
        "ON products(erp_codprod) WHERE erp_codprod IS NOT NULL"
    )
    # Variantes já sincronizadas antes de a sincronização limpar o nome da
    # variante herdado da Wake: a fila do ERP não as reenvia.
    migration_name = "products_variant_name_from_erp"
    if not _migration_applied(conn, migration_name):
        conn.execute(
            "UPDATE products SET variant_name = NULL "
            "WHERE erp_synced_at IS NOT NULL AND variant_name IS NOT NULL"
        )
        _mark_migration_applied(conn, migration_name)


def _ensure_transactions_erp_columns(conn: sqlite3.Connection) -> None:
    """Bairro do cliente (exigido pelo ERP) e situação do envio do pedido ao ERP.

    ``erp_status``: ``nao_enviado`` · ``na_fila`` · ``enviado`` · ``erro`` ·
    ``cancelamento_pendente`` · ``cancelamento_erro`` · ``cancelado``. Vendas
    anteriores à integração ficam ``nao_enviado`` e só vão ao ERP por ação
    explícita no admin.
    """
    cols = _table_columns(conn, "transactions")
    for field, ddl in {
        "client_neighborhood": "TEXT",
        "erp_status": "TEXT NOT NULL DEFAULT 'nao_enviado'",
        "erp_codparc": "INTEGER",
        "erp_order_id": "TEXT",
        "erp_sent_at": "TEXT",
        # IDPEDIDO do ERP (inteiro): sequencial próprio, dado na 1ª vez que
        # a venda vai para a fila e nunca reaproveitado (``erp_counters``).
        "erp_idpedido": "INTEGER",
    }.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {sql_ident(field)} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_erp_status "
        "ON transactions(erp_status)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_erp_idpedido "
        "ON transactions(erp_idpedido) WHERE erp_idpedido IS NOT NULL"
    )


def _ensure_erp_tables(conn: sqlite3.Connection) -> None:
    """Configuração, mapeamentos, fila de envio e histórico da integração ERP."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS erp_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS erp_payment_types (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_method TEXT    NOT NULL,
            installments   INTEGER,
            codtipvenda    INTEGER NOT NULL,
            description    TEXT,
            updated_at     TEXT    NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_erp_payment_types_rule
            ON erp_payment_types(payment_method, COALESCE(installments, 0));

        CREATE TABLE IF NOT EXISTS erp_customers (
            cpf        TEXT PRIMARY KEY,
            codparc    INTEGER NOT NULL,
            name       TEXT,
            synced_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS erp_outbox (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kind            TEXT    NOT NULL,
            transaction_id  INTEGER NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'pendente',
            attempts        INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            payload_json    TEXT,
            response_json   TEXT,
            last_error      TEXT,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL,
            UNIQUE (kind, transaction_id),
            FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_erp_outbox_due
            ON erp_outbox(status, next_attempt_at);

        CREATE TABLE IF NOT EXISTS erp_sync_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT    NOT NULL,
            started_at   TEXT    NOT NULL,
            finished_at  TEXT,
            status       TEXT    NOT NULL,
            inserted     INTEGER NOT NULL DEFAULT 0,
            updated      INTEGER NOT NULL DEFAULT 0,
            unchanged    INTEGER NOT NULL DEFAULT 0,
            warnings     INTEGER NOT NULL DEFAULT 0,
            images       INTEGER NOT NULL DEFAULT 0,
            message      TEXT,
            error        TEXT
        );

        -- Último preço de cada CODPROD vindo da fila de preços do gateway. A
        -- fila entrega cada alteração uma vez só (depois de confirmada, some):
        -- produto que chega depois do preço dele é cadastrado com este valor.
        -- Contadores que não podem voltar atrás (o reset do totem não mexe):
        -- 'idpedido' = último IDPEDIDO entregue a uma venda.
        CREATE TABLE IF NOT EXISTS erp_counters (
            name  TEXT    PRIMARY KEY,
            value INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS erp_prices (
            codprod     INTEGER PRIMARY KEY,
            price       REAL    NOT NULL,
            received_at TEXT    NOT NULL
        );

        -- Estoque do ERP por produto × local × lote (CONTROLE), da fila
        -- /products/stock. Só serve para escolher o local/lote do item no
        -- pedido; o estoque que vale para vender no evento é o do Totem.
        -- CONTROLE ' ' (um espaço) = produto sem controle de lote.
        CREATE TABLE IF NOT EXISTS erp_stock (
            codprod     INTEGER NOT NULL,
            codlocal    INTEGER NOT NULL,
            controle    TEXT    NOT NULL,
            estoque     REAL    NOT NULL DEFAULT 0,
            reservado   REAL    NOT NULL DEFAULT 0,
            disponivel  REAL    NOT NULL DEFAULT 0,
            received_at TEXT    NOT NULL,
            PRIMARY KEY (codprod, codlocal, controle)
        );

        -- Quanto cada pedido enviado tirou de cada lote. Abatido do saldo de
        -- erp_stock até a sincronização seguinte trazer o saldo novo daquele
        -- produto (só conta o que foi gravado depois de erp_stock.received_at).
        CREATE TABLE IF NOT EXISTS erp_stock_allocations (
            transaction_id INTEGER NOT NULL,
            codprod        INTEGER NOT NULL,
            codlocal       INTEGER NOT NULL,
            controle       TEXT    NOT NULL,
            qty            REAL    NOT NULL,
            created_at     TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_erp_stock_alloc_lot
            ON erp_stock_allocations(codprod, codlocal, controle);
        CREATE INDEX IF NOT EXISTS idx_erp_stock_alloc_tx
            ON erp_stock_allocations(transaction_id);
    """)
    conn.execute(
        "INSERT OR IGNORE INTO erp_settings (key, value, updated_at) VALUES ('codemp', '1', ?)",
        (_now_iso(),),
    )
    _ensure_schema_migrations_table(conn)
    _mark_migration_applied(conn, ERP_MIGRATION_NAME)
    # O checkout separou "Cartão" em crédito e débito: os códigos já cadastrados
    # por parcela eram do crédito (débito ganhou código próprio).
    if not _migration_applied(conn, "erp_payment_types_cartao_to_credito"):
        conn.execute(
            "UPDATE erp_payment_types SET payment_method = 'credito' "
            "WHERE payment_method = 'cartao'"
        )
        _mark_migration_applied(conn, "erp_payment_types_cartao_to_credito")


def _ensure_erp_tipvenda_table(conn: sqlite3.Connection) -> None:
    """Tipos de negociação liberados no ERP (``GET orders/payment``).

    Acumulado da fila ``orders/payment`` (cada lote traz só o que mudou):
    ``active = 0`` marca o código que veio com ``STATUS`` inativo. A escolha
    por forma de pagamento continua em ``erp_payment_types``.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_tipvenda (
            codtipvenda INTEGER PRIMARY KEY,
            description TEXT,
            dhalter     TEXT,
            active      INTEGER NOT NULL DEFAULT 1,
            synced_at   TEXT    NOT NULL
        )
        """
    )


def _ensure_order_number_registry(conn: sqlite3.Connection) -> None:
    """Números de pedido já emitidos, para nunca repetirem (vão ao ERP).

    Sobrevive ao reset do totem, que apaga ``transactions``. Na criação, recebe
    os números das vendas existentes.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_number_registry (
            order_number TEXT PRIMARY KEY,
            issued_at    TEXT NOT NULL
        )
        """
    )
    _ensure_schema_migrations_table(conn)
    migration_name = "order_number_registry_backfill"
    if not _migration_applied(conn, migration_name):
        conn.execute(
            "INSERT OR IGNORE INTO order_number_registry (order_number, issued_at) "
            "SELECT order_number, created_at FROM transactions WHERE order_number IS NOT NULL"
        )
        _mark_migration_applied(conn, migration_name)


def _ensure_product_sku_aliases_table(conn: sqlite3.Connection) -> None:
    """Tabela de SKUs alternativos (ERP / legado) por produto."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS product_sku_aliases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            sku        TEXT    NOT NULL,
            created_at TEXT    NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            UNIQUE (sku)
        );
        CREATE INDEX IF NOT EXISTS idx_product_sku_aliases_product
            ON product_sku_aliases(product_id);
    """)


def _ensure_promotions_extended_rule_types(conn: sqlite3.Connection) -> None:
    """Recria promotions com CHECK ampliado (min_bundle, exact_bundle, combo_bundle).

    Em bases existentes o DDL ``CREATE TABLE IF NOT EXISTS`` não altera a restrição
    CHECK — por isso recriamos a tabela preservando os dados.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='promotions'"
    ).fetchone()
    if row is None:
        return  # tabela ainda não existe; _SCHEMA criará com CHECK correto
    if 'combo_bundle' in (row[0] or ''):
        return  # já migrada

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS promotions_v2")
    conn.execute(
        """
        CREATE TABLE promotions_v2 (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            rule_type   TEXT    NOT NULL
                CHECK (rule_type IN ('percent','fixed','bogo','min_bundle','exact_bundle','combo_bundle')),
            rule_value  REAL    NOT NULL DEFAULT 0,
            min_qty     INTEGER NOT NULL DEFAULT 1,
            free_qty    INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        )
        """
    )
    # Normaliza nomes legados de rule_type que possam existir de tentativas anteriores
    conn.execute(
        "INSERT INTO promotions_v2 "
        "SELECT id, event_id, name, "
        "CASE rule_type "
        "  WHEN 'a_partir_de' THEN 'min_bundle' "
        "  WHEN 'na_compra_de' THEN 'exact_bundle' "
        "  WHEN 'min_bundle' THEN 'min_bundle' "
        "  WHEN 'exact_bundle' THEN 'exact_bundle' "
        "  WHEN 'combo_bundle' THEN 'combo_bundle' "
        "  ELSE rule_type END, "
        "rule_value, min_qty, free_qty, active, created_at, updated_at "
        "FROM promotions"
    )
    conn.execute("DROP TABLE promotions")
    conn.execute("ALTER TABLE promotions_v2 RENAME TO promotions")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_promotions_event "
        "ON promotions(event_id, active)"
    )
    conn.execute("PRAGMA foreign_keys = ON")


def _ensure_promotions_bogo_product_columns(conn: sqlite3.Connection) -> None:
    """Adiciona SKUs de compra/grátis no BOGO (bases já criadas sem essas colunas)."""
    cols = _table_columns(conn, "promotions")
    if "bogo_buy_product_id" not in cols:
        conn.execute("ALTER TABLE promotions ADD COLUMN bogo_buy_product_id INTEGER")
    if "bogo_free_product_id" not in cols:
        conn.execute("ALTER TABLE promotions ADD COLUMN bogo_free_product_id INTEGER")


_GIFT_RULES_DDL = """
    CREATE TABLE {name} (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        INTEGER NOT NULL,
        name            TEXT    NOT NULL,
        rule_type       TEXT    NOT NULL CHECK (rule_type IN ('min_qty', 'kit', 'min_total')),
        min_qty         INTEGER NOT NULL DEFAULT 1,
        min_value       REAL,
        gift_product_id INTEGER,
        gift_item_id    INTEGER,
        gift_qty        INTEGER NOT NULL DEFAULT 1,
        max_per_order   INTEGER,
        active          INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL,
        FOREIGN KEY (event_id)        REFERENCES events(id)     ON DELETE CASCADE,
        FOREIGN KEY (gift_product_id) REFERENCES products(id)   ON DELETE CASCADE,
        FOREIGN KEY (gift_item_id)    REFERENCES gift_items(id) ON DELETE CASCADE
    )
"""

_GIFT_RULE_PRODUCTS_DDL = """
    CREATE TABLE IF NOT EXISTS gift_rule_products (
        gift_rule_id INTEGER NOT NULL,
        product_id   INTEGER NOT NULL,
        PRIMARY KEY (gift_rule_id, product_id),
        FOREIGN KEY (gift_rule_id) REFERENCES gift_rules(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id)   REFERENCES products(id)   ON DELETE CASCADE
    )
"""


def _ensure_gift_rules_tables(conn: sqlite3.Connection) -> None:
    """Brindes por evento: regra → produtos do estoque e/ou brindes avulsos.

    Ficam fora de ``promotions`` de propósito: brinde não mexe no preço dos
    itens pagos, só acrescenta uma linha a R$ 0,00 no pedido.
    ``transaction_items.gift_rule_id`` marca essa linha (sem FK: a venda
    guarda a referência mesmo se a regra for excluída depois) e
    ``gift_item_id``, quando o brinde é avulso (não é produto nem vai ao ERP).

    ``gift_items.stock`` é o total disponível no evento (NULL = sem limite); o
    saldo é esse total menos o que saiu em vendas confirmadas.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            description TEXT,
            stock       INTEGER,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        )
        """
    )
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'gift_rules'"
    ).fetchone()
    if row is None:
        conn.execute(_GIFT_RULES_DDL.format(name="gift_rules"))
    elif "min_total" not in (row[0] or ""):
        # Versão de 2026-10-09 (só produto, só quantidade): o CHECK e o NOT NULL
        # de gift_product_id não mudam com ALTER. Recria preservando dados e
        # vínculos; os vínculos saem antes do DROP para não sumirem em cascata.
        conn.execute("DROP TABLE IF EXISTS gift_rules_v2")
        conn.execute(_GIFT_RULES_DDL.format(name="gift_rules_v2"))
        conn.execute(
            """
            INSERT INTO gift_rules_v2
                (id, event_id, name, rule_type, min_qty, gift_product_id, gift_qty,
                 max_per_order, active, created_at, updated_at)
            SELECT id, event_id, name, rule_type, min_qty, gift_product_id, gift_qty,
                   max_per_order, active, created_at, updated_at
              FROM gift_rules
            """
        )
        links = conn.execute(
            "SELECT gift_rule_id, product_id FROM gift_rule_products"
        ).fetchall() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gift_rule_products'"
        ).fetchone() else []
        conn.execute("DROP TABLE IF EXISTS gift_rule_products")
        conn.execute("DROP TABLE gift_rules")
        conn.execute("ALTER TABLE gift_rules_v2 RENAME TO gift_rules")
        conn.execute(_GIFT_RULE_PRODUCTS_DDL)
        conn.executemany(
            "INSERT OR IGNORE INTO gift_rule_products (gift_rule_id, product_id) VALUES (?, ?)",
            [(int(r[0]), int(r[1])) for r in links],
        )
    conn.execute(_GIFT_RULE_PRODUCTS_DDL)
    # Produtos do estoque que a regra entrega (pode ser mais de um). A coluna
    # gift_rules.gift_product_id fica com o primeiro, para leitores antigos.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_rule_gift_products (
            gift_rule_id INTEGER NOT NULL,
            product_id   INTEGER NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (gift_rule_id, product_id),
            FOREIGN KEY (gift_rule_id) REFERENCES gift_rules(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id)   REFERENCES products(id)   ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO gift_rule_gift_products (gift_rule_id, product_id, position)
        SELECT g.id, g.gift_product_id, 0
          FROM gift_rules g
         WHERE g.gift_product_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM gift_rule_gift_products x WHERE x.gift_rule_id = g.id)
        """
    )
    # Brindes avulsos que a regra entrega (pode ser mais de um), como os
    # produtos acima. gift_rules.gift_item_id fica com o primeiro.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_rule_gift_items (
            gift_rule_id INTEGER NOT NULL,
            gift_item_id INTEGER NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (gift_rule_id, gift_item_id),
            FOREIGN KEY (gift_rule_id) REFERENCES gift_rules(id) ON DELETE CASCADE,
            FOREIGN KEY (gift_item_id) REFERENCES gift_items(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO gift_rule_gift_items (gift_rule_id, gift_item_id, position)
        SELECT g.id, g.gift_item_id, 0
          FROM gift_rules g
         WHERE g.gift_item_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM gift_rule_gift_items x WHERE x.gift_rule_id = g.id)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_rule_gift_items_item ON gift_rule_gift_items(gift_item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_rules_event ON gift_rules(event_id, active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_items_event ON gift_items(event_id)")
    cols = _table_columns(conn, "transaction_items")
    if "gift_rule_id" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN gift_rule_id INTEGER")
    if "gift_item_id" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN gift_item_id INTEGER")


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Inicialização + seed
# ---------------------------------------------------------------------------

def _consolidate_legacy_movement_types(conn: sqlite3.Connection) -> None:
    """Converte tipos legados ``ajuste``/``inicial`` para ``entrada`` ou ``saída``."""
    conn.execute(
        "DELETE FROM stock_movements WHERE movement_type IN ('ajuste', 'inicial') AND delta = 0"
    )
    conn.execute(
        """
        UPDATE stock_movements
           SET movement_type = 'entrada'
         WHERE movement_type IN ('inicial', 'ajuste') AND delta > 0
        """
    )
    conn.execute(
        """
        UPDATE stock_movements
           SET movement_type = 'saida'
         WHERE movement_type = 'ajuste' AND delta < 0
        """
    )


def _ensure_min_stock_default_five(conn: sqlite3.Connection) -> None:
    """Migração única: produtos com mínimo 0 passam a usar o padrão (5 un.)."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= 1:
        return
    conn.execute(
        "UPDATE products SET min_stock = ? WHERE min_stock = 0",
        (DEFAULT_MIN_STOCK,),
    )
    conn.execute(
        "UPDATE event_products SET min_stock = ? WHERE min_stock = 0",
        (DEFAULT_MIN_STOCK,),
    )
    conn.execute("PRAGMA user_version = 1")


def _ensure_user_access(conn: sqlite3.Connection) -> None:
    """Tipo e permissões dos usuários (database/users.py) na tabela ``sellers``.

    - ``role``: administrador / vendedor / marketing / personalizado
      (contas antigas viram ``vendedor``, que é o que elas eram);
    - ``permissions``: JSON, só para o tipo personalizado;
    - ``all_events`` + ``user_events``: eventos que a conta abre no painel.

    As contas de Marketing da primeira versão (``marketing_users`` /
    ``event_marketing``) passam para cá e essas tabelas são removidas.
    """
    cols = _table_columns(conn, "sellers")
    if "role" not in cols:
        conn.execute("ALTER TABLE sellers ADD COLUMN role TEXT NOT NULL DEFAULT 'vendedor'")
    if "permissions" not in cols:
        conn.execute("ALTER TABLE sellers ADD COLUMN permissions TEXT")
    if "all_events" not in cols:
        conn.execute("ALTER TABLE sellers ADD COLUMN all_events INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_events (
            user_id  INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            added_at TEXT    NOT NULL,
            PRIMARY KEY (user_id, event_id),
            FOREIGN KEY (user_id)  REFERENCES sellers(id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES events(id)  ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_events_event ON user_events(event_id)")

    has_old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'marketing_users'"
    ).fetchone()
    if not has_old:
        return
    links: dict = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_marketing'"
    ).fetchone():
        for r in conn.execute("SELECT marketing_id, event_id, added_at FROM event_marketing").fetchall():
            links.setdefault(int(r[0]), []).append((int(r[1]), r[2]))
    for r in conn.execute(
        "SELECT id, name, username, password_hash, active, created_at, updated_at, last_login_at "
        "FROM marketing_users"
    ).fetchall():
        login = r[2]
        taken = conn.execute(
            "SELECT 1 FROM sellers WHERE username = ? OR email = ?", (login, login)
        ).fetchone()
        if taken:
            login = f"{login}.mkt"
        cur = conn.execute(
            """
            INSERT INTO sellers (name, email, username, password_hash, active, created_at,
                                 updated_at, last_login_at, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'marketing')
            """,
            (r[1], login, login, r[3], r[4], r[5], r[6], r[7]),
        )
        for event_id, added_at in links.get(int(r[0]), []):
            conn.execute(
                "INSERT OR IGNORE INTO user_events (user_id, event_id, added_at) VALUES (?, ?, ?)",
                (int(cur.lastrowid), event_id, added_at),
            )
    conn.execute("DROP TABLE IF EXISTS event_marketing")
    conn.execute("DROP TABLE IF EXISTS marketing_users")


def init_db() -> None:
    """Cria as tabelas, aplica migrações leves e remove resíduos do seed antigo."""
    mode = connection.enable_wal()
    if mode != "wal":
        logging.getLogger(__name__).warning(
            "Banco SQLite ficou em journal_mode=%s (esperado WAL).", mode,
        )
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _ensure_products_sku_column(conn)
        _ensure_transaction_items_product_sku_column(conn)
        _ensure_transactions_client_columns(conn)
        _ensure_transactions_cro_columns(conn)
        _ensure_transactions_payment_method(conn)
        _ensure_transactions_card_installments(conn)
        _ensure_transactions_aut(conn)
        _ensure_transactions_event_id(conn)
        _ensure_sellers_columns(conn)
        _ensure_products_family_columns(conn)
        _ensure_products_erp_columns(conn)
        _ensure_product_sku_aliases_table(conn)
        _ensure_events_tables(conn)
        _ensure_promotions_extended_rule_types(conn)
        _ensure_promotions_bogo_product_columns(conn)
        _ensure_events_badge_color(conn)
        _ensure_events_goals(conn)
        _ensure_events_operations_closed(conn)
        _ensure_events_operation_type(conn)
        _ensure_event_extensions(conn)
        _ensure_checkout_holds_table(conn)
        _ensure_event_products_backorder_limit(conn)
        _ensure_event_products_backorder_omit_insert_unlimited(conn)
        _ensure_event_products_price(conn)
        _ensure_transaction_items_promo_columns(conn)
        _ensure_gift_rules_tables(conn)
        _ensure_user_access(conn)
        _ensure_delivery_columns(conn)
        _ensure_transactions_handover_status(conn)
        _ensure_transactions_receipt_note(conn)
        _ensure_transactions_erp_columns(conn)
        _ensure_erp_tables(conn)
        _ensure_order_number_registry(conn)
        _ensure_erp_tipvenda_table(conn)
        _consolidate_legacy_movement_types(conn)
        _purge_invalid_product_ids(conn)
        _purge_legacy_demo_products(conn)
        _restore_retired_variant_parents(conn)
        _ensure_min_stock_default_five(conn)


def _purge_invalid_product_ids(conn: sqlite3.Connection) -> None:
    """Remove cadastros com ``id`` não positivo (resíduos da antiga integração Wake)."""
    rows = conn.execute("SELECT id FROM products WHERE id < 1").fetchall()
    if not rows:
        return
    ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM stock_movements WHERE product_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM products WHERE id IN ({placeholders})",
        ids,
    )


def _purge_legacy_demo_products(conn: sqlite3.Connection) -> None:
    """Remove produtos do catálogo fictício inicial (imagens picsum.photos).

    O catálogo passou a vir de integração (Wake, depois ERP); estes registros eram
    identificáveis pela URL de placeholder usada no seed antigo.
    """
    rows = conn.execute(
        "SELECT id FROM products WHERE image LIKE ?",
        ("%picsum.photos%",),
    ).fetchall()
    if not rows:
        return
    ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM stock_movements WHERE product_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM products WHERE id IN ({placeholders})",
        ids,
    )


def _restore_retired_variant_parents(conn: sqlite3.Connection) -> None:
    """Reativa SKUs-base que a rotina antiga desligou do catálogo/evento."""
    _ensure_schema_migrations_table(conn)
    migration_name = "restore_variant_parent_products_v2"
    if _migration_applied(conn, migration_name):
        return
    from .products import restore_retired_variant_parents_in_conn

    restore_retired_variant_parents_in_conn(conn)
    _mark_migration_applied(conn, migration_name)
