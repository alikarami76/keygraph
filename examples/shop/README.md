# Example: a fictional webshop

A made-up shop with 35 tables in three schemas, so you can try every part of keygraph without a database of your own.
Nothing here is real data.

| Schema | Tables | What it shows |
|---|---|---|
| `shop` | 25 | customers, orders, order items, catalog, fulfilment, a `categories` tree (`parent_id`), a polymorphic `comments` table, an `audit_log`, a scratch table `orders_shadow` |
| `billing` | 6 | payments, refunds, vouchers, invoices: relations that cross schemas |
| `reporting` | 5 | a nightly copy of four `shop` tables: keygraph marks it as a mirror and keeps it out of areas and paths |

About half of the foreign keys are declared (`dumps/key_columns.json`). The rest are inferred from column names, with a
confidence and the evidence behind it. Many columns named `created_by_id` point at `shop.users`, which makes `users` a hub.

## Files

- `dumps/*.json`: `information_schema` style dumps (COLUMNS, STATISTICS, KEY_COLUMNS, TABLES).
- `context.yaml`: concepts, metrics, column codes, table grain and pitfalls, and a small areas curation.
- `shop.db`: SQLite with a few hundred sample rows, used by `keygraph profile`. It includes an email column (masked in
  the profile) and one order status code (`9`) that the context does not document, so the profile flags it.
- `generate.py`: rewrites the dumps and `shop.db` (deterministic). `build.sh`: the two build commands below in one script.

## Run it

From the `keygraph/` folder (use `uv run keygraph` instead of `keygraph` when you work from a checkout):

    keygraph load --dir examples/shop/out \
      --columns examples/shop/dumps/columns.json --statistics examples/shop/dumps/statistics.json \
      --key-columns examples/shop/dumps/key_columns.json --tables examples/shop/dumps/tables.json \
      --context examples/shop/context.yaml

    keygraph profile --dir examples/shop/out --source sqlite --url sqlite:///examples/shop/shop.db --bare-tables \
      --tables shop.customers,shop.orders,shop.order_items,shop.products,billing.payments,shop.categories,billing.vouchers

    keygraph ui --dir examples/shop/out            # open the explorer

Then ask it things:

    keygraph areas --dir examples/shop/out
    keygraph area Ordering --dir examples/shop/out
    keygraph path shop.customers shop.shipments --dir examples/shop/out     # two fan-out warnings
    keygraph search "in transit" --dir examples/shop/out                   # finds a status label
    keygraph describe shop.customers --dir examples/shop/out               # email values are withheld
    keygraph areas --include-mirrors --dir examples/shop/out               # shows the reporting mirror

Bare names such as `customers` are ambiguous here because `reporting.customers` copies `shop.customers`; give the schema.

Two builds of the same dumps give byte-identical `graph.json` and `keygraph.html`. After `profile`, only the
`profiled_at` timestamps differ.
