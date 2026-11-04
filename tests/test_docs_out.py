import re

from keygraph import areas as A
from keygraph.docs_out import mermaid_area, slug, table_file, write_docs
from keygraph.graph import build_graph
from keygraph.model import Column, Schema, Table
from tests.test_areas import pk, two_islands

LINE = re.compile(r'^\s+(\w+) (\|\||\}o|\|o|o\{|\}\||\|\{)--(\|\||o\{|\|\{|o\||\|o|\}o) (\w+) : "[^"]*"$')


def test_mermaid_is_valid_er_syntax():
    g = two_islands()
    a = next(x for x in A.cluster_areas(g) if "app.orders" in x["tables"])
    m = mermaid_area(g, a)
    lines = m.splitlines()
    assert lines[0] == "erDiagram"
    rels = [l for l in lines[1:] if "--" in l and not l.strip().startswith("%%")]
    assert rels and all(LINE.match(l) for l in rels)
    assert "users" not in " ".join(rels)  # hubs are notes, not relations
    assert any(l.strip().startswith("%%") and "users" in l for l in lines)


def test_mermaid_entity_names_unique_across_schemas():
    sc = Schema()
    sc.add(Table("a", "root", [Column("id", "int")], pk()))
    sc.add(Table("a", "items", [Column("id", "int"), Column("root_id", "int")], pk()))
    sc.add(Table("b", "items", [Column("id", "int"), Column("root_id", "int")], pk()))
    g = build_graph(sc)
    m = mermaid_area(g, {"tables": ["a.items", "b.items", "a.root"]})
    ents = re.findall(r"^    (\w+) \{$", m, re.M)
    assert len(ents) == len(set(ents)) == 3


def test_write_docs(tmp_path):
    g = two_islands()
    areas = A.cluster_areas(g)
    r = write_docs(tmp_path, g, areas, None)
    assert r["areas"] == len(areas) and r["tables"] == len(g.nodes)
    assert (tmp_path / "areas" / "index.md").exists()
    assert (tmp_path / "tables" / "app.orders.md").read_text().startswith("# app.orders")
    assert slug("Unconnected (app)") == "unconnected-app"
    page = (tmp_path / "areas" / "order.md").read_text()
    assert "## Main join paths" in page and "app.order_items" in page


def test_table_file_safe():
    assert table_file("app.we/ird name") == "app.we_ird_name.md"


def test_neighbouring_areas_section(tmp_path):
    sc = Schema()
    sc.add(Table("a", "orders", [Column("id", "int")], pk()))
    sc.add(Table("a", "vouchers", [Column("id", "int")], pk()))
    for n in ("o1", "o2", "o3"):
        sc.add(Table("a", f"order_{n}", [Column("id", "int"), Column("order_id", "int")], pk()))
    for n in ("v1", "v2", "v3"):
        sc.add(Table("a", f"voucher_{n}", [Column("id", "int"), Column("voucher_id", "int")], pk()))
    sc.add(Table("a", "voucher_link", [Column("id", "int"), Column("voucher_id", "int"), Column("order_o1_id", "int")], pk()))
    g = build_graph(sc)
    areas = A.cluster_areas(g)
    write_docs(tmp_path, g, areas, None)
    page = (tmp_path / "areas" / "order.md").read_text()
    assert "## Neighbouring areas" in page
    assert "[Voucher](voucher.md): 1 relation" in page


def test_grain_from_ctx_dump(tmp_path):
    g = two_islands()
    areas = A.cluster_areas(g)
    write_docs(tmp_path, g, areas, {"tables": [{"table": "app.orders", "grain": "one order"}]})
    assert "One row: one order" in (tmp_path / "tables" / "app.orders.md").read_text()
    assert "- app.orders: one order" in (tmp_path / "areas" / "order.md").read_text()


def test_mermaid_names_are_safe_identifiers():
    """Names starting with a digit or holding non-ASCII letters break GitHub's Mermaid renderer."""
    sc = Schema()
    sc.add(Table("a", "2fa_codes", [Column("id", "int"), Column("1st_try", "int")], pk()))
    sc.add(Table("a", "سفارش", [Column("id", "int"), Column("2fa_code_id", "int")], pk()))
    g = build_graph(sc)
    m = mermaid_area(g, {"tables": ["a.2fa_codes", "a.سفارش"]})
    ents = re.findall(r"^    (\S+) \{$", m, re.M)
    assert ents and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", e) for e in ents), ents
    attrs = re.findall(r"^        (\S+) (\S+) (PK|FK)$", m, re.M)
    assert attrs and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) for a in attrs for x in a[:2]), attrs
    assert "%% t_2fa_codes is a.2fa_codes" in m


def test_area_page_says_when_the_diagram_is_cut(tmp_path):
    from tests.test_explorer import big_area  # 41-table area
    g = build_graph(big_area())
    areas = A.cluster_areas(g)
    write_docs(tmp_path, g, areas, None)
    big = next(a for a in areas if "hq.accounts" in a["tables"])
    page = next(p for p in (tmp_path / "areas").glob("*.md") if f"{big['size']} tables" in p.read_text())
    text = page.read_text()
    assert "shows the 25 most connected" in text
    assert len(re.findall(r"^    \w+ \{$", text, re.M)) == 25
