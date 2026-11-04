#!/usr/bin/env python3
"""Builds page.html for the how-it-works PDF. Real CLI output comes from the demo in examples/shop/out
(build it first with examples/shop/build.sh). Run from anywhere: python3 docs/how-it-works/build_page.py"""
import html
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = "examples/shop/out"
BIN = pathlib.Path(sys.executable).parent


def cli(*args, head=None, drop=(), pick=None, show=None):
    """Run keygraph and return an escaped '$ command' block."""
    cmd = ["keygraph", *args]
    r = subprocess.run(["keygraph", *args], cwd=ROOT, capture_output=True, text=True,
                       env={"PATH": f"{ROOT / '.venv/bin'}:/usr/bin:/bin", "HOME": "/nonexistent"})
    lines = [l.rstrip() for l in (r.stdout or r.stderr).splitlines()]
    lines = [l for l in lines if not any(d in l for d in drop)]
    if pick:
        lines = [l for l in lines if any(p in l for p in pick)]
    if head:
        lines = lines[:head]
    shown = show or " ".join(cmd)
    txt = "\n".join([f"$ {shown}", *lines])
    txt = txt.replace("⚠", "[!]").replace("→", "->").replace("—", "-")
    return f'<pre class="cli">{html.escape(txt)}</pre>'


def code(txt, cls="cli"):
    return f'<pre class="{cls}">{html.escape(txt.strip(chr(10)))}</pre>'


pages = []


def page(kicker, title, lead, body, cls=""):
    n = len(pages) + 2  # the cover is page 1
    pages.append(f'''<section class="page {cls}">
<header><div class="kicker">{kicker}</div><h1>{title}</h1><p class="lead">{lead}</p></header>
<div class="body">{body}</div>
<footer><img class="fmark" src="assets/keygraph-mark.svg" alt=""><span>keygraph &middot; how it works</span><span class="pn">{n}</span></footer>
</section>''')


def shot(name, alt):
    return f'<figure class="shot"><img src="shots/{name}.png" alt="{alt}"></figure>'


def notes(title, items, extra=""):
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f'<aside class="notes"><h3>{title}</h3><ul>{lis}</ul>{extra}</aside>'


# ---- 2 what it is
page("Overview", "keygraph turns a database's keys into a map you can query",
     "It reads a database's structure, works out how its tables relate, adds what the business calls things, and writes an "
     "explorer for people and plain files for AI agents. It does this without calling an LLM, so the same input always gives the same output.",
     '''<div class="stats">
<div><b>35</b><span>tables in 3 schemas</span></div><div><b>56</b><span>relations: 18 declared, 38 inferred</span></div>
<div><b>4</b><span>business areas</span></div><div><b>1</b><span>hub table (users)</span></div><div><b>1</b><span>mirror schema (reporting)</span></div></div>
<div class="cols2">
<div><h3>What you get</h3><ul>
<li><b>A map.</b> Every primary key, unique key and index (with column order), every declared foreign key, and the relations that were never declared, each with a confidence score and the evidence behind it.</li>
<li><b>Meaning.</b> Concepts, metrics, column codes ("3 = shipped") and what one row of a table is, so a search for "revenue" finds the right table.</li>
<li><b>Real values.</b> Top values, empty rates and ranges sampled from the data, with personal data masked.</li></ul></div>
<div><h3>What it never does</h3><ul>
<li>It never writes to your database. It reads metadata, and it samples values only when you run <code>profile</code>, with one bounded SELECT per table.</li>
<li>It never sends anything anywhere. The explorer is one HTML file that works offline.</li>
<li>It never guesses silently. A relation that is only a guess is drawn dashed and carries its score.</li></ul>
<p class="small">Every example in this guide comes from a made-up webshop in <code>examples/shop</code>. You can rebuild all of it in a minute.</p></div></div>
<img class="wide mid" src="assets/how-it-works-light.png" alt="The four stages: sources, keys and relations, context and profiles, outputs">''')

