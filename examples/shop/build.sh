#!/bin/sh
# Builds the demo into examples/shop/out (or the folder given as $1). Run from the keygraph folder.
set -e
OUT="${1:-examples/shop/out}"
D=examples/shop/dumps
rm -f "$OUT/keygraph.db"  # start clean, so the code index and the query log are not appended twice
keygraph load --dir "$OUT" --columns $D/columns.json --statistics $D/statistics.json \
  --key-columns $D/key_columns.json --tables $D/tables.json --context examples/shop/context.yaml
keygraph profile --dir "$OUT" --source sqlite --url sqlite:///examples/shop/shop.db --bare-tables \
  --tables shop.customers,shop.orders,shop.order_items,shop.products,billing.payments,shop.categories,billing.vouchers
keygraph code index examples/shop/app --name shop-app --dir "$OUT"
keygraph queries import examples/shop/queries.jsonl --dir "$OUT"
keygraph render --queries --dir "$OUT"
