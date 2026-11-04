"""Writes the fictional webshop example: information_schema-style JSON dumps and a small SQLite database.

Run `python3 generate.py` in this folder. The output is deterministic, so the committed files only change when this
script changes. Everything here is invented; no real data is involved."""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
TYPES = {"id": "bigint unsigned", "int": "int", "dec": "decimal(12,2)", "dt": "datetime", "txt": "text", "bool": "tinyint(1)",
         "date": "date", "json": "json"}

# (schema, name, comment, [columns], [declared foreign keys], [extra indexes], [unique keys], approx rows)
# Column: "name:type" or "name:type?" (nullable). The first column "id" is the primary key unless the table has none.
TABLES: list[tuple] = []


def table(schema, name, comment, cols, fks=(), indexes=(), uniques=(), rows=1000, pk=True):
    TABLES.append((schema, name, comment, cols, list(fks), list(indexes), list(uniques), rows, pk))


AUD = ["created_at:dt", "updated_at:dt?"]
# --- shop: people and staff ---------------------------------------------------------------------------------
table("shop", "users", "Back-office staff accounts", ["id:id", "login:s60", "full_name:s120", "role:s30", "active:bool"] + AUD,
      uniques=[("uq_users_login", ["login"])], rows=140)
table("shop", "customers", "People who buy", ["id:id", "email:s190", "first_name:s80", "last_name:s80", "status:tiny", "locale:s8",
      "created_at:dt", "last_login_at:dt?"], uniques=[("uq_customers_email", ["email"])], rows=420000)
table("shop", "customer_addresses", "Delivery addresses of a customer", ["id:id", "customer_id:id", "city:s80", "postal_code:s12",
      "line1:s200", "is_default:bool"], fks=[("customer_id", "shop.customers")], rows=610000)
table("shop", "sellers", "Third-party sellers on the marketplace", ["id:id", "name:s150", "status:tiny", "commission_pct:dec",
      "created_by_id:id", "created_at:dt"], rows=3100)
table("shop", "seller_users", "Which staff of a seller can log in", ["id:id", "seller_id:id", "login:s60", "active:bool"],
      fks=[("seller_id", "shop.sellers")], rows=9000)
# --- shop: catalog ------------------------------------------------------------------------------------------
table("shop", "categories", "Product category tree", ["id:id", "parent_id:id?", "name:s120", "slug:s120", "sort_order:int",
      "created_by_id:id"], fks=[("parent_id", "shop.categories")], uniques=[("uq_categories_slug", ["slug"])], rows=840)
table("shop", "brands", "Brand names", ["id:id", "name:s120", "created_by_id:id"], rows=1900)
table("shop", "products", "A product as shown on a page", ["id:id", "category_id:id", "brand_id:id?", "seller_id:id", "title:s255",
      "status:tiny", "created_by_id:id", "created_at:dt", "updated_at:dt?"],
      fks=[("category_id", "shop.categories"), ("brand_id", "shop.brands")], indexes=[("ix_products_seller", ["seller_id"])], rows=260000)
table("shop", "product_variants", "A sellable variant (size, colour) of a product with its own SKU and price",
      ["id:id", "product_id:id", "sku:s60", "price:dec", "list_price:dec", "color:s30?", "size:s30?", "active:bool"],
      fks=[("product_id", "shop.products")], uniques=[("uq_variants_sku", ["sku"])], rows=900000)
table("shop", "product_images", "Pictures of a product", ["id:id", "product_id:id", "url:s400", "position:int"],
      indexes=[("ix_images_product", ["product_id"])], rows=1500000)
table("shop", "warehouses", "Places stock is kept", ["id:id", "name:s120", "city:s80", "created_by_id:id"], rows=14)
table("shop", "inventory", "Units on hand per variant and warehouse", ["id:id", "variant_id:id", "warehouse_id:id", "on_hand:int", "reserved:int",
      "updated_at:dt"], fks=[("variant_id", "shop.product_variants"), ("warehouse_id", "shop.warehouses")], rows=2100000)