# ---- 3 connect
page("Step 1", "Connect a source: scan a live database or load dumps",
     "keygraph needs the structure of your tables and nothing else. Point it at a database it can read, or hand it files that came from information_schema.",
     f'''<div class="cols2"><div>
<h3>Live database (read-only)</h3>{code("keygraph scan --url postgresql://user@host/db --schema public")}
<h3>information_schema dumps, when you cannot connect</h3>{code("""keygraph load --dir out \\
  --columns columns.json --statistics statistics.json \\
  --key-columns key_columns.json --tables tables.json \\
  --context context.yaml""")}
<p>Dumps use information_schema field names. <code>--columns-map</code>, <code>--flatten</code> and <code>--where</code> adapt other shapes. Without STATISTICS, keygraph falls back to COLUMN_KEY. Without KEY_COLUMN_USAGE, every relation is inferred.</p>
</div><div>
<h3>What it printed for the demo</h3>{cli("load", "--dir", OUT, "--columns", "examples/shop/dumps/columns.json", "--statistics", "examples/shop/dumps/statistics.json", "--key-columns", "examples/shop/dumps/key_columns.json", "--tables", "examples/shop/dumps/tables.json", "--context", "examples/shop/context.yaml", show="keygraph load --dir out --columns ... --context context.yaml", drop=["pages"])}
<h3>Sources</h3>
<table class="t"><tr><th>Source</th><th>How</th></tr>
<tr><td>MySQL, MariaDB</td><td>information_schema</td></tr><tr><td>PostgreSQL</td><td>information_schema, pg_catalog</td></tr>
<tr><td>SQLite, SQL Server</td><td>SQLAlchemy, INFORMATION_SCHEMA</td></tr><tr><td>ClickHouse, MongoDB</td><td>system tables, sampled documents</td></tr>
<tr><td>Metabase</td><td>its REST metadata</td></tr><tr><td>Anything else</td><td><code>fetch</code> through a read-only command</td></tr></table>
</div></div>
<p class="callout">Result: a folder with <code>graph.json</code>, <code>keygraph.db</code> (SQLite with full-text search), <code>keygraph.html</code> (the explorer), <code>RELATIONS_REPORT.md</code> and the agent pages in <code>areas/</code> and <code>tables/</code>.</p>''')

# ---- 4 keys
page("Step 2a", "Keys and indexes are read straight from the schema",
     "Before any guessing, keygraph records what the database says. That tells you which columns are safe to filter on, and which indexes are wasted.",
     f'''<div class="cols2"><div>{cli("keys", "shop.orders", "--dir", OUT, drop=["engine:"])}</div><div>
<h3>How to read it</h3><ul>
<li><b>Column order matters.</b> An index on (customer_id, placed_at) helps a filter on customer_id, or on both. It does not help a filter on placed_at alone. That is why the list shows each index in column order.</li>
<li><b>Efficient filters</b> are the leading columns of the primary key, unique keys and indexes. Filter on these first when a table has millions of rows.</li>
<li><b>Redundant indexes</b> are flagged. Here <code>ix_orders_customer_id</code> is a prefix of <code>ix_orders_customer_placed</code>, so it only costs write speed.</li>
<li><b>Table facts</b> such as engine, row estimate and comment come from <code>--tables</code>. The row estimate (5.2M) is what makes the explorer warn before you scan a big table.</li></ul>
<p class="callout">The same facts, plus column types, defaults, enum members and comments, are in <code>keygraph describe</code> and on each table page of the explorer.</p></div></div>''')

