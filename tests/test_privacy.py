"""No output of `keygraph build` may contain an absolute local path (context sources are relative or bare names)."""
import base64
import gzip
import re

from keygraph.cli import main, run_pipeline
from keygraph.context import ContextDB
from keygraph.graph import Graph

CTX = """
verified: true
concepts:
  - name: customer
    definition: Person who buys.
    links: [{table: shop.customers, role: primary-record}]
  - name: order
    links: [{table: shop.orders, role: primary-record}]
relations:
  - {from: customer, rel: places, to: order}
columns:
  - {table: shop.orders, column: status, meaning: Order state, codes: {"1": new}}
tables:
  - {table: shop.orders, grain: one row per order}
"""


def test_no_absolute_paths_in_outputs(tmp_path, dump_schema):
    ctxdir = tmp_path / "context"
    (ctxdir / "sub").mkdir(parents=True)
    (ctxdir / "sub" / "shop.yaml").write_text(CTX, encoding="utf-8")
    (tmp_path / "abs.yaml").write_text(f"source: {tmp_path}/notes/abs.yaml\n" + CTX.replace("Order state", "State"), encoding="utf-8")
    out = tmp_path / "out"
    run_pipeline(dump_schema, out, context=[str(ctxdir), str(tmp_path / "abs.yaml")], verbose=False)
    files = [out / "keygraph.html", out / "graph.json", *(out / "areas").glob("*.md"), *(out / "tables").glob("*.md")]
    assert len(files) > 4
    for f in files:
        assert str(tmp_path) not in text_of(f), f.name
    assert "(source: abs.yaml)" in (out / "graph.json").read_text(encoding="utf-8")  # an absolute source keeps its bare name


def text_of(f):
    """File text, with keygraph.html's embedded data block decoded when it is gzip+base64 (large graphs)."""
    text = f.read_text(encoding="utf-8")
    m = re.search(r'id="kg-data">GZ:([^<]*)</script>', text)
    if m:
        text += gzip.decompress(base64.b64decode(m.group(1))).decode("utf-8")
    return text


def test_text_of_decodes_gzip_block(tmp_path, dump_schema):
    from keygraph.explorer import build_html
    run_pipeline(dump_schema, tmp_path, verbose=False)
    g = Graph.load(tmp_path / "graph.json")
    f = tmp_path / "k.html"
    f.write_text(build_html(g, None, gzip_over=10), encoding="utf-8")
    raw = f.read_text(encoding="utf-8")
    assert 'id="kg-data">GZ:' in raw and '"order_items"' in text_of(f)[len(raw):]  # found in the decoded data


def test_old_db_rows_with_absolute_sources_never_leak(tmp_path, dump_schema, capsys):
    out = tmp_path / "out"
    run_pipeline(dump_schema, out, verbose=False)
    db = ContextDB(out / "keygraph.db", Graph.load(out / "graph.json"))
    absolute = f"{tmp_path}/private/notes/shop.yaml"
    for kind, item in (("concept", {"name": "customer", "links": [{"table": "shop.customers", "role": "primary-record"}]}),
                       ("concept", {"name": "order", "links": [{"table": "shop.orders", "role": "primary-record"}]}),
                       ("relation", {"from": "customer", "rel": "places", "to": "order"}),
                       ("table", {"table": "shop.orders", "grain": "one row per order"}),
                       ("column", {"table": "shop.orders", "column": "status", "meaning": "state"})):
        db.upsert(kind, item, "x.yaml")
    for t in ("concepts", "concept_links", "concept_relations", "column_semantics", "table_notes"):
        db.db.execute(f"UPDATE {t} SET source=?", (absolute,))  # what a db written before the fix holds
    db.db.commit()
    db.close()
    run_pipeline(dump_schema, out, verbose=False)
    files = [out / "keygraph.html", out / "graph.json", *(out / "areas").glob("*.md"), *(out / "tables").glob("*.md")]
    assert "(source: shop.yaml)" in (out / "graph.json").read_text(encoding="utf-8")
    for f in files:
        assert str(tmp_path) not in text_of(f), f.name
    assert main(["describe", "orders", "--dir", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "context source: shop.yaml" in printed and str(tmp_path) not in printed


def test_markup_in_context_cannot_break_the_page(tmp_path, dump_schema):
    nasty = "watch out <!-- not a comment --> and </script><b>x</b>"
    f = tmp_path / "c.yaml"
    f.write_text("tables:\n  - table: shop.orders\n    summary: '" + nasty + "'\n", encoding="utf-8")
    run_pipeline(dump_schema, tmp_path / "out", context=[str(f)], verbose=False)
    html = (tmp_path / "out" / "keygraph.html").read_text(encoding="utf-8")
    block = html.split('id="kg-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in block and "\\u003c!--" in block