# --- shop: ordering ----------------------------------------------------------------------------------------
table("shop", "carts", "Open baskets", ["id:id", "customer_id:id?", "created_at:dt", "updated_at:dt?"], indexes=[("ix_carts_customer", ["customer_id"])], rows=900000)
table("shop", "cart_items", "Lines of a basket", ["id:id", "cart_id:id", "variant_id:id", "qty:int"],
      fks=[("cart_id", "shop.carts")], indexes=[("ix_cart_items_variant", ["variant_id"])], rows=2400000)
table("shop", "orders", "One checkout by a customer", ["id:id", "customer_id:id", "address_id:id?", "voucher_id:id?", "status:tiny", "currency:s3",
      "total:dec", "placed_at:dt", "created_by_id:id?", "updated_at:dt?"],
      fks=[("customer_id", "shop.customers")], indexes=[("ix_orders_placed", ["placed_at"]), ("ix_orders_customer_placed", ["customer_id", "placed_at"])],
      rows=5200000)
table("shop", "order_items", "One line of an order: a variant, a quantity and the price paid",
      ["id:id", "order_id:id", "variant_id:id", "seller_id:id", "qty:int", "unit_price:dec", "discount:dec"],
      fks=[("order_id", "shop.orders"), ("variant_id", "shop.product_variants")], indexes=[("ix_items_seller", ["seller_id"])], rows=14800000)
table("shop", "order_status_history", "Every status change of an order", ["id:id", "order_id:id", "old_status:tiny?", "new_status:tiny", "changed_by_id:id?",
      "changed_at:dt"], indexes=[("ix_hist_order", ["order_id", "changed_at"])], rows=21000000)
table("shop", "orders_shadow", "Scratch copy of orders used by a one-off migration", ["id:id", "customer_id:id", "status:tiny", "total:dec", "placed_at:dt"], rows=5100000)
# --- shop: fulfilment ---------------------------------------------------------------------------------------
table("shop", "shipments", "A parcel sent for an order", ["id:id", "order_id:id", "warehouse_id:id", "carrier:s40", "tracking_no:s60?", "status:tiny",
      "shipped_at:dt?", "delivered_at:dt?", "created_by_id:id?"], fks=[("order_id", "shop.orders")], rows=6100000)
table("shop", "shipment_items", "Which order items are in a parcel", ["id:id", "shipment_id:id", "order_item_id:id", "qty:int"],
      fks=[("shipment_id", "shop.shipments")], rows=16000000)
table("shop", "returns", "A customer sends items back", ["id:id", "order_id:id", "requested_by_customer_id:id", "reason:s60", "status:tiny", "created_at:dt"],
      indexes=[("ix_returns_order", ["order_id"])], rows=410000)
table("shop", "reviews", "Customer reviews of a product", ["id:id", "product_id:id", "customer_id:id", "rating:tiny", "body:txt?", "created_at:dt"],
      fks=[("product_id", "shop.products")], rows=1300000)
# --- shop: shared / cross-cutting -----------------------------------------------------------------------------
table("shop", "comments", "Free-text comments on several kinds of record (polymorphic)",
      ["id:id", "target_type:s30", "target_id:id", "author_id:id", "body:txt", "created_at:dt"], indexes=[("ix_comments_target", ["target_type", "target_id"])], rows=880000)
table("shop", "audit_log", "Append-only trail of changes made by staff", ["id:id", "entity_type:s40", "entity_id:id", "actor_id:id", "action:s20", "payload:json?",
      "created_at:dt"], indexes=[("ix_audit_entity", ["entity_type", "entity_id"])], rows=90000000)
# --- billing --------------------------------------------------------------------------------------------------
table("billing", "payment_methods", "Ways to pay", ["id:id", "code:s30", "name:s80", "active:bool"], uniques=[("uq_pm_code", ["code"])], rows=9)
table("billing", "vouchers", "Discount codes", ["id:id", "code:s40", "kind:s12", "value:dec", "valid_from:dt", "valid_to:dt?", "created_by_id:id"],
      uniques=[("uq_vouchers_code", ["code"])], rows=52000)