# ---- 5 inferred
page("Step 2b", "Missing foreign keys are inferred, with a score and the reasons",
     "Many databases declare few foreign keys. keygraph matches columns such as order_id to a table called orders, scores the match, and shows every reason it used.",
     f'''<div class="cols2"><div>{cli("explain", "billing.payments.order_id", "--dir", OUT, drop=["values:"])}
<p class="small">This relation is not declared in the demo. It crosses two schemas, which lowers the score a little, and the index on the column raises it.</p></div><div>
<h3>What changes the score</h3><table class="t"><tr><th>Raises it</th><th>Lowers it</th></tr>
<tr><td>Declared foreign key (always 1.0)</td><td>Type mismatch</td></tr><tr><td>Name matches a table</td><td>Match across schemas</td></tr>
<tr><td>Target is a primary key</td><td>More than one possible target</td></tr><tr><td>Index on the column</td><td>Values barely overlap (after <code>probe</code>)</td></tr>
<tr><td>A business relation in the context file</td><td>No key on the target</td></tr></table>
<p><b>Read the number like this.</b> 0.8 and above: use it. 0.6 to 0.8: read the evidence first. Below 0.6: hidden until you switch on "Show weak relations" (floor 0.4), and check it with a small SELECT.</p>
<p>Role columns such as <code>created_by_id</code> are recognised as pointers to a staff table. Polymorphic pairs such as <code>target_type</code> + <code>target_id</code> have no single target and are marked as such.</p></div></div>
<img class="wide small-img" src="assets/relations-legend-light.png" alt="Legend: declared, inferred, concept, polymorphic and hub lines">''')

# ---- 6 hubs and areas
page("Step 3", "Hubs and areas turn 35 tables into 4 pictures",
     "A table that half the database points to would make every drawing unreadable. keygraph shows such hubs as labels, groups the rest into business areas, and keeps copied schemas out of the way.",
     f'''<div class="cols2"><div>{cli("areas", "--dir", OUT)}
{cli("area", "Ordering", "--dir", OUT, head=25)}</div><div>
<h3>Hubs</h3><p>A hub is a table in the top 1% by incoming relations, or one where at least 60% of the incoming relations come from role columns such as <code>created_by_id</code>. In the demo, <code>shop.users</code> receives 13 such relations. It appears as a small "users" label on each box instead of 13 lines.</p>
<h3>Areas</h3><p>Tables that join each other form an area. The grouping is deterministic: the same dumps always give the same areas. A short curation in <code>context.yaml</code> renames or moves tables, so "Ordering" and "Fulfilment" read the way your team talks. Tables with no relations once hubs are removed land in "Unconnected".</p>
<h3>Mirrors</h3><p><code>reporting</code> holds copies of four <code>shop</code> tables. keygraph detects that, marks the schema as a mirror of <code>shop</code>, and leaves it out of areas, hub counts and join paths so nothing is counted twice. <code>--include-mirrors</code> brings it back.</p>
{code("""areas:
  - name: Fulfilment
    pin: [shop.shipments, shop.shipment_items,
          shop.warehouses, shop.inventory, shop.returns]""", "cli small")}</div></div>''', "dense")

# ---- 7 context
page("Step 4a", "Business context says what the tables mean",
     "A schema knows column names. It does not know that status 3 means shipped or that orders_shadow is scratch space. You write that down once, in a YAML file, and keygraph keeps it next to the schema.",
     f'''<div class="cols2"><div>{code("""concepts:
  - name: customer
    definition: A person with an account who can place orders.
    synonyms: [buyer, shopper, account holder]
    links: [{table: shop.customers, role: primary-record}]
columns:
  - table: shop.orders
    column: status
    meaning: Order lifecycle state
    codes: {"1": placed, "2": paid, "3": shipped, "4": cancelled}
metrics:
  - name: net revenue
    expression: SUM(order_items.qty * order_items.unit_price - order_items.discount)
    tables: [shop.order_items, shop.orders]
tables:
  - table: shop.order_items
    grain: one row per variant on an order
    pitfalls: Joining this table to shipments repeats each line.""")}
<p class="small">Also supported: concept relations, <code>pii: true</code> flags, markdown notes per table (<code>context import-notes</code>) and <code>context add</code> on the command line. Unknown tables only warn.</p></div><div>
{cli("search", "revenue", "--dir", OUT, head=11)}
<p>Search ranks tables by names, synonyms, concepts, metrics, notes, column codes and profiled values. Each hit says why it matched, what one row is, which columns to filter on and what to watch out for.</p>
<p class="callout">Every context row records its source file and whether someone verified it, and it can be exported again with <code>keygraph context export</code>.</p></div></div>''')

