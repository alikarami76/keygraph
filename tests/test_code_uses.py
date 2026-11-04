"""The uses pass: symbols from declarations, the common-name guard, ambiguity and string literals."""
from keygraph.code.uses import build_index, find_uses

DECLS = [
    {"kind": "declare_table", "table": "app.orders", "column": None, "symbol": "Order"},
    {"kind": "declare_column", "table": "app.orders", "column": "status", "symbol": "status"},
    {"kind": "declare_column", "table": "app.orders", "column": "placed_at", "symbol": "placedAt"},
    {"kind": "declare_table", "table": "app.shipments", "column": None, "symbol": "Shipment"},
    {"kind": "declare_column", "table": "app.shipments", "column": "tracking_no", "symbol": "trackingNo"},
    {"kind": "declare_table", "table": "app.parcels", "column": None, "symbol": "Parcel"},
    {"kind": "declare_column", "table": "app.parcels", "column": "tracking_no", "symbol": "trackingNo"},
]


def uses(text, skip=(), tables=()):
    return [(r.line, r.table_id, r.column, r.symbol, r.rule, r.confidence)
            for r in find_uses("r", "f.ts", text, build_index(DECLS), set(skip), set(tables))]


def test_index_has_variants_and_entity_names():
    idx = build_index(DECLS)
    assert idx.symbols["getPlacedAt"] == [("app.orders", "placed_at", "placedAt")]
    assert idx.symbols["placed_at"] == [("app.orders", "placed_at", "placedAt")]
    assert idx.names["app.orders"] == {"orders", "Order"}


def test_named_file_gets_0_8_elsewhere_0_5():
    assert uses("const o: Order = load();\no.getPlacedAt();\n") == [
        (2, "app.orders", "placed_at", "getPlacedAt", "use:symbol", 0.8)]
    assert uses("x.placedAt\n") == [(1, "app.orders", "placed_at", "placedAt", "use:symbol", 0.5)]


def test_common_name_counts_only_where_the_entity_is_named():
    assert uses("if (x.status) {}\n") == []
    assert uses("// orders\nif (x.status) {}\n") == [(2, "app.orders", "status", "status", "use:symbol", 0.8)]


def test_ambiguous_symbol_splits_confidence_and_naming_narrows_it():
    assert uses("p.trackingNo\n") == [(1, "app.parcels", "tracking_no", "trackingNo", "use:symbol", 0.25),
                                      (1, "app.shipments", "tracking_no", "trackingNo", "use:symbol", 0.25)]
    assert uses("Shipment.trackingNo\n") == [(1, "app.shipments", "tracking_no", "trackingNo", "use:symbol", 0.8)]


def test_declaration_lines_are_skipped_and_literals_need_the_table_in_the_file():
    assert uses("placedAt\nplacedAt\n", skip=[1]) == [(2, "app.orders", "placed_at", "placedAt", "use:symbol", 0.5)]
    assert uses("row['status']\n") == []
    assert uses("row['status']\n", tables=["app.orders"]) == [(1, "app.orders", "status", "status", "use:literal", 0.8)]


def test_guard_for_symbols_declared_by_five_tables():
    decls = [{"kind": "declare_column", "table": f"app.t{i}", "column": "ref_code", "symbol": "refCode"} for i in range(5)]
    idx = build_index(decls)
    assert find_uses("r", "f.ts", "x.refCode\n", idx, set(), set()) == []
    assert [r.table_id for r in find_uses("r", "f.ts", "// t3\nx.refCode\n", idx, set(), set())] == ["app.t3"]