table("billing", "voucher_redemptions", "A voucher used on an order", ["id:id", "voucher_id:id", "order_id:id", "amount:dec", "redeemed_at:dt"],
      fks=[("voucher_id", "billing.vouchers")], indexes=[("ix_redeem_order", ["order_id"])], rows=1900000)
table("billing", "payments", "Money taken for an order", ["id:id", "order_id:id", "method_id:id", "amount:dec", "currency:s3", "status:tiny", "gateway_ref:s80?",
      "paid_at:dt?", "created_at:dt"], fks=[("method_id", "billing.payment_methods")], indexes=[("ix_payments_order", ["order_id"])], rows=5600000)
table("billing", "refunds", "Money given back", ["id:id", "payment_id:id", "amount:dec", "reason:s60?", "created_at:dt", "created_by_id:id?"],
      fks=[("payment_id", "billing.payments")], rows=380000)
table("billing", "invoices", "Invoice issued to a customer", ["id:id", "order_id:id", "customer_id:id", "number:s30", "issued_at:dt", "total:dec"],
      uniques=[("uq_invoices_number", ["number"])], indexes=[("ix_invoices_order", ["order_id"])], rows=5000000)
# --- reporting: a nightly copy (mirror) ----------------------------------------------------------------------------
for t in ("customers", "products", "orders", "order_items"):
    src = next(x for x in TABLES if x[0] == "shop" and x[1] == t)
    cols = [c for c in src[3]] + ["loaded_at:dt"]
    table("reporting", t, f"Reporting copy of shop.{t}, reloaded nightly", cols, rows=src[7], pk=False)
table("reporting", "daily_sales_load", "Load log of the nightly copy", ["id:id", "run_date:date", "rows_loaded:int", "finished_at:dt"], rows=900)

# --- the sample SQLite database ----------------------------------------------------------------------------------
PROFILED = ["customers", "orders", "order_items", "products", "payments", "categories", "vouchers"]


def col_sql(spec: str) -> tuple[str, str, bool]:
    name, t = spec.split(":")
    null = t.endswith("?")
    t = t.rstrip("?")
    if t.startswith("s") and t[1:].isdigit():
        return name, f"varchar({t[1:]})", null
    if t == "tiny":
        return name, "tinyint", null
    return name, TYPES[t], null


def dump() -> None:
    columns, stats, kcu, tables = [], [], [], []
    for schema, name, comment, cols, fks, indexes, uniques, rows, pk in TABLES:
        idx = []  # (name, unique, [cols])
        if pk:
            idx.append(("PRIMARY", True, ["id"]))
        idx += [(n, True, c) for n, c in uniques]
        idx += [(f"ix_{name}_{c}", False, [c]) for c, _ in fks if not any(i[2][0] == c for i in idx)]
        idx += [(n, False, c) for n, c in indexes]
        keyed = {}
        for n, uniq, cs in idx:
            keyed.setdefault(cs[0], "PRI" if n == "PRIMARY" else ("UNI" if uniq and len(cs) == 1 else "MUL"))
        for pos, spec in enumerate(cols, 1):
            cname, ctype, null = col_sql(spec)
            row = {"TABLE_SCHEMA": schema, "TABLE_NAME": name, "COLUMN_NAME": cname, "ORDINAL_POSITION": pos,
                   "DATA_TYPE": ctype.split("(")[0].split(" ")[0], "COLUMN_TYPE": ctype, "IS_NULLABLE": "YES" if null else "NO",
                   "COLUMN_KEY": keyed.get(cname, ""), "EXTRA": "auto_increment" if cname == "id" and pk else ""}
            columns.append(row)
        for n, uniq, cs in idx:
            for seq, c in enumerate(cs, 1):
                stats.append({"TABLE_SCHEMA": schema, "TABLE_NAME": name, "INDEX_NAME": n, "NON_UNIQUE": 0 if uniq else 1,
                              "SEQ_IN_INDEX": seq, "COLUMN_NAME": c})
        for c, ref in fks:
            rs, rt = ref.split(".")
            kcu.append({"TABLE_SCHEMA": schema, "TABLE_NAME": name, "CONSTRAINT_NAME": f"fk_{name}_{c}", "ORDINAL_POSITION": 1,
                        "COLUMN_NAME": c, "REFERENCED_TABLE_SCHEMA": rs, "REFERENCED_TABLE_NAME": rt, "REFERENCED_COLUMN_NAME": "id"})
        tables.append({"TABLE_SCHEMA": schema, "TABLE_NAME": name, "ENGINE": "InnoDB", "TABLE_ROWS": rows,
                       "DATA_LENGTH": rows * 120, "INDEX_LENGTH": rows * 40, "TABLE_COMMENT": comment})
    for fname, data in (("columns.json", columns), ("statistics.json", stats), ("key_columns.json", kcu), ("tables.json", tables)):
        (HERE / "dumps").mkdir(exist_ok=True)
        (HERE / "dumps" / fname).write_text(json.dumps(data, indent=1) + "\n")


