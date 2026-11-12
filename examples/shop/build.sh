#!/bin/sh
# Builds the demo into examples/shop/out next to this script (or the folder given as $1).
# Works from any current directory and from a copied examples/shop folder.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  "") OUT="$HERE/out" ;;
  /*) OUT="$1" ;;
  *) OUT="$(pwd)/$1" ;;
esac
cd "$HERE"
D=dumps
rm -f "$OUT/keygraph.db"  # start clean, so the code index and the query log are not appended twice
keygraph load --dir "$OUT" --columns $D/columns.json --statistics $D/statistics.json \
  --key-columns $D/key_columns.json --tables $D/tables.json --context context.yaml
keygraph profile --dir "$OUT" --source sqlite --url sqlite:///shop.db --bare-tables \
  --tables shop.customers,shop.orders,shop.order_items,shop.products,billing.payments,shop.categories,billing.vouchers
keygraph code index app --name shop-app --dir "$OUT"
keygraph queries import queries.jsonl --dir "$OUT"
keygraph render --queries --dir "$OUT"
