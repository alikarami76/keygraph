/* keygraph explorer prototype: five screens (map, area, path, table, search) plus a states sheet.
   Route by hash: #map  #area  #path  #table  #search  #states ; theme by ?theme=dark|light */
(function () {
  const D = window.KGDATA, $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const theme = () => document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const color = i => KGTHEME.schemaColor(i, theme());

  const I = {
    search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
    map: '<rect x="1.5" y="2" width="5" height="4" rx="1"/><rect x="9.5" y="2" width="5" height="4" rx="1"/><rect x="5.5" y="10" width="5" height="4" rx="1"/><path d="M6.5 4h3M5 6l2 4M11 6l-2 4"/>',
    area: '<rect x="1.5" y="2" width="6" height="5" rx="1"/><rect x="8.5" y="9" width="6" height="5" rx="1"/><path d="M7.5 4.5h2a1.5 1.5 0 0 1 1.5 1.5v3"/>',
    path: '<circle cx="3" cy="8" r="1.75"/><circle cx="13" cy="8" r="1.75"/><path d="M4.75 8h2M9.25 8h2"/><circle cx="8" cy="8" r="1"/>',
    table: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M2 6h12M6 6v7.5"/>',
    clock: '<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>',
    sun: '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"/>',
    moon: '<path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z"/>',
    copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>',
    warn: '<path d="M8 2.5l6 10.5H2z"/><path d="M8 6.5v3M8 11.2v.1"/>',
    sliders: '<path d="M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6"/><circle cx="10" cy="4.5" r="1.5"/><circle cx="6" cy="11.5" r="1.5"/>',
    plus: '<path d="M8 3v10M3 8h10"/>', minus: '<path d="M3 8h10"/>', fit: '<path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"/>',
    swap: '<path d="M3 5h10l-3-3M13 11H3l3 3"/>', arrowR: '<path d="M3 8h10M9.5 4.5L13 8l-3.5 3.5"/>', arrowL: '<path d="M13 8H3M6.5 4.5L3 8l3.5 3.5"/>',
    download: '<path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10"/>', close: '<path d="M4 4l8 8M12 4l-8 8"/>', ext: '<path d="M9 2.5h4.5V7M13.5 2.5L7.5 8.5M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3"/>',
    empty: '<rect x="6" y="9" width="16" height="12" rx="2.5"/><rect x="26" y="27" width="16" height="12" rx="2.5"/><path d="M22 15h4a3 3 0 0 1 3 3v9" stroke-dasharray="2 3"/>',
    nopath: '<circle cx="8" cy="24" r="4"/><circle cx="40" cy="24" r="4"/><path d="M12 24h8M28 24h8" /><path d="M21 21l6 6M27 21l-6 6"/>'
  };
  const icon = (n, cls = '') => `<svg class="kg-icon ${cls}" viewBox="0 0 16 16" aria-hidden="true">${I[n]}</svg>`;
  const bigIcon = n => `<svg viewBox="0 0 48 48" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${I[n]}</svg>`;
  const conf = c => { const b = KGTHEME.band(c); return `<span class="kg-conf" data-band="${b}" title="confidence ${c.toFixed(2)} (${b})"><span class="kg-conf-meter"><i></i><i></i><i></i><i></i></span>${c.toFixed(2)}</span>`; };
  const kind = k => `<span class="kg-chip" data-kind="${k}"><span>${k}</span></span>`;
  const keyTag = k => `<span class="kg-key kg-key--${k.split(' ')[0].toLowerCase()}">${k}</span>`;
  const warn = w => `<span class="kg-warn">${icon('warn')}${esc(w)}</span>`;
  const AREAC = { Orders: 0, Catalog: 1, Customers: 2, Sellers: 3, Payments: 4, Shipping: 5, Promotions: 6 };
  const ent = (full, glyph = 'table', col) => { const p = full.split('.'); const name = p.pop(); return `<a class="kg-entity">${glyph ? `<span class="kg-glyph kg-glyph--${glyph}" ${col ? `style="--kg-glyph-color:${col}"` : ''}></span>` : ''}<span class="kg-entity-name">${p.length ? `<span class="kg-entity-schema">${esc(p.join('.'))}</span>` : ''}${esc(name)}</span></a>`; };

  /* ---------- shell ---------- */
  const LOGO = window.KGLOGO;
  function rail(active) {
    const nav = [['map', 'Areas', '#map', D.stats.areas], ['path', 'Path', '#path']];
    return `<aside class="kg-rail">
      <div class="kg-rail-brand"><span class="kg-rail-logo">${LOGO}</span><span class="kg-rail-mark">${window.KGMARK}</span></div>
      <a class="kg-rail-search" href="#search" ${active === 'search' ? 'aria-current="page"' : ''}>${icon('search')}<span class="kg-nav-label">Search</span><span class="kg-kbd">/</span></a>
      <nav class="kg-nav">${nav.map(([i, l, h, n]) => `<a class="kg-nav-item" href="${h}" ${active === i || (i === 'map' && active === 'area') ? 'aria-current="page"' : ''}>${icon(i)}<span class="kg-nav-label">${l}</span>${n ? `<span class="kg-section-count">${n}</span>` : ''}</a>`).join('')}</nav>
      <div class="kg-rail-group kg-overline">Recent</div>
      <div class="kg-rail-list">${D.recent.map(r => `<a href="#table">${icon('table', 'kg-icon--sm')}${esc(r)}</a>`).join('')}</div>
      <div class="kg-rail-foot"><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm kg-rail-only-collapsed" title="Relations: weak, hubs" aria-label="Relation settings">${icon('sliders')}</button>
        <label class="kg-switch">Show weak relations<input type="checkbox"></label>
        <label class="kg-switch">Show hubs as edges<input type="checkbox"></label>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px">
          <span class="kg-rail-meta" title="built ${D.stats.built}">${D.stats.tables} tables · 4 schemas</span>
          <button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" id="themebtn" title="Switch theme" aria-label="Switch theme">${icon(theme() === 'dark' ? 'sun' : 'moon')}</button>
        </div>
      </div></aside>`;
  }
  function page(active, head, body, panel) {
    $('#app').innerHTML = `<div class="kg-app${panel ? '' : ' kg-app--panel-closed'}">${rail(active)}<section class="kg-view"><header class="kg-viewhead">${head}</header>${body}</section>${panel ? `<aside class="kg-panel">${panel}</aside>` : '<aside></aside>'}</div>`;
    $('#themebtn').onclick = () => { document.documentElement.setAttribute('data-theme', theme() === 'dark' ? 'light' : 'dark'); route(); };
  }
  const zoomCtl = `<div class="kg-overlay kg-overlay--br kg-zoom"><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="in" aria-label="Zoom in">${icon('plus')}</button><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="out" aria-label="Zoom out">${icon('minus')}</button><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="fit" aria-label="Fit">${icon('fit')}</button></div>`;
  const bindZoom = d => document.querySelectorAll('[data-z]').forEach(b => b.onclick = () => b.dataset.z === 'fit' ? d.fit() : d.zoomBy(b.dataset.z === 'in' ? 1.2 : 1 / 1.2));
  const legendSvg = (cls, dash) => `<svg viewBox="0 0 24 12"><path d="M1 6h22" fill="none" stroke="var(--kg-graph-edge-${cls})" stroke-width="1.6" ${dash ? `stroke-dasharray="${dash}"` : ''} ${cls === 'faint' ? '' : ''}/></svg>`;

  /* ---------- 1. Area map ---------- */
  function viewMap() {
    const s = D.stats;
    page('map',
      `<h1>Areas</h1><span class="kg-faint" style="font:var(--kg-type-small)">${s.areas} areas grouped from ${s.tables} tables · lines show relations that cross areas</span><span class="kg-spacer"></span>
       <div class="kg-seg" role="group" aria-label="Line weight"><button aria-pressed="true">All relations</button><button>Declared only</button></div>`,
      `<div class="kg-canvas" id="cv">${zoomCtl}</div>`,
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Database</span></div><h2 class="kg-panel-title">shop_production</h2>
        <div class="kg-panel-meta"><span><b>${s.tables}</b> tables</span><span><b>${s.columns}</b> columns</span><span><b>${s.relations}</b> relations</span></div></div>
       <div class="kg-panel-body">
        <details class="kg-section" open><summary>Relations<span class="kg-section-count">${s.relations}</span></summary><div class="kg-section-body">
          <div class="kg-stats"><div class="kg-stat"><b>${s.declared}</b><span>declared</span></div><div class="kg-stat"><b>${s.inferred}</b><span>inferred</span></div><div class="kg-stat"><b>${s.polymorphic}</b><span>polymorphic</span></div></div>
          <p class="kg-faint" style="margin:0;font:var(--kg-type-small)">Inferred relations below 0.60 confidence are hidden. Turn on “Show weak relations” to see them.</p></div></details>
        <details class="kg-section" open><summary>Largest areas</summary><div class="kg-section-body--flush">
          ${D.areas.filter(a => !a.other).sort((a, b) => b.count - a.count).slice(0, 6).map(a => `<div class="kg-level"><span class="kg-swatch" style="--kg-glyph-color:${color(a.ci)};margin-left:6px"></span><span>${esc(a.name)} <span class="kg-faint kg-mono" style="font-size:11.5px">${esc(a.top[0])}…</span></span><span class="kg-conf">${a.count}</span></div>`).join('')}
        </div></details>
        <details class="kg-section" open><summary>Hubs<span class="kg-section-count">${s.hubs}</span></summary><div class="kg-section-body">
          <p class="kg-muted" style="margin:0 0 10px;font:var(--kg-type-small)">Tables that almost everything points to. They are shown as labels on boxes, not as lines, so they don’t tangle every view.</p>
          <div class="kg-level" style="grid-template-columns:1fr auto;padding:4px 0">${ent('shop.users', 'table', color(2))}<span class="kg-faint" style="font:var(--kg-type-caption)">← 31 tables · mostly created_by_id</span></div><div class="kg-level" style="grid-template-columns:1fr auto;padding:4px 0">${ent('shop.sellers', 'table', color(3))}<span class="kg-faint" style="font:var(--kg-type-caption)">← 17 tables · seller_id</span></div></div></details>
        <details class="kg-section"><summary>Curate areas</summary><div class="kg-section-body"><p class="kg-muted" style="margin:0;font:var(--kg-type-small)">Rename, pin or merge areas in a context file; the next build applies it.</p></div></details>
       </div>`);
    const areas = D.areas.map(a => ({ ...a, color: color(a.ci) }));
    const d = KGDiagram.mount($('#cv')).map({ areas, links: D.links });
    setTimeout(() => d.fit({ pad: 40, minK: 0.7 }), 0); bindZoom(d);
    d.onSelect = () => location.hash = '#area';
  }

  /* ---------- 2. Area (ER) ---------- */
  function viewArea() {
    const tables = D.orderTables.map(t => ({ ...t, color: color(t.neighbour ? 1 : 0) }));
    page('area',
      `<nav class="kg-crumbs"><a href="#map">Areas</a><span class="sep">/</span><span aria-current="page" style="font:var(--kg-type-title)">Orders</span></nav>
       <span class="kg-faint" style="font:var(--kg-type-small)">9 tables · 11 joins inside · 8 neighbouring tables</span><span class="kg-spacer"></span>
       <button class="kg-btn kg-btn--sm">${icon('path', 'kg-icon--sm')}Path between selected</button>
       <button class="kg-btn kg-btn--sm kg-btn--ghost">${icon('download', 'kg-icon--sm')}Mermaid</button>`,
      `<div class="kg-canvas" id="cv">
        <div class="kg-overlay kg-legend kg-legend--inline" style="left:12px;bottom:66px"><div class="kg-legend-row">${legendSvg('declared')}declared</div><div class="kg-legend-row">${legendSvg('inferred', '5 4')}inferred, fainter when less sure</div><div class="kg-legend-row"><span class="kg-hub">→ users</span>hub shown as a label</div></div>
        <div class="kg-strip"><span class="kg-strip-label">Neighbouring tables</span><div class="kg-strip-items">${D.neighbours.map(n => `<button class="kg-nchip" ${n.added ? 'aria-pressed="true"' : ''}><span class="kg-swatch" style="--kg-glyph-color:${color(D.areas.find(a => a.name === n.area).ci)}"></span>${esc(n.t)}<small>${n.n}</small>${n.added ? '' : '<span class="plus">+</span>'}</button>`).join('')}</div></div>
        ${zoomCtl.replace('kg-zoom"', 'kg-zoom" style="bottom:66px"')}</div>`,
      panelTable());
    const d = KGDiagram.mount($('#cv'));
    d.er({ tables: tables.map(t => ({ ...t, selected: t.id === 'orders' })), edges: D.orderEdges }, { ranksep: 60, nodesep: 22 });
    const svg = $('#cv .kg-edges');
    svg.querySelectorAll('.kg-edge').forEach(g => { g.addEventListener('mouseenter', () => svg.classList.add('has-hover')); g.addEventListener('mouseleave', () => svg.classList.remove('has-hover')); });
    setTimeout(() => d.fit({ pad: 40, minK: 0.8, focus: 'orders', offsetY: -24 }), 0);
    bindZoom(d);
    d.onSelect = () => location.hash = '#table';
  }

  /* right panel: the selected table, compact */
  function panelTable() {
    const t = D.table;
    return `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--table" style="--kg-glyph-color:${color(0)}"></span><span class="kg-overline">Table · Orders</span><span class="kg-badge kg-badge--verified">verified</span></div>
      <h2 class="kg-panel-title"><span class="kg-entity-schema">shop</span>orders</h2>
      <div class="kg-panel-meta"><span><b>${t.rows}</b> rows</span><span><b>${t.cols}</b> columns</span><span><b>${t.relations}</b> relations</span></div>
      <div class="kg-panel-actions"><a class="kg-btn kg-btn--sm" href="#table">${icon('table', 'kg-icon--sm')}Open table</a><a class="kg-btn kg-btn--sm" href="#path">${icon('path', 'kg-icon--sm')}Path from here</a></div></div>
     <div class="kg-panel-body">
      <details class="kg-section" open><summary>What one row is</summary><div class="kg-section-body"><p style="margin:0">${esc(t.grain)}</p>
        <div class="kg-callout kg-callout--pitfall" style="margin-top:12px">${icon('warn')}<div><p><b>Joining items multiplies rows.</b> Sum <code class="kg-mono">total_amount</code> before you join <code class="kg-mono">order_items</code>, or totals count once per item.</p></div></div></div></details>
      <details class="kg-section" open><summary>Keys</summary><div class="kg-section-body--flush"><table class="kg-table"><tbody>
        ${t.keys.slice(0, 4).map(k => `<tr><td class="kg-c-keys">${keyTag(k.kind)}</td><td class="kg-c-mono">${k.cols.join(', ')}</td><td class="kg-c-type">${k.name}</td></tr>`).join('')}</tbody></table></div></details>
      <details class="kg-section" open><summary>Joins in this area<span class="kg-section-count">4</span></summary><div class="kg-section-body--flush">
        ${[['order_items', 'order_id', 'declared', 1], ['order_status_history', 'order_id', 'declared', 1], ['order_tags', 'order_id', 'inferred', .88], ['order_notes', 'order_id', 'inferred', .81]].map(([tt, c, k, cf]) => `<div class="kg-rel"><div class="kg-rel-line"><span class="kg-rel-dir">${icon('arrowL', 'kg-icon--sm')}</span>${ent('shop.' + tt, 'table', color(0))}${conf(cf)}</div><div class="kg-rel-join">${tt}.${c}<span class="o">=</span>orders.id</div></div>`).join('')}
      </div></details>
     </div>`;
  }

  /* ---------- 3. Path ---------- */
  function viewPath() {
    const p = D.path, AL = ['o', 'oi', 'pv', 'p', 'c'];
    const card = (t, i, last) => `<div class="kg-chain-card ${i === 0 || last ? 'kg-chain-card--end' : ''}" style="--kg-glyph-color:${color(t.ci)}"><div class="kg-tcard-head"><span class="kg-glyph kg-glyph--table"></span><span class="kg-entity-name">${esc(t.name)}</span><span class="kg-alias">${AL[i]}</span></div><div style="padding:4px 0">${t.rows.map(r => `<div class="kg-tcard-row"><span>${keyTag(r.key)}</span><span class="kg-entity-name">${esc(r.name)}</span></div>`).join('')}</div><div class="kg-chain-area">${esc(t.area)}</div></div>`;
    const colOf = x => x.split('.')[1]; const brk = x => esc(x).replace(/_/g, '_<wbr>');
    const step = (s, i) => `<div class="kg-step kg-step--${s.kind}"><div class="kg-step-on">${AL[i + 1]}.${brk(colOf(s.on[1]))}<br><span class="o">=</span> ${AL[i]}.${brk(colOf(s.on[0]))}</div><div class="kg-step-line"></div><div class="kg-step-meta">${conf(s.conf)}</div><div class="kg-step-why">${esc(s.card)}</div>${s.warns.length ? `<div class="kg-step-meta">${s.warns.map(warn).join('')}</div>` : ''}</div>`;
    let chain = card(p.tables[0], 0, false);
    p.steps.forEach((st, i) => { chain += `<div class="kg-chain-unit">${step(st, i)}${card(p.tables[i + 1], i + 1, i + 1 === p.tables.length - 1)}</div>`; });
    const sql = p.sql.map(([c, t]) => c ? `<span class="${c}">${esc(t)}</span>` : esc(t)).join('');
    page('path', `<h1>Path</h1><span class="kg-faint" style="font:var(--kg-type-small)">Ranked by confidence, then joins; hubs, fan-out and log tables cost extra</span>`,
      `<div style="display:grid;grid-template-rows:auto 1fr;min-height:0;background:var(--kg-canvas)">
        <div class="kg-pathbar"><div class="kg-pathfield"><small>From</small><span class="kg-glyph kg-glyph--table" style="--kg-glyph-color:${color(0)}"></span>shop.orders</div>
          <button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" aria-label="Swap">${icon('swap')}</button>
          <div class="kg-pathfield"><small>To</small><span class="kg-glyph kg-glyph--table" style="--kg-glyph-color:${color(1)}"></span>shop.categories</div>
          <span class="kg-spacer"></span><span class="kg-field-label">Max joins</span><div class="kg-seg"><button>3</button><button aria-pressed="true">4</button><button>5</button></div></div>
        <div class="kg-pathscroll">
          <div class="kg-route-head"><span class="kg-rank kg-rank--best">1</span><h2>Best route</h2>${conf(p.conf)}<span class="kg-chip">${p.hops} joins</span>${warn('1 fan-out step')}</div>
          <div class="kg-chain">${chain}</div>
          <div class="kg-sqlblock"><div class="kg-sqlblock-head"><span class="kg-overline">SQL</span><span class="kg-faint" style="font:var(--kg-type-caption)">aliases match the cards</span><span class="kg-spacer"></span><button class="kg-btn kg-btn--primary kg-btn--sm">${icon('copy', 'kg-icon--sm')}Copy SQL</button></div><pre class="kg-code">${sql}</pre></div>
          <div class="kg-alts"><h3 class="kg-overline">Other routes</h3>
            ${p.alts.map((a, i) => `<div class="kg-alt"><span class="kg-rank">${i + 2}</span><span class="kg-alt-route">${a.route.map(r => `<b>${esc(r)}</b>`).join('<span class="o">→</span>')}</span><span class="kg-alt-warns">${a.warns.map(warn).join('')}</span>${conf(a.conf)}</div>`).join('')}
          </div></div></div>`,
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Route 1 · evidence</span></div><h2 class="kg-panel-title" style="font:var(--kg-type-title)">orders → categories</h2>
        <div class="kg-panel-meta"><span>Confidence <b>0.88</b></span><span><b>4</b> joins</span><span><b>2</b> inferred</span></div></div>
       <div class="kg-panel-body">
        <details class="kg-section" open><summary>Why each step</summary><div class="kg-section-body--flush">
          ${p.steps.map((s, i) => `<div class="kg-rel"><div class="kg-rel-line"><span class="kg-hop">${i + 1}</span><span class="kg-mono" style="font-size:12px">${esc(s.on[1])}</span>${conf(s.conf)}</div><div class="kg-rel-tags">${kind(s.kind)}<span class="kg-chip">${esc(s.card)}</span>${s.warns.map(warn).join('')}</div>
            <details class="kg-rel-evidence" ${i === 1 || i === 0 ? 'open' : ''}><summary>Evidence</summary><ul>${s.kind === 'declared' ? '<li>Declared foreign key in the schema</li>' + (i === 0 ? '<li data-sign="-">One order has 2.7 items on average, so sums of order columns repeat</li>' : '') : '<li>Column name matches the table name</li><li>Types match (bigint = bigint)</li><li>Leading column of an index</li>' + (i === 3 ? '<li data-sign="-">Target is not unique on 0.2% of sampled rows</li>' : '')}</ul></details></div>`).join('')}
        </div></details>
        <details class="kg-section" open><summary>Ranking</summary><div class="kg-section-body"><p class="kg-muted" style="margin:0;font:var(--kg-type-small)">Score is the product of step confidences, then the number of joins. Passing through a hub, a one-to-many step or a log table lowers the score; each penalty is shown on its step.</p></div></details>
       </div>`);
  }

  /* ---------- 4. Table ---------- */
  function viewTable() {
    const t = D.table, c = D.column;
    const prof = p => p.range ? `<span class="kg-faint">${esc(p.range)}</span>` : p.top ? `<span class="kg-faint">top ${esc(p.top)}</span>` : `<span class="kg-faint">${p.distinct} distinct</span>`;
    const emptyCell = p => `<span class="kg-minibar" title="${p.empty}% empty"><i style="width:${Math.max(p.empty ? 3 : 0, p.empty)}%;background:${p.empty > 50 ? 'var(--kg-warning)' : 'var(--kg-text-3)'}"></i></span>${p.empty ? p.empty + '%' : '<span class="kg-faint">0</span>'}`;
    const rows = t.columns.map(col => `<tr ${col.selected ? 'aria-selected="true"' : ''}><td class="kg-c-name">${esc(col.n)}</td><td class="kg-c-type">${esc(col.t)}${col.null ? ' <span class="kg-chip kg-chip--mono" style="height:16px;padding:0 5px">null</span>' : ''}</td><td class="kg-c-keys"><span class="kg-flags" style="flex-wrap:nowrap">${col.keys.map(keyTag).join('')}</span></td>
      <td><span class="kg-flags" style="flex-wrap:nowrap">${col.hub ? `<span class="kg-hub">→ ${col.hub}</span>` : ''}${col.pii ? '<span class="kg-badge kg-badge--pii">PII</span>' : ''}${col.enum && col.enum.some(e => e[2]) ? '<span class="kg-badge kg-badge--fanout">undocumented 9</span>' : ''}</span></td>
      <td style="white-space:nowrap">${col.enum ? `<span class="kg-enum" style="flex-wrap:nowrap">${col.enum.slice(0, col.enum.length > 4 ? 3 : 4).map(e => `<code>${esc(e[0])}${e[1] ? ' ' + esc(e[1]) : ''}</code>`).join('')}${col.enum.length > 4 ? `<code>+${col.enum.length - 3}</code>` : ''}</span>` : prof(col.prof)}</td>
      <td class="kg-c-num">${emptyCell(col.prof)}</td></tr>`).join('');
    const relItem = i => `<div class="kg-relitem">${ent('shop.' + i.t, 'table', color(AREAC[i.area] ?? 7))}<span class="kg-faint" style="font:var(--kg-type-caption)">${esc(i.area)}</span><span style="display:flex;gap:8px;align-items:center">${i.kind === 'declared' ? '<span class="kg-chip" data-kind="declared"><span>declared</span></span>' : conf(i.conf)}<span class="kg-relitem-acts"><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" title="Path from here">${icon('path', 'kg-icon--sm')}</button></span></span></div>`;
    page('table', `<nav class="kg-crumbs"><a href="#map">Areas</a><span class="sep">/</span><a href="#area">Orders</a><span class="sep">/</span><span aria-current="page">shop.orders</span></nav><span class="kg-spacer"></span>
        <a class="kg-btn kg-btn--sm" href="#path">${icon('path', 'kg-icon--sm')}Path from here</a><a class="kg-btn kg-btn--sm" href="#area">${icon('area', 'kg-icon--sm')}Open area</a><button class="kg-btn kg-btn--sm kg-btn--ghost">${icon('download', 'kg-icon--sm')}Markdown</button>`,
      `<div class="kg-doc"><div class="kg-doc-inner">
        <div class="kg-doc-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--table" style="--kg-glyph-color:${color(0)}"></span><span class="kg-overline">Table in Orders</span><span class="kg-badge kg-badge--verified">context verified</span></div>
          <h1 class="kg-doc-title"><span class="kg-entity-schema">shop</span>orders</h1><p class="kg-doc-grain">${esc(t.grain)}</p>
          <div class="kg-doc-facts"><span class="kg-fact"><b>${t.rows}</b> rows</span><span class="kg-fact"><b>${t.cols}</b> columns</span><span class="kg-fact"><b>${t.relations}</b> relations</span><span class="kg-fact">${esc(t.freshness)}</span><span class="kg-fact">soft delete: <code class="kg-mono" style="font-size:11.5px">is_deleted</code></span></div></div>
        <h2>Columns <span class="kg-section-count">${t.columns.length} of ${t.cols}</span></h2>
        <div class="kg-boxed"><table class="kg-table kg-table--clickable kg-cols-grid"><thead><tr><th>Column</th><th>Type</th><th>Keys</th><th>Flags</th><th>Values</th><th style="text-align:right">Empty</th></tr></thead><tbody>${rows}</tbody></table></div>
        <h2>Keys and indexes</h2>
        <div class="kg-boxed"><table class="kg-table"><thead><tr><th style="width:1%">Kind</th><th>Columns, in order</th><th>Name</th></tr></thead><tbody>${t.keys.map(k => `<tr><td>${keyTag(k.kind)}</td><td class="kg-c-mono">${k.cols.map((x, i) => `${i ? '<span class="kg-faint"> → </span>' : ''}${esc(x)}`).join('')}</td><td class="kg-c-type">${esc(k.name)}</td></tr>`).join('')}</tbody></table></div>
        <div class="kg-two">
          <div><h2>Points to <span class="kg-section-count">3</span></h2><div class="kg-relgroup">
            ${t.pointsTo.map(g => `<div class="kg-relgroup-head">via <code class="kg-mono">${g.col}</code></div>${g.items.map(relItem).join('')}`).join('')}
            <div class="kg-hubline">Hubs <span class="kg-hub">→ users</span><span class="kg-faint">via user_id, created_by_id</span></div></div></div>
          <div><h2>Pointed to by <span class="kg-section-count">15</span></h2><div class="kg-relgroup">
            ${t.pointedBy.map(g => `<div class="kg-relgroup-head">via <code class="kg-mono">orders.${g.col}</code></div>${g.items.map(relItem).join('')}${g.collapsed.map(x => `<div class="kg-relcollapsed">${x.n} tables via <code class="kg-mono">${x.via}</code><span class="kg-faint">in ${esc(x.areas)}</span><a>Show</a></div>`).join('')}`).join('')}</div></div>
        </div>
      </div></div>`,
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--column"></span><span class="kg-overline">Column</span></div>
        <h2 class="kg-panel-title"><span class="kg-entity-schema">orders</span>status</h2>
        <div class="kg-panel-meta"><span class="kg-mono">${c.type} · not null</span><span>${keyTag('IX 1')} in ix_orders_status_updated</span></div></div>
       <div class="kg-panel-body">
        <details class="kg-section" open><summary>Meaning</summary><div class="kg-section-body"><p style="margin:0">${esc(c.meaning)}</p></div></details>
        <details class="kg-section" open><summary>Codes<span class="kg-section-count">7 seen · 6 documented</span></summary><div class="kg-section-body">
          ${c.codes.map(([k, l, pc, u]) => `<div class="kg-profile-row"><span>${esc(k)} <span class="${u ? '' : 'kg-muted'}" style="${u ? 'color:var(--kg-warning)' : ''}">${esc(l)}</span></span><span class="kg-profile-bar"><i style="width:${Math.max(1.5, pc)}%;${u ? 'background:var(--kg-warning)' : ''}"></i></span><span>${pc}%</span></div>`).join('')}
          <div class="kg-callout kg-callout--pitfall" style="margin-top:12px">${icon('warn')}<div><p>Code <b>9</b> appears in 0.3% of rows but no context file documents it.</p></div></div></div></details>
        <details class="kg-section" open><summary>Use</summary><div class="kg-section-body"><dl class="kg-kv"><dt>Filter</dt><dd>${esc(c.filters)}</dd><dt>Index</dt><dd>${esc(c.index)}</dd><dt>Metrics</dt><dd>${c.metrics.map(m => `<span class="kg-entity"><span class="kg-glyph kg-glyph--metric"></span><span class="kg-entity-name" style="font-family:var(--kg-font-sans);font-size:13px">${m}</span></span>`).join('<br>')}</dd></dl></div></details>
        <details class="kg-section"><summary>References<span class="kg-section-count">none</span></summary></details>
       </div>`);
  }

  /* ---------- 5. Search ---------- */
  function viewSearch() {
    const s = D.search; let first = true;
    page('search', `<h1>Search</h1><span class="kg-faint" style="font:var(--kg-type-small)">Names, synonyms, context notes and profiled values</span>`,
      `<div class="kg-doc"><div class="kg-doc-inner" style="max-width:820px">
        <div class="kg-search-big">${icon('search')}<input value="${s.q}" aria-label="Search"></div>
        <div class="kg-search-filters"><button class="kg-toggle" aria-pressed="true">All <span class="kg-faint">9</span></button><button class="kg-toggle">Concepts 1</button><button class="kg-toggle">Metrics 1</button><button class="kg-toggle">Tables 2</button><button class="kg-toggle">Columns 2</button><button class="kg-toggle">Values 2</button><span class="kg-spacer"></span><span class="kg-faint" style="font:var(--kg-type-caption);align-self:center"><span class="kg-kbd">↵</span> open  <span class="kg-kbd">↑</span><span class="kg-kbd">↓</span> move</span></div>
        ${s.groups.map(g => `<div class="kg-result-group"><h3 class="kg-overline">${g.type}</h3>${g.items.map(it => { const sel = first; first = false; return `<div class="kg-result" ${sel ? 'aria-selected="true"' : ''}><span class="kg-glyph kg-glyph--${g.glyph}" style="--kg-glyph-color:${color(AREAC[it.meta] ?? 4)}"></span><span class="kg-result-title ${it.sans ? 'is-sans' : ''}">${it.title.replace(/^([^<]*)$/, m => m.replace(/(refund)/i, '<mark>$1</mark>'))}</span><span class="kg-reason">${esc(it.meta)}</span><span class="kg-result-why"><span class="kg-chip" style="height:18px;margin-right:6px">${it.why[0]}</span>${it.why[1]}</span></div>`; }).join('')}</div>`).join('')}
      </div></div>`,
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--concept"></span><span class="kg-overline">Concept</span><span class="kg-badge kg-badge--verified">verified</span></div><h2 class="kg-panel-title" style="font-family:var(--kg-font-sans)">Refund</h2>
        <div class="kg-panel-meta"><span>Also: money back, chargeback, reembolso</span></div></div>
       <div class="kg-panel-body"><details class="kg-section" open><summary>Definition</summary><div class="kg-section-body"><p style="margin:0">Money returned to a customer after a payment, in full or in part. A return usually leads to a refund; a chargeback is a refund the card network forces.</p></div></details>
        <details class="kg-section" open><summary>Where it lives<span class="kg-section-count">4</span></summary><div class="kg-section-body--flush">
          ${[['billing.payment_refunds', 'table', 'one row per refund'], ['billing.payments.refunded_amount', 'column', 'running total'], ['billing.payments.status', 'column', 'code 7 = refunded'], ['shop.returns.refund_id', 'column', 'set when paid out']].map(([n, g, w]) => `<div class="kg-level" style="grid-template-columns:1fr auto">${ent(n, g)}<span class="kg-faint" style="font:var(--kg-type-caption)">${w}</span></div>`).join('')}</div></details>
        <details class="kg-section" open><summary>Related</summary><div class="kg-section-body"><div style="display:flex;gap:6px;flex-wrap:wrap"><span class="kg-chip">Return</span><span class="kg-chip">Payment</span><span class="kg-chip">Chargeback</span></div></div></details></div>`);
  }

  /* ---------- states sheet ---------- */
  function viewStates() {
    const box = (title, inner) => `<div><div class="kg-overline" style="margin-bottom:8px">${title}</div><div class="kg-boxed" style="background:var(--kg-surface);min-height:220px;display:grid;align-content:center">${inner}</div></div>`;
    page('map', `<h1>States</h1><span class="kg-faint" style="font:var(--kg-type-small)">Empty, loading, error and no-result states every view uses</span>`,
      `<div class="kg-doc" style="background:var(--kg-canvas)"><div class="kg-doc-inner" style="max-width:none;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px">
        ${box('First open (no selection)', `<div class="kg-empty">${bigIcon('empty')}<h3>Start with an area or a search</h3><p>Pick an area on the map, search for a table, column or business term, or find a path between two tables.</p><div class="kg-toolbar"><button class="kg-btn kg-btn--primary kg-btn--sm">${icon('search', 'kg-icon--sm')}Search</button><button class="kg-btn kg-btn--sm">Find a path</button></div></div>`)}
        ${box('Loading a large graph', `<div style="padding:24px;display:grid;gap:12px"><div style="display:flex;align-items:center;gap:10px;font:var(--kg-type-small);color:var(--kg-text-2)"><span class="kg-spinner"></span>Indexing 2,912 tables for search…</div><span class="kg-skeleton" style="width:70%"></span><span class="kg-skeleton" style="width:92%"></span><span class="kg-skeleton" style="width:54%"></span><span class="kg-skeleton" style="width:80%"></span></div>`)}
        ${box('Data could not be read', `<div style="padding:24px"><div class="kg-error">${icon('warn')}<div><b>This file’s embedded graph is damaged.</b><br><span class="kg-muted">Rebuild it with <code class="kg-mono">keygraph build</code>. Details: unexpected end of JSON at byte 1,048,576.</span></div></div></div>`)}
        ${box('No path found', `<div class="kg-empty">${bigIcon('nopath')}<h3>No route within 4 joins</h3><p>shop.reviews and billing.payouts don’t connect above 0.60 confidence. Allow 5 joins or show weak relations.</p><div class="kg-toolbar"><button class="kg-btn kg-btn--sm">Allow 5 joins</button><button class="kg-btn kg-btn--sm kg-btn--ghost">Show weak relations</button></div></div>`)}
        ${box('No search results', `<div class="kg-empty">${bigIcon('empty')}<h3>Nothing matches “ledgr”</h3><p>Did you mean <a class="kg-entity"><span class="kg-entity-name">ledger_entries</span></a>? Search also reads synonyms and context notes.</p></div>`)}
        ${box('Area too large', `<div style="padding:20px"><div class="kg-callout">${icon('table')}<div><p><b>Showing the 25 most connected of 61 tables.</b></p><p class="kg-muted">Large canvases stop being readable. Show all, or open a table to see its own joins.</p><div class="kg-toolbar" style="margin-top:10px;display:flex;gap:8px"><button class="kg-btn kg-btn--sm">Show all 61</button></div></div></div></div>`)}
      </div></div>`, null);
  }

  const views = { map: viewMap, area: viewArea, path: viewPath, table: viewTable, search: viewSearch, states: viewStates };
  function route() { (views[location.hash.slice(1)] || viewMap)(); }
  const q = new URLSearchParams(location.search).get('theme'); if (q) document.documentElement.setAttribute('data-theme', q);
  window.addEventListener('hashchange', route);
  document.fonts.ready.then(route);
})();