def sqlite_db() -> None:
    rnd = random.Random(7)
    path = HERE / "shop.db"
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    for schema, name, _c, cols, *_ in TABLES:
        if name not in PROFILED or schema == "reporting":
            continue
        defs = []
        for spec in cols:
            cname, ctype, _n = col_sql(spec)
            defs.append(f"{cname} {'INTEGER PRIMARY KEY' if cname == 'id' else ctype}")
        con.execute(f"CREATE TABLE {name}({', '.join(defs)})")
    first, last = ["Ava", "Noah", "Mia", "Liam", "Zoe", "Omar", "Lena", "Ravi"], ["Stone", "Reyes", "Okafor", "Lindqvist", "Patel"]
    n_cust = 60
    for i in range(1, n_cust + 1):
        con.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?)",
                    (i, f"{first[i % 8].lower()}.{last[i % 5].lower()}{i}@example.com", first[i % 8], last[i % 5],
                     rnd.choice([1, 1, 1, 1, 2]), rnd.choice(["en", "en", "de", "fr"]), f"2026-0{1 + i % 6}-{1 + i % 27:02d} 10:00:00",
                     f"2026-10-{1 + i % 28:02d} 09:30:00"))
    cats = [(1, None, "Home"), (2, 1, "Kitchen"), (3, 1, "Garden"), (4, None, "Electronics"), (5, 4, "Phones"), (6, 4, "Audio")]
    for cid, parent, nm in cats:
        con.execute("INSERT INTO categories VALUES(?,?,?,?,?,?)", (cid, parent, nm, nm.lower(), cid, 1))
    for i in range(1, 41):
        con.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?)",
                    (i, 1 + i % 6, 1 + i % 5, 1 + i % 8, f"Product {i}", rnd.choice([1, 1, 1, 2]), 1, "2026-02-01 08:00:00", None))
    for i in range(1, 8):
        con.execute("INSERT INTO vouchers VALUES(?,?,?,?,?,?,?)", (i, f"SAVE{i * 5}", rnd.choice(["percent", "fixed"]), i * 5, "2026-01-01 00:00:00", None, 1))
    item = 1
    for o in range(1, 121):
        status = rnd.choices([1, 2, 3, 4, 9], [2, 5, 4, 2, 1])[0]  # 9 is on purpose missing from the documented codes
        total = 0
        for _ in range(rnd.randint(1, 4)):
            price, qty = rnd.choice([9.9, 19.5, 24.0, 59.9, 120.0]), rnd.randint(1, 3)
            con.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?,?)", (item, o, 1 + item % 40, 1 + item % 8, qty, price, 0))
            total += price * qty
            item += 1
        con.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (o, 1 + o % n_cust, None, rnd.choice([None, None, 1, 2]), status, "EUR", round(total, 2),
                     f"2026-1{o % 2}-{1 + o % 28:02d} 12:00:00", None, None))
        con.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,?)",
                    (o, o, rnd.choice([1, 2, 3]), round(total, 2), "EUR", rnd.choice([1, 2, 2, 2]), f"gw_{o:05d}", f"2026-10-{1 + o % 28:02d} 12:05:00", f"2026-10-{1 + o % 28:02d} 12:00:00"))
    con.commit()
    con.close()


if __name__ == "__main__":
    dump()
    sqlite_db()
    print(f"{len(TABLES)} tables")