# ---- 8 profiles
page("Step 4b", "Value profiles show what is really in the columns",
     "The context file says what should be there. A profile samples the data and shows what is. It also compares the two, and it never stores personal values.",
     f'''<div class="cols2"><div>
{code("""keygraph profile --dir out --source sqlite \\
  --url sqlite:///shop.db --bare-tables \\
  --tables shop.customers,shop.orders,shop.order_items""")}
{cli("profile", "--dir", OUT, "--source", "sqlite", "--url", "sqlite:///examples/shop/shop.db", "--bare-tables", "--tables", "shop.customers,shop.orders,billing.payments", show="keygraph profile ... (output)", drop=["profiled shop", "profiled billing"])}
<p>One bounded SELECT per table, never a full scan. Each profile records when it was taken, how many rows and by which method, so a reader knows how fresh and approximate it is.</p></div><div>
<h3>Personal data stays out</h3>
{cli("describe", "shop.customers", "--dir", OUT, pick=["email varchar", "first_name", "status tinyint"], show="keygraph describe shop.customers  (3 of 8 columns)")}
<p>PII columns are recognised by name (email, phone, name, address, token ...), by value pattern, or by a <code>pii</code> flag. For them keygraph keeps counts, empty rate, length and the pattern name. It never stores a top value, a minimum or a maximum.</p></div></div>''')

# ---- 9-14 explorer views
def view(kicker, title, lead, name, alt, items, extra=""):
    page(kicker, title, lead, f'<div class="viewrow">{shot(name, alt)}{notes("What to look for", items, extra)}</div>', "viewpage")


view("Explorer 1 of 6 · Areas", "The Areas view is the map of the whole database",
     "Start here when you do not know where something lives. Each box is a business area; the number on a line is how many relations join two areas.",
     "areas", "Area map of the demo with four areas and a side panel",
     ["Each box shows the area name, its table count, one square per table and its three busiest tables.",
      "Line thickness follows the number of relations between two areas. Hubs never draw lines here.",
      "The side panel counts relations by kind, lists the hubs and the mirror schema, and opens the area curation help.",
      "Click a box to open that area."])
view("Explorer 2 of 6 · Area", "The Area view draws one area as a table diagram",
     "Boxes are tables listing their key columns. Lines attach to the exact column that joins, so you can see which column does the work.",
     "area", "ER diagram of the Ordering area",
     ["Solid lines are declared foreign keys. Dashed lines are inferred, with the confidence on the line.",
      "The pill on <code>orders</code> reads 'users': the hub reference, shown as a label and not as a line.",
      "'+7 columns' says how much is hidden. Open the table to see all of it.",
      "Neighbouring tables from other areas wait in the strip at the bottom. An area never draws more than about 40 boxes, and over 25 tables it first shows the 25 most connected."])
view("Explorer 3 of 6 · Path", "The Path view finds the join between two tables",
     "Pick two tables and get ranked routes, a copyable JOIN, and a warning at every step that can multiply rows.",
     "path", "Best route from customers to shipments with SQL and evidence",
     ["The best route is customers, orders, shipments: two declared joins.",
      "Both steps carry a fan-out warning. One customer has many orders, so summing a customer column over this join counts it many times. The SQL comment says to aggregate first.",
      "Routes through a hub, a one-to-many step or a scratch table (<code>*_shadow</code>, <code>*_log</code>) score lower. The side panel shows each step's evidence."])
view("Explorer 4 of 6 · Table", "The Table view explains one table as a page",
     "This is the page to read before you query a table: what a row is, what to avoid, which metrics use it and how its columns look.",
     "table", "Table page for shop.orders",
     ["The header gives the row grain, row estimate, column and relation counts and the date column.",
      "The orange box is the pitfall from the context file. The purple items are metrics that use this table, with their expressions.",
      "The column grid shows keys, flags, value ranges and how often a column is empty.",
      "Relations below the grid are grouped by column and area; groups over 8 items collapse."])
view("Explorer 5 of 6 · Column", "The Column view shows what a column holds and how it is used",
     "For status columns this is where the documented codes meet the real data.",
     "column", "Column page for orders.status with value frequencies",
     ["The bars compare the documented codes with the sampled values. Code 9 appears in 6.7% of rows and the context file does not know it, so it is flagged.",
      "The profile block says when and how the sample was taken. 'Null means' comes from the context file.",
      "The side panel lists the indexes that cover the column and the joins it takes part in."])
view("Explorer 6 of 6 · Search", "Search finds tables, columns, concepts and values by meaning",
     "Type a business word. Results are grouped by type and each says why it matched.",
     "search", "Search results for revenue",
     ["Metrics, concepts, tables, columns and profiled values come from one index, so a status label such as 'in transit' finds the column that stores it.",
      "A reason chip shows the match: name, synonym, context, enum label or profiled value.",
      "Press Enter to open the top result in the view that fits it. The side panel previews the highlighted one."])

# ---- 15 agents
page("For agents", "An agent uses the same graph through a short command sequence",
     "Every command prints plain text or JSON (<code>--json</code>), and the build also writes markdown and Mermaid pages that an agent can read like any other file.",
     f'''<div class="cols2"><div>
<h3>The sequence</h3>
<table class="t seq"><tr><th>Need</th><th>Command</th></tr>
<tr><td>Where does this live?</td><td><code>keygraph search "net revenue"</code></td></tr>
<tr><td>What is around it?</td><td><code>keygraph areas</code>, <code>keygraph area Ordering</code></td></tr>
<tr><td>What is this table?</td><td><code>keygraph describe shop.orders</code></td></tr>
<tr><td>How do I join A to B?</td><td><code>keygraph path A B</code></td></tr>
<tr><td>Why this relation?</td><td><code>keygraph explain T.col</code></td></tr>
<tr><td>Which filter is fast?</td><td><code>keygraph keys T</code></td></tr></table>
{cli("path", "shop.customers", "shop.shipments", "--dir", OUT, "-k", "1", head=12, drop=["business relation"])}
</div><div>
<h3>Files an agent can read</h3>
{code("""areas/index.md        every area with its top tables
areas/ordering.md     tables, joins, Mermaid ER diagram
tables/shop.orders.md grain, keys, columns,
                      points to / pointed to by
RELATIONS_REPORT.md   all relations with evidence
graph.json            everything, machine readable""")}
{code("""erDiagram
    orders {
        bigint_unsigned id PK
        bigint_unsigned customer_id FK
    }
    %% orders hub ref to shop.users via created_by_id""", "cli small")}
<p>The <code>keygraph</code> skill in <code>skills/keygraph/SKILL.md</code> tells an agent when to use each command and how to read confidence: use 0.8 and above freely, check 0.6 to 0.8 against the evidence, and verify anything lower, ambiguous or marked fan-out with a small SELECT.</p>
<p class="callout">Corrections go back in: <code>context add</code> and an overrides file (<code>confirm</code>, <code>reject</code>, <code>add</code>) make the next build better.</p></div></div>''')

# ---- assemble
cover = '''<section class="page cover">
<img class="logo" src="assets/keygraph-logo-light.svg" alt="keygraph">
<h1>How keygraph works</h1>
<p class="lead">From a database to a map that people and AI agents can use. Each step shows real output from the demo webshop in examples/shop.</p>
<img class="wide" src="assets/how-it-works-light.png" alt="The four stages: sources, keys and relations, context and profiles, outputs">
<ol class="toc"><li>What it is</li><li>Connect a source</li><li>Keys, indexes and inferred relations</li><li>Hubs and areas</li><li>Business context and value profiles</li><li>The six explorer views</li><li>How an agent uses it</li></ol>
<p class="small">Open source, MIT licence. Everything shown here is fictional.</p>
</section>'''

css = (HERE / "style.css").read_text()
doc = f'''<style id="rs">{css}</style><script>document.head.appendChild(document.getElementById('rs'))</script>
{cover}
{"".join(pages)}'''
(HERE / "page.html").write_text(doc)
print(len(pages) + 1, "pages")
