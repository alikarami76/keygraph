/* keygraph explorer: the question-first views (Area map, Area, Path, Table, Column, Search) over the embedded graph.
 *
 * Routes (hash, so Back and Forward work):  #areas  #area/<id>  #table/<schema.table>  #col/<schema.table>/<col>
 *                                           #path/<a>/<b>  #search/<q>
 * Every view is a plain function that renders the whole app from `state`. No canvas draws more than
 * MAX_CANVAS_NODES boxes; anything bigger is grouped and says what it hides. `window.__kg.maxNodesDrawn` is the
 * number of boxes the current view drew (0 for document views).
 * Layout and look come from the keygraph design system (keygraph.inline.css, kg-theme.js, kg-diagram.js).
 */
(async function () {
  'use strict';
  const MAX_CANVAS_NODES = 40, AREA_FIRST_SHOW = 25, GROUP_COLLAPSE = 8, FLOOR = 0.6, WEAK_FLOOR = 0.4;
  const HUB_PENALTY = 0.7, FANOUT_PENALTY = 0.85, LOG_PENALTY = 0.8;
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const enc = encodeURIComponent;
  const KG = window.__kg = { maxNodesDrawn: 0, ready: false };

  /* ---------- data ---------- */
  let D;
  try {
    const raw = document.getElementById('kg-data').textContent.trim();
    if (raw.startsWith('GZ:')) {
      const bin = Uint8Array.from(atob(raw.slice(3)), c => c.charCodeAt(0));
      D = JSON.parse(await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).text());
    } else D = JSON.parse(raw);
    if (!D || !Array.isArray(D.t) || !Array.isArray(D.e)) throw new Error('the data has no tables or relations');
  } catch (err) {
    document.getElementById('app').innerHTML = `<div class="kg-loading"><div class="kg-error"><svg class="kg-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5l6 10.5H2z"/><path d="M8 6.5v3M8 11.2v.1"/></svg><div><b>This file’s embedded graph is damaged.</b><br><span class="kg-muted">Rebuild it with <code class="kg-mono">keygraph build</code>. Details: ${esc(err && err.message || err)}</span></div></div></div>`;
    KG.error = String(err); document.body.dataset.ready = 'error';
    return;
  }
  const S = D.s, T = D.t, E = D.e, C = Object.assign({ concepts: [], rels: [], cols: [], metrics: [], tn: [], p: {} }, D.c || {});
  C.p = C.p || {};
  const N = T.length;
  const tid = i => S[T[i][1]] + '.' + T[i][0];
  const tname = i => T[i][0], tschema = i => S[T[i][1]];
  const cname = (t, c) => T[t][2][c][0];
  const idx = new Map(); T.forEach((_, i) => idx.set(tid(i), i));
  const byBare = new Map(); T.forEach((t, i) => byBare.set(t[0], byBare.has(t[0]) ? -1 : i));
  function resolveTable(name) {
    if (name == null) return -1;
    if (idx.has(name)) return idx.get(name);
    const b = byBare.get(name); return b == null ? -1 : b;
  }
  const isHubT = t => !!(T[t][7] & 2), isLogT = t => !!(T[t][7] & 1), isMirrorT = t => !!(T[t][7] & 4);
  const MIRRORS = D.mi || {};
  const eConf = e => e[4], eDecl = e => e[5] === 0, eHub = e => !!(e[9] & 1), eHubOrRole = e => !!(e[9] & 3);
  const CARDS = ['many-to-one', 'one-to-one', 'many-to-many', 'one-to-many'];
  const CARD_TXT = ['many → one', 'one → one', 'many → many', 'one → many'];
  const flipCard = c => c === 0 ? 3 : c === 3 ? 0 : c;
  const STATUS = ['declared', 'inferred', 'confirmed', 'added', 'probed'];

  // adjacency in edge order (the same order Python's Graph.adjacency uses)
  const adj = T.map(() => []);
  E.forEach((e, ei) => { adj[e[0]].push({ ei, rev: false }); if (e[1] !== e[0]) adj[e[1]].push({ ei, rev: true }); });
  const deg = adj.map(a => a.length);
  const orient = (h, near) => { const e = E[h.ei]; return h.rev ? { ei: h.ei, near, far: e[0], nc: e[3], fc: e[2], card: flipCard(e[6]) } : { ei: h.ei, near, far: e[1], nc: e[2], fc: e[3], card: e[6] }; };
  let adjSorted = null; // neighbours by descending confidence, stable (Python's sorted(..., key=-confidence))
  const sortedAdj = t => { if (!adjSorted) adjSorted = new Array(N); return adjSorted[t] || (adjSorted[t] = adj[t].map((h, i) => [h, i]).sort((x, y) => E[y[0].ei][4] - E[x[0].ei][4] || x[1] - y[1]).map(x => x[0])); };

  // areas (fall back to one area per schema when the graph has none, e.g. an old graph.json)
  let AREAS = (D.a || []).map((a, i) => ({ id: a[0], name: a[1], tables: a[2], auto: !!a[3], unconnected: !!((a[4] || 0) & 1), mirror: !!((a[4] || 0) & 2), i }));
  if (!AREAS.length) {
    AREAS = S.map((s, si) => ({ id: 'schema_' + si, name: s, tables: [...T.keys()].filter(t => T[t][1] === si).sort((a, b) => deg[b] - deg[a] || tid(a).localeCompare(tid(b))), auto: true }))
      .filter(a => a.tables.length).sort((a, b) => b.tables.length - a.tables.length).map((a, i) => ({ ...a, i }));
  }
  const areaOf = new Int32Array(N).fill(-1);
  AREAS.forEach(a => a.tables.forEach(t => { areaOf[t] = a.i; }));
  const lost = [...T.keys()].filter(t => areaOf[t] < 0);
  if (lost.length) { const a = { id: 'unassigned', name: 'Unassigned', tables: lost, auto: true, i: AREAS.length }; AREAS.push(a); lost.forEach(t => { areaOf[t] = a.i; }); }
  const areaById = new Map(AREAS.map(a => [a.id, a]));
  const LINKS = (D.al || []).map(l => ({ a: l[0], b: l[1], count: l[2] }));
  const HUBS = [...T.keys()].filter(isHubT);

  /* context text: hide "not recorded yet" placeholders, then render inline markup safely (escape first, then a
     small whitelist: `code`, **bold**, _italic_). A " | " list separator becomes a line break or bullets. */
  const PLACEHOLDER = /^\s*[_*]*\s*(not recorded( yet)?|n\/?a|tbd|todo|-+)\s*\.?\s*[_*]*\s*$/i;
  const blank = v => typeof v === 'string' && PLACEHOLDER.test(v) ? '' : v;
  const cleanCtx = o => { if (o && typeof o === 'object') for (const k in o) { if (typeof o[k] === 'string') o[k] = blank(o[k]); } return o; };
  C.tn.forEach(cleanCtx); C.cols.forEach(cleanCtx); C.metrics.forEach(cleanCtx); C.concepts.forEach(cleanCtx);
  const md = (text, opts = {}) => {
    let h = esc(text == null ? '' : text);
    h = h.replace(/`([^`\n]+)`/g, '<code class="kg-mono">$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;:])/g, '$1<em>$2</em>');
    const parts = h.split(/\s+\|\s+/);
    if (parts.length > 1) return opts.list ? `<ul class="kg-md-list">${parts.map(x => `<li>${x}</li>`).join('')}</ul>` : parts.join(' · ');
    return h.replace(/\|/g, '&#124;');
  };
  const plain = text => String(text == null ? '' : text).replace(/`([^`]+)`/g, '$1').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\s+\|\s+/g, ' · ');
  // context lookups
  const tnote = {}, colsem = {}, tconcepts = T.map(() => []), tmetrics = T.map(() => []);
  C.tn.forEach(x => { tnote[x.t] = x; });
  C.cols.forEach(x => { colsem[x.t + ':' + x.c] = x; });
  C.concepts.forEach((c, ci) => c.l.forEach(l => tconcepts[l[0]].push([ci, l[2], l[1]])));
  C.metrics.forEach((m, mi) => m.t.forEach(t => tmetrics[t].push(mi)));
  const prof = (t, c) => C.p[t + ':' + c];
  const colIndex = (t, name) => T[t][2].findIndex(c => c[0] === name);

  /* ---------- state, theme, small helpers ---------- */
  const state = { weak: false, hubsAsEdges: false, mirrors: false, maxHops: 4, added: {}, showAll: {}, sel: [], expanded: new Set(), pathKey: '', pathRoute: 0, panelCol: null, searchType: 'all', settings: false, otherOpen: false };
  const floor = () => state.weak ? WEAK_FLOOR : FLOOR;
  const theme = () => document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  try { const th = localStorage.getItem('kg-theme'); if (th === 'dark' || th === 'light') document.documentElement.setAttribute('data-theme', th); } catch (_) { /* storage blocked */ }
  try { const q = new URLSearchParams(location.search).get('theme'); if (q === 'dark' || q === 'light') document.documentElement.setAttribute('data-theme', q); } catch (_) { /* ignore */ }
  const color = ai => KGTHEME.schemaColor(Math.max(0, ai), theme());
  const tcolor = t => color(areaOf[t]);
  const fmt = n => n == null ? '' : n >= 1e9 ? (n / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B' : n >= 1e6 ? (n / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K' : n.toLocaleString('en-US');
  const pct = x => x == null ? '' : (x * 100 >= 10 ? (x * 100).toFixed(0) : (x * 100).toFixed(1)).replace(/\.0$/, '') + '%';
  const hT = t => '#table/' + enc(tid(t)), hA = ai => '#area/' + enc(AREAS[ai].id), hC = (t, c) => '#col/' + enc(tid(t)) + '/' + enc(cname(t, c));
  const hP = (a, b) => '#path' + (a != null && a >= 0 ? '/' + enc(tid(a)) + (b != null && b >= 0 ? '/' + enc(tid(b)) : '') : '');

  const I = {
    search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
    map: '<rect x="1.5" y="2" width="5" height="4" rx="1"/><rect x="9.5" y="2" width="5" height="4" rx="1"/><rect x="5.5" y="10" width="5" height="4" rx="1"/><path d="M6.5 4h3M5 6l2 4M11 6l-2 4"/>',
    area: '<rect x="1.5" y="2" width="6" height="5" rx="1"/><rect x="8.5" y="9" width="6" height="5" rx="1"/><path d="M7.5 4.5h2a1.5 1.5 0 0 1 1.5 1.5v3"/>',
    path: '<circle cx="3" cy="8" r="1.75"/><circle cx="13" cy="8" r="1.75"/><path d="M4.75 8h2M9.25 8h2"/><circle cx="8" cy="8" r="1"/>',
    table: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M2 6h12M6 6v7.5"/>',
    sun: '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"/>',
    moon: '<path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z"/>',
    copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>',
    warn: '<path d="M8 2.5l6 10.5H2z"/><path d="M8 6.5v3M8 11.2v.1"/>',
    sliders: '<path d="M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6"/><circle cx="10" cy="4.5" r="1.5"/><circle cx="6" cy="11.5" r="1.5"/>',
    plus: '<path d="M8 3v10M3 8h10"/>', minus: '<path d="M3 8h10"/>', fit: '<path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"/>',
    swap: '<path d="M3 5h10l-3-3M13 11H3l3 3"/>', arrowR: '<path d="M3 8h10M9.5 4.5L13 8l-3.5 3.5"/>', arrowL: '<path d="M13 8H3M6.5 4.5L3 8l3.5 3.5"/>',
    empty: '<rect x="6" y="9" width="16" height="12" rx="2.5"/><rect x="26" y="27" width="16" height="12" rx="2.5"/><path d="M22 15h4a3 3 0 0 1 3 3v9" stroke-dasharray="2 3"/>',
    nopath: '<circle cx="8" cy="24" r="4"/><circle cx="40" cy="24" r="4"/><path d="M12 24h8M28 24h8" /><path d="M21 21l6 6M27 21l-6 6"/>'
  };
  const icon = (n, cls = '') => `<svg class="kg-icon ${cls}" viewBox="0 0 16 16" aria-hidden="true">${I[n]}</svg>`;
  const bigIcon = n => `<svg viewBox="0 0 48 48" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${I[n]}</svg>`;
  const band = c => KGTHEME.band(c);
  /* code index: "Used in code" (absent unless a repo was indexed) */
  const K = D.k || { r: [], t: {} };
  const Q = D.q || {};  /* query log: "Queries" (absent unless the build passed --queries) */
  const CODE_KINDS = ['declares table', 'declares column', 'writes', 'reads', 'uses'];
  const codeT = t => K.t[t] || null;
  const codeC = (t, c) => { const x = K.t[t]; return (x && x[2][c]) || null; };
  const codeTotal = x => (x ? x[0].reduce((a, b) => a + b, 0) : 0);
  function codeSection(x, t, ref, open) {
    if (!x) return '';
    const n = codeTotal(x), refs = x[1].slice(0, 20);
    const counts = x[0].map((v, i) => (v ? `<span class="kg-chip"><span>${esc(CODE_KINDS[i])}</span><span class="kg-faint">${v}</span></span>` : '')).join('');
    const rows = refs.map(r => `<div class="kg-coderef" data-coderef style="margin:0 0 8px"><code class="kg-mono" style="font-size:11.5px">${esc(K.r[r[0]])}:${esc(r[1])}:${r[2]}</code> <span class="kg-faint">${esc(CODE_KINDS[r[3]])}${r[5] >= 0 ? ' · ' + esc(cname(t, r[5])) : ''}</span> ${conf(r[4])}${r.length > 6 ? `<pre class="kg-code" data-snippet style="margin:4px 0 0">${esc(r[6])}</pre>` : ''}</div>`).join('');
    const more = n > refs.length ? `<p class="kg-faint" style="margin:6px 0 0;font:var(--kg-type-caption)">${n - refs.length} more: <code class="kg-mono">keygraph code where ${esc(ref)}</code></p>` : '';
    return `<details class="kg-section" data-code ${open ? 'open' : ''}><summary>Used in code<span class="kg-section-count">${n}</span></summary><div class="kg-section-body"><div class="kg-flags" style="flex-wrap:wrap;margin-bottom:10px">${counts}</div>${rows}${more}</div></details>`;
  }
  const fmtMs = v => (v == null ? '-' : Math.round(v).toLocaleString('en-US') + ' ms');
  function querySection(t, open) {
    const qs = Q[t];
    if (!qs || !qs.length) return '';
    const rows = qs.map(q => `<div class="kg-queryref" data-queryref style="margin:0 0 12px"><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:baseline"><code class="kg-mono" style="font-size:11.5px">${esc(q.fp)}</code><span class="kg-faint">${q.runs} run${q.runs === 1 ? '' : 's'} · median ${fmtMs(q.median_ms)} · p95 ${fmtMs(q.p95_ms)}</span></div>${q.context ? `<p style="margin:4px 0 0">${esc(q.context)}</p>` : ''}<pre class="kg-code" style="margin:4px 0 0;white-space:pre-wrap">${esc(q.sql)}</pre></div>`).join('');
    return `<details class="kg-section" data-queries ${open ? 'open' : ''}><summary>Queries<span class="kg-section-count">${qs.length}</span></summary><div class="kg-section-body">${rows}</div></details>`;
  }
  const conf = c => `<span class="kg-conf" data-band="${band(c)}" title="confidence ${c.toFixed(2)} (${band(c)})"><span class="kg-conf-meter"><i></i><i></i><i></i><i></i></span>${c.toFixed(2)}</span>`;
  const keyTag = k => `<span class="kg-key kg-key--${k.split(' ')[0].toLowerCase()}">${esc(k)}</span>`;
  const WARN_TXT = { hub: 'hub', fan_out: 'fan-out', log: 'log or scratch table' };
  const warn = w => `<span class="kg-warn">${icon('warn')}${esc(w)}</span>`;
  const glyph = (t, kind = 'table') => `<span class="kg-glyph kg-glyph--${kind}" style="--kg-glyph-color:${t >= 0 ? tcolor(t) : 'var(--kg-text-2)'}"></span>`;
  const nameHtml = t => `<span class="kg-entity-name"><span class="kg-entity-schema">${esc(tschema(t))}.</span>${esc(tname(t))}</span>`;
  const ent = t => `<a class="kg-entity" href="${hT(t)}" title="${esc(tid(t))}">${glyph(t)}${nameHtml(t)}</a>`;
  const colLink = (t, c, withTable = true) => `<a class="kg-entity" href="${hC(t, c)}"><span class="kg-glyph kg-glyph--column"></span><span class="kg-entity-name">${withTable ? `<span class="kg-entity-schema">${esc(tid(t))}.</span>` : ''}${esc(cname(t, c))}</span></a>`;
  const areaLink = ai => ai >= 0 ? `<a class="kg-link-text" href="${hA(ai)}">${esc(AREAS[ai].name)}</a>` : '';
  const colsTxt = (t, cs) => cs.map(c => cname(t, c)).join(', ');

  /* recent tables: the last 10, per tab */
  let recentMem = [];
  const recent = () => { try { const r = JSON.parse(sessionStorage.getItem('kg-recent') || '[]'); return Array.isArray(r) ? r : recentMem; } catch (_) { return recentMem; } };
  function pushRecent(t) {
    const r = [tid(t), ...recent().filter(x => x !== tid(t))].slice(0, 10); recentMem = r;
    try { sessionStorage.setItem('kg-recent', JSON.stringify(r)); } catch (_) { /* storage blocked: keep it in memory */ }
  }

  function toast(msg) {
    const t = document.createElement('div'); t.className = 'kg-toast'; t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  }
  async function copyText(text, what) {
    try { await navigator.clipboard.writeText(text); toast(what + ' copied'); }
    catch (_) { toast('The browser blocked the clipboard. Select the text and copy it.'); }
  }

  /* ---------- relation summaries (mirrors keygraph.summary.relation_summary) ---------- */
  function relSummary(t, minConf, colFilter) {
    const out = new Map(), inc = new Map(), hubs = new Map();
    for (const h of adj[t]) {
      const e = E[h.ei];
      if (e[4] < minConf) continue;
      if (colFilter && !colFilter(e)) continue;
      if (e[0] === t && eHubOrRole(e)) {
        const x = hubs.get(e[1]) || (hubs.set(e[1], { to: e[1], columns: [], count: 0 }), hubs.get(e[1]));
        e[2].forEach(c => { const n = cname(t, c); if (!x.columns.includes(n)) x.columns.push(n); }); x.count++; continue;
      }
      let bucket, far, col;
      if (e[0] === t) { bucket = out; far = e[1]; col = colsTxt(e[0], e[2]); }
      else if (e[1] === t) { bucket = inc; far = e[0]; col = colsTxt(e[0], e[2]); }
      else continue;
      const key = col + '|' + areaOf[far];
      const g = bucket.get(key) || (bucket.set(key, { column: col, area: areaOf[far], edges: [], far: new Map(), key }), bucket.get(key));
      g.far.set(far, Math.max(g.far.get(far) || 0, e[4])); g.edges.push(h.ei);
    }
    const finish = b => [...b.values()].map(g => {
      const tables = [...g.far.entries()].sort((x, y) => y[1] - x[1] || (tid(x[0]) < tid(y[0]) ? -1 : 1)).map(x => x[0]);
      return { column: g.column, area: g.area, edges: g.edges, tables, count: tables.length, collapsed: tables.length > GROUP_COLLAPSE, key: g.key };
    }).sort((a, b) => b.count - a.count || (a.column < b.column ? -1 : a.column > b.column ? 1 : 0));
    return { out: finish(out), in: finish(inc), hubs: [...hubs.values()].sort((a, b) => b.count - a.count || (tid(a.to) < tid(b.to) ? -1 : 1)) };
  }

  /* ---------- join paths (mirrors keygraph.query.find_paths: same penalties, order and tie-breaks) ---------- */
  function hopWarnings(h, end) {
    // passing through a hub costs once, on the step that arrives at it; an endpoint the reader chose never does
    const w = []; let f = 1.0;
    if (isHubT(h.far) && h.far !== end) { w.push('hub'); f *= HUB_PENALTY; }
    if (h.card === 3) { w.push('fan_out'); f *= FANOUT_PENALTY; }
    if (isLogT(h.far)) { w.push('log'); f *= LOG_PENALTY; }
    return [w, f];
  }
  function heapPush(hp, x) { hp.push(x); let i = hp.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (less(hp[p], hp[i])) break; [hp[p], hp[i]] = [hp[i], hp[p]]; i = p; } }
  function heapPop(hp) { const top = hp[0], last = hp.pop(); if (hp.length) { hp[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < hp.length && less(hp[l], hp[m])) m = l; if (r < hp.length && less(hp[r], hp[m])) m = r; if (m === i) break; [hp[m], hp[i]] = [hp[i], hp[m]]; i = m; } } return top; }
  function less(x, y) { return x[0] !== y[0] ? x[0] < y[0] : x[1] !== y[1] ? x[1] < y[1] : x[2] < y[2]; }
  const round3 = x => Number(x.toFixed(3));
  function aliases(ts) { const used = {}; return ts.map(t => { const base = tname(t).split(/[_\W]+/).filter(Boolean).map(w => w[0]).join('') || 't'; used[base] = (used[base] || 0) + 1; return used[base] === 1 ? base : base + used[base]; }); }
  function joinSql(path) {
    const ts = [path[0].near, ...path.map(h => h.far)], al = aliases(ts);
    const lines = [`FROM ${tid(ts[0])} AS ${al[0]}`];
    path.forEach((h, i) => lines.push(`JOIN ${tid(ts[i + 1])} AS ${al[i + 1]} ON ` + h.nc.map((c, j) => `${al[i]}.${cname(h.near, c)} = ${al[i + 1]}.${cname(h.far, h.fc[j])}`).join(' AND ')));
    return lines.join('\n');
  }
  function findPaths(a, b, maxHops = 4, minConf = 0.6, k = 3, maxPops = 300000, withMirrors = false) {
    if (a === b || a < 0 || b < 0) return [];
    const skipMirrors = !(withMirrors || isMirrorT(a) || isMirrorT(b));
    const hp = [[-1.0, 0, 0, a, []]]; let seq = 0, pops = 0; const found = [];
    while (hp.length && found.length < k && pops < maxPops) {
      const [negp, hops, , node, path] = heapPop(hp); pops++;
      if (node === b) { found.push([path, -negp]); continue; }
      if (hops >= maxHops) continue;
      const seen = new Set([a, ...path.map(h => h.far)]), perFar = new Map();
      for (const raw of sortedAdj(node)) {
        const h = orient(raw, node), c = E[h.ei][4];
        if (c < minConf || seen.has(h.far) || (skipMirrors && h.far !== b && isMirrorT(h.far))) continue;
        perFar.set(h.far, (perFar.get(h.far) || 0) + 1);
        if (perFar.get(h.far) > 2) continue;
        seq++;
        const [, f] = hopWarnings(h, b);
        heapPush(hp, [negp * c * f, hops + 1, seq, h.far, path.concat([h])]);
      }
    }
    const res = found.map(([path, p]) => {
      let cf = 1.0; path.forEach(h => { cf *= E[h.ei][4]; });
      return { path, raw: p, tables: [path[0].near, ...path.map(h => h.far)], confidence: round3(cf), score: round3(p), hops: path.length,
               steps: path.map(h => ({ ...h, warnings: hopWarnings(h, b)[0] })), sql: joinSql(path) };
    });
    // sort on the unrounded score, like query.find_paths
    return res.map((r, i) => [r, i]).sort((x, y) => y[0].raw - x[0].raw || x[0].hops - y[0].hops || x[1] - y[1]).map(x => x[0]);
  }

  /* ---------- search ---------- */
  const norm = s => String(s || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[٠-٩]/g, d => d.charCodeAt(0) - 0x660).replace(/[۰-۹]/g, d => d.charCodeAt(0) - 0x6F0)
    .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک').replace(/[ً-ٟـ]/g, '').replace(/‌/g, ' ').toLowerCase().replace(/[\s_./\\:;,|()[\]{}=<>"'`*+!?#-]+/g, ' ').trim();
  const KINDS = ['concept', 'metric', 'table', 'column', 'value'];
  const KIND_LABEL = { concept: 'Concepts', metric: 'Metrics', table: 'Tables', column: 'Columns', value: 'Values' };
  const BASE_W = { concept: 3.2, metric: 2.6, table: 3, column: 1.2, value: 0.9 };
  const REASON_W = { name: 2, synonym: 1.6, 'enum label': 1.2, context: 0.6, 'profiled value': 0.4, 'table name': 0 };
  const REASON_TXT = { 'table name': 'in table name' };
  const reasonTxt = r => REASON_TXT[r] || r;
  let SX = null;
  function buildIndex() {
    if (SX) return SX;
    const t0 = performance.now();
    const ents = [], post = new Map();
    const add = (k, t, c, label, fields, extra) => {
      const id = ents.length; const fs = fields.filter(f => f[1]).map(([r, txt]) => [r, norm(txt), plain(txt)]);
      ents.push({ k, t, c, label, fs, ...(extra || {}) });
      const toks = new Set(); fs.forEach(f => f[1].split(' ').forEach(x => x && toks.add(x)));
      for (const tok of toks) { let p = post.get(tok); if (!p) post.set(tok, p = []); p.push(id); }
    };
    T.forEach((tt, i) => { const n = tnote[i]; add('table', i, -1, tid(i), [['name', tid(i)], ['context', n ? [n.g, n.sm, n.p].filter(Boolean).join(' · ') : '']]); });
    C.concepts.forEach((c, i) => add('concept', -1, i, c.n, [['name', c.n], ['synonym', (c.sy || []).join(', ')], ['context', c.d]]));
    C.metrics.forEach((m, i) => add('metric', -1, i, m.n, [['name', m.n], ['context', [m.d, m.e].filter(Boolean).join(' · ')]]));
    T.forEach((tt, i) => tt[2].forEach((col, j) => {
      const cs = colsem[i + ':' + col[0]];
      add('column', i, j, tid(i) + '.' + col[0], [['name', col[0]], ['table name', tid(i)], ['context', cs ? [cs.m, cs.nm, cs.u].filter(Boolean).join(' · ') : (col[5] || '')]]);
      const codes = cs && cs.k ? Object.entries(cs.k) : [];
      codes.forEach(([code, label]) => add('value', i, j, `${tid(i)}.${col[0]} = ${code}`, [['enum label', String(label)], ['name', String(code)]], { code, why: `Code ${code} means “${label}”` }));
      (col[4] || []).slice(0, 60).forEach(m => { if (!codes.some(x => x[0] === m)) add('value', i, j, `${tid(i)}.${col[0]} = ${m}`, [['enum label', m]], { code: m, why: `Member of the ${col[1].split('(')[0]} type` }); });
      const p = prof(i, j);
      if (p && !p.pii && p.top_values && p.top_values.length && !codes.length && !(col[4] || []).length) {
        const vals = p.top_values.slice(0, 10).map(v => String(v[0])).filter(v => v.length <= 60 && /[^\d\s.:-]/.test(v));
        if (vals.length) add('value', i, j, `${tid(i)}.${col[0]}`, [['profiled value', vals.join(' | ')]], { vals: p.top_values.slice(0, 10), n: p.sample_size });
      }
    }));
    const keys = [...post.keys()].sort();
    SX = { ents, post, keys, ms: performance.now() - t0 };
    return SX;
  }
  function lb(keys, s) { let lo = 0, hi = keys.length; while (lo < hi) { const m = (lo + hi) >> 1; if (keys[m] < s) lo = m + 1; else hi = m; } return lo; }
  function search(q, opts = {}) {
    const X = buildIndex(), toks = norm(q).split(' ').filter(Boolean);
    if (!toks.length) return [];
    let best = null, bc = Infinity;
    for (const tk of toks) {
      const lists = []; let cnt = 0;
      for (let i = lb(X.keys, tk); i < X.keys.length && X.keys[i].startsWith(tk); i++) { const p = X.post.get(X.keys[i]); lists.push(p); cnt += p.length; if (cnt > 80000) break; }
      if (!lists.length) return [];
      if (cnt < bc) { bc = cnt; best = lists; }
    }
    const qn = toks.join(' '), seen = new Set(), res = [];
    const hasAll = txt => { const pad = ' ' + txt; return toks.every(t => pad.includes(' ' + t)); };
    for (const l of best) for (const id of l) {
      if (seen.has(id)) continue; seen.add(id);
      const e = X.ents[id];
      if (opts.tablesOnly && e.k !== 'table') continue;
      let reason = null, rtxt = '';
      for (const f of e.fs) if (hasAll(f[1])) { reason = f[0]; rtxt = f[2]; break; }
      if (!reason) { if (!hasAll(e.fs.map(f => f[1]).join(' '))) continue; reason = e.k === 'column' ? 'table name' : 'context'; rtxt = e.fs.map(f => f[2]).join(' · '); }
      let s = BASE_W[e.k] + REASON_W[reason];
      // match quality first: the whole query as the field (3), as a whole-word phrase in it (2), every token a
      // whole word (1), else only prefixes (0). Exact enum labels and exact names beat fragment matches.
      const fn = (e.fs.find(f => f[0] === reason) || e.fs[0])[1], pad = ' ' + fn + ' ';
      let quality = fn === qn ? 3 : pad.includes(' ' + qn + ' ') ? 2 : toks.every(t => pad.includes(' ' + t + ' ')) ? 1 : 0;
      // exact hits the joined fields hide: one profiled value equal to the query, or a table's bare name
      if (quality < 3 && ((reason === 'profiled value' && e.vals && e.vals.some(v => norm(v[0]) === qn)) || (e.k === 'table' && reason === 'name' && norm(tname(e.t)) === qn))) quality = 3;
      s += quality * 3;
      const ln = norm(e.k === 'table' ? tname(e.t) : e.k === 'column' ? cname(e.t, e.c) : e.label), full = norm(e.label);
      if (reason === 'name' || reason === 'synonym') { if (ln === qn || full === qn) s += 5; else if (ln.startsWith(qn) || full.startsWith(qn)) s += 2; }
      if (reason === 'table name') s -= 1.5;
      if (e.k === 'table') s += Math.min(1, deg[e.t] / 50);
      s -= (rtxt.length || 0) / 400;
      res.push({ e, s, q: quality, reason, rtxt });
    }
    // match quality is the first key (exact field > whole-word phrase > all whole tokens > prefixes), score the second:
    // an exact enum label or table name beats a concept whose name merely contains the query
    res.sort((a, b) => b.q - a.q || b.s - a.s || (a.e.label < b.e.label ? -1 : 1));
    const once = new Set(); // an enum member that is also a documented code is one result, not two
    return res.filter(r => { const k = r.e.k + '|' + r.e.label; if (once.has(k)) return false; once.add(k); return true; }).slice(0, opts.limit || 200);
  }
  function didYouMean(q) {
    const X = buildIndex(), w = norm(q).split(' ').filter(Boolean).pop(); if (!w || w.length < 3) return null;
    let best = null, bd = 3;
    const dist = (a, b) => { if (Math.abs(a.length - b.length) >= bd) return 9; const d = Array.from({ length: b.length + 1 }, (_, i) => i); for (let i = 1; i <= a.length; i++) { let prev = d[0]; d[0] = i; for (let j = 1; j <= b.length; j++) { const tmp = d[j]; d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1)); prev = tmp; } } return d[b.length]; };
    for (const k of X.keys) { if (k.length < 3) continue; const d = dist(w, k); if (d < bd) { bd = d; best = k; if (d === 1) break; } }
    return best;
  }
  const reEsc = t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  /* highlight only at the start of a word (a whole token or its prefix), never inside an unrelated word;
     the whole query first (so "in_review" lights up as one), then its tokens, skipping 1-letter tokens */
  const hl = (txt, q) => {
    const raw = String(txt == null ? '' : txt), qq = String(q || '').trim(); if (!qq) return esc(raw);
    const B = '(^|[^A-Za-z0-9\\u0600-\\u06FF])';
    let re = new RegExp(B + '(' + reEsc(qq) + ')', 'ig');
    if (!re.test(raw)) { const toks = norm(qq).split(' ').filter(t => t.length > 1 || norm(qq).length === 1); if (!toks.length) return esc(raw); re = new RegExp(B + '(' + toks.map(reEsc).join('|') + ')', 'ig'); }
    re.lastIndex = 0; let out = '', last = 0, m;
    while ((m = re.exec(raw))) { const at = m.index + m[1].length; out += esc(raw.slice(last, at)) + '<mark>' + esc(m[2]) + '</mark>'; last = at + m[2].length; if (!m[2].length) re.lastIndex++; }
    return out + esc(raw.slice(last));
  };
  function openHref(r) {
    const e = r.e;
    if (e.k === 'table') return hT(e.t);
    if (e.k === 'column' || e.k === 'value') return hC(e.t, e.c);
    if (e.k === 'concept') { const c = C.concepts[e.c]; const l = c.l.find(x => x[2] === 'primary-record') || c.l[0]; return l ? (l[1] && colIndex(l[0], l[1]) >= 0 ? hC(l[0], colIndex(l[0], l[1])) : hT(l[0])) : '#search/' + enc(c.n); }
    const m = C.metrics[e.c]; return m.t.length ? hT(m.t[0]) : '#search/' + enc(m.n);
  }
  function resultMeta(e) {
    if (e.k === 'concept') { const n = C.concepts[e.c].l.length; return n ? n + ' table' + (n > 1 ? 's' : '') : ''; }
    if (e.k === 'metric') { const m = C.metrics[e.c]; return m.t.length ? tid(m.t[0]) : ''; }
    return e.t >= 0 ? AREAS[areaOf[e.t]].name : '';
  }
  function resultWhy(r, q) {
    const e = r.e;
    if (e.k === 'value' && e.why) return hl(e.why, q);
    if (e.k === 'value' && e.vals) { const toks = norm(q).split(' '); const v = e.vals.find(x => toks.every(t => (' ' + norm(x[0])).includes(' ' + t))) || e.vals[0]; return `Top value “${hl(v[0], q)}”` + (e.n ? ` (${pct(v[1] / e.n)} of sampled rows)` : ''); }
    if (e.k === 'column') { const col = T[e.t][2][e.c]; const cs = colsem[e.t + ':' + col[0]]; return esc(col[1]) + (cs && cs.m ? ' · ' + hl(plain(cs.m), q) : ''); }
    if (e.k === 'table') { const n = tnote[e.t]; return r.reason === 'context' ? hl(r.rtxt, q) : (n && n.g ? esc(plain(n.g)) : `${T[e.t][2].length} columns · ${deg[e.t]} relations`); }
    if (e.k === 'concept') { const c = C.concepts[e.c]; return r.reason === 'synonym' ? 'Synonyms: ' + hl((c.sy || []).join(', '), q) : hl(plain(c.d || ''), q); }
    return hl(plain(C.metrics[e.c].d || ''), q);
  }
  const resultGlyph = e => e.k === 'table' ? glyph(e.t) : e.k === 'concept' ? '<span class="kg-glyph kg-glyph--concept"></span>' : e.k === 'metric' ? '<span class="kg-glyph kg-glyph--metric"></span>' : '<span class="kg-glyph kg-glyph--column"></span>';

  /* ---------- shell ---------- */
  function rail(active) {
    const rec = recent().map(resolveTable).filter(t => t >= 0);
    return `<aside class="kg-rail">
      <div class="kg-rail-brand"><a class="kg-rail-logo" href="#areas" aria-label="keygraph home">${window.KGLOGOS.logo}</a><a class="kg-rail-mark" href="#areas" aria-label="keygraph home">${window.KGLOGOS.mark}</a></div>
      <a class="kg-rail-search" href="#search" title="Search (/)" ${active === 'search' ? 'aria-current="page"' : ''}>${icon('search')}<span class="kg-nav-label">Search</span><span class="kg-kbd">/</span></a>
      <nav class="kg-nav">
        <a class="kg-nav-item" href="#areas" title="Areas" ${active === 'areas' || active === 'area' ? 'aria-current="page"' : ''}>${icon('map')}<span class="kg-nav-label">Areas</span><span class="kg-section-count" title="connected areas">${AREAS.filter(a => !a.unconnected && (state.mirrors || !a.mirror)).length}</span></a>
        <a class="kg-nav-item" href="#path" title="Path" ${active === 'path' ? 'aria-current="page"' : ''}>${icon('path')}<span class="kg-nav-label">Path</span></a>
      </nav>
      <div class="kg-rail-group kg-overline">Recent</div>
      <div class="kg-rail-list">${rec.length ? rec.map(t => `<a href="${hT(t)}" title="${esc(tid(t))}">${icon('table', 'kg-icon--sm')}${esc(tid(t))}</a>`).join('') : '<span class="kg-faint" style="font:var(--kg-type-caption);padding:0 8px">Tables you open show here.</span>'}</div>
      <div class="kg-rail-foot" style="position:relative">
        <button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm kg-rail-only-collapsed" data-act="settings" title="Relations: weak, hubs" aria-label="Relation settings" aria-expanded="${state.settings}">${icon('sliders')}</button>
        ${state.settings ? `<div class="kg-popover" style="position:absolute;left:52px;bottom:40px;width:230px;padding:10px;display:grid;gap:8px">${switches()}</div>` : ''}
        ${switches()}
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px">
          <span class="kg-rail-meta" title="${esc((D.st && D.st.built) || '')}">${N} tables · ${S.length} schema${S.length === 1 ? '' : 's'}</span>
          <button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-act="theme" title="Switch theme" aria-label="Switch theme">${icon(theme() === 'dark' ? 'sun' : 'moon')}</button>
        </div>
      </div></aside>`;
  }
  const switches = () => `<label class="kg-switch">Show weak relations<input type="checkbox" data-act="weak" ${state.weak ? 'checked' : ''}></label>
        <label class="kg-switch">Show hubs as edges<input type="checkbox" data-act="hubs" ${state.hubsAsEdges ? 'checked' : ''}></label>${Object.keys(MIRRORS).length ? `
        <label class="kg-switch" title="${esc(Object.entries(MIRRORS).map(([m, o]) => m + ' copies ' + o).join('; '))}">Show mirror schemas<input type="checkbox" data-act="mirrors" ${state.mirrors ? 'checked' : ''}></label>` : ''}`;
  let diagram = null;
  function page(view, head, body, panel) {
    if (diagram) { diagram.destroy(); diagram = null; }
    KG.maxNodesDrawn = 0;
    $('#app').innerHTML = `<div class="kg-app${panel ? '' : ' kg-app--panel-closed'}">${rail(view)}<section class="kg-view" data-view="${view}"><header class="kg-viewhead">${head}</header>${body}</section>${panel ? `<aside class="kg-panel" aria-label="Details">${panel}</aside>` : '<aside></aside>'}</div>`;
  }
  const setPanel = html => { const p = $('.kg-panel'); if (p) p.innerHTML = html; };
  const zoomCtl = (style = '') => `<div class="kg-overlay kg-overlay--br kg-zoom" style="${style}"><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="in" aria-label="Zoom in">${icon('plus')}</button><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="out" aria-label="Zoom out">${icon('minus')}</button><button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-z="fit" aria-label="Fit">${icon('fit')}</button></div>`;
  const legendSvg = (cls, dash) => `<svg viewBox="0 0 24 12"><path d="M1 6h22" fill="none" stroke="var(--kg-graph-edge-${cls})" stroke-width="1.6" ${dash ? `stroke-dasharray="${dash}"` : ''}/></svg>`;
  function bindZoom(d, fitOpts) { $$('[data-z]').forEach(b => { b.onclick = () => b.dataset.z === 'fit' ? d.fit(fitOpts) : d.zoomBy(b.dataset.z === 'in' ? 1.2 : 1 / 1.2); }); }
  const searchBox = (ph = 'Find a table, column or business term') => `<div class="kg-search">${icon('search')}<input class="kg-search-input" data-search placeholder="${esc(ph)}" aria-label="Search" autocomplete="off" spellcheck="false"><span class="kg-kbd">/</span></div>`;

  /* readable first paint: DESIGN.md's minimum zoom (map 0.7, ER 0.8). A view draws as many boxes as fit at that
     zoom in this viewport, never more than MAX_CANVAS_NODES; the rest are grouped behind "Other areas" or "show all". */
  const MAP_MIN_K = 0.7, ER_MIN_K = 0.8;
  function neededK(d, inset) {
    const r = d.container.getBoundingClientRect(), W = parseFloat(d.world.style.width) || 0, H = parseFloat(d.world.style.height) || 0;
    if (!W || !H) return 1;
    return Math.min(1, (r.width - inset.left - inset.right) / W, (r.height - inset.top - inset.bottom) / H);
  }
  /* the largest box count in 1..max whose drawing fits at minK. The drawn area grows about linearly with the count,
     so the first try (max) gives an estimate; scan down from a little above it (the layout direction can change
     with the count, so "fits" is not strictly monotonic). The final render is the chosen count. */
  function largestFit(max, tryN, minK) {
    const need = tryN(max, false);
    if (need >= minK) return max;
    const est = Math.max(1, Math.floor(max * Math.pow(need / minK, 2)));
    let best = 1;
    for (let n = Math.min(max - 1, est + 4); n >= 1; n--) { if (tryN(n, true) >= minK) { best = n; break; } }
    tryN(best, false);
    return best;
  }
  const showAreas = () => largestFit(MAX_CANVAS_NODES, (n, probe) => renderAreas(n, probe), MAP_MIN_K);
  const showArea = id => state.showAll[id] || !areaById.get(id) ? renderArea(id, MAX_CANVAS_NODES, false)
    : largestFit(MAX_CANVAS_NODES, (n, probe) => renderArea(id, n, probe), ER_MIN_K);
  /* ---------- 1. Area map ---------- */
  // "Orders (shop: orders)" -> main "Orders", muted "shop · orders"; "Unconnected (shop)" -> "Unconnected", "shop"
  const splitName = name => { const m = /^(.*?) \((.+)\)$/.exec(name || ''); return m ? { main: m[1], sub: m[2].replace(/: /g, ' · ') } : { main: name, sub: '' }; };
  const midTrunc = (x, n) => { x = String(x); if (x.length <= n) return x; const h = Math.ceil((n - 1) / 2); return x.slice(0, h) + '…' + x.slice(x.length - (n - 1 - h)); };
  // name first, schema muted after it; a long name is cut in the middle so its distinctive end stays visible
  const entNF = (t, n = 30) => `<a class="kg-entity kg-entity--nf" href="${hT(t)}" title="${esc(tid(t))}">${glyph(t)}<span class="kg-entity-name">${esc(midTrunc(tname(t), n))}</span><span class="kg-nf-schema">${esc(tschema(t))}</span></a>`;
  const areaRow = a => { const nm = splitName(a.name); return `<a class="kg-level" href="${hA(a.i)}" title="${esc(a.name)}" style="text-decoration:none;color:inherit"><span class="kg-swatch" style="--kg-glyph-color:${color(a.i)};margin-left:6px"></span><span class="kg-level-main">${esc(nm.main)} <span class="kg-faint kg-mono" style="font-size:11.5px">${esc(nm.sub || tname(a.tables[0]))}</span></span><span class="kg-conf">${a.tables.length}</span></a>`; };
  const visibleAreas = () => AREAS.filter(a => state.mirrors || !a.mirror);
  function mapLinks(mapId, ids) {
    const lk = new Map();
    LINKS.forEach(l => { const a = mapId(l.a), b = mapId(l.b); if (!a || !b || a === b) return; const k = a < b ? a + '|' + b : b + '|' + a; lk.set(k, (lk.get(k) || 0) + l.count); });
    const all = [...lk.entries()].map(([k, count]) => { const [a, b] = k.split('|'); return { a, b, count }; });
    // draw only the strong links (count >= max(3, 5% of the largest)) plus each box's top 3, so the map stays legible
    const maxC = Math.max(1, ...all.map(l => l.count)), thr = Math.max(3, 0.05 * maxC), keep = new Set();
    ids.forEach(id => all.filter(l => l.a === id || l.b === id).sort((x, y) => y.count - x.count).slice(0, 3).forEach(l => keep.add(l)));
    return { all, shown: all.filter(l => l.count >= thr || keep.has(l)) };
  }
  function renderAreas(limit = MAX_CANVAS_NODES, probe = false) {
    const st = D.st || {}, kinds = st.edges_by_kind || {};
    const sorted = visibleAreas().sort((a, b) => b.tables.length - a.tables.length || a.i - b.i);
    const conn = sorted.filter(a => !a.unconnected), loose = sorted.filter(a => a.unconnected);
    // unconnected areas never spend the box budget: they fold into one "Not connected" box
    const budget = Math.min(MAX_CANVAS_NODES, limit) - (loose.length ? 1 : 0);
    const shown = conn.length > budget ? conn.slice(0, Math.max(1, budget - 1)) : conn, hidden = conn.length > budget ? conn.slice(Math.max(1, budget - 1)) : [];
    const shownSet = new Set(shown.map(a => a.i)), hiddenSet = new Set(hidden.map(a => a.i));
    const mapId = ai => shownSet.has(ai) ? 'a' + ai : hiddenSet.has(ai) ? 'other' : null;
    const nodes = shown.map(a => { const nm = splitName(a.name); return { id: 'a' + a.i, name: nm.main, sub: nm.sub, count: a.tables.length, top: a.tables.slice(0, 3).map(tname), color: color(a.i) }; });
    if (hidden.length) nodes.push({ id: 'other', name: 'Other areas', sub: `${hidden.length} smaller areas`, other: true, count: hidden.reduce((x, a) => x + a.tables.length, 0), color: 'var(--kg-text-3)', top: [...hidden.slice(0, 2).map(a => splitName(a.name).main), `+ ${Math.max(0, hidden.length - 2)} more`] });
    const looseT = loose.reduce((x, a) => x + a.tables.length, 0);
    if (loose.length) nodes.push({ id: 'loose', name: `Not connected: ${loose.length} area${loose.length > 1 ? 's' : ''}`, sub: 'no relations once hubs are set aside', other: true, count: looseT, color: 'var(--kg-text-3)', top: [...loose.slice(0, 2).map(a => splitName(a.name).sub || a.name), loose.length > 2 ? `+ ${loose.length - 2} more` : ''].filter(Boolean) });
    const links = mapLinks(mapId, nodes.map(n => n.id));
    const polyT = T.filter(t => t[5].length).length;
    const weakHidden = E.filter(e => e[4] < floor()).length;
    const hubRows = HUBS.filter(h => state.mirrors || !isMirrorT(h)).sort((a, b) => deg[b] - deg[a]).slice(0, 8).map(h => {
      const cols = new Map(); let n = 0; adj[h].forEach(x => { const e = E[x.ei]; if (e[1] === h && e[0] !== h) { n++; const c = colsTxt(e[0], e[2]); cols.set(c, (cols.get(c) || 0) + 1); } });
      const top = [...cols.entries()].sort((a, b) => b[1] - a[1])[0];
      return `<div class="kg-level" style="grid-template-columns:minmax(0,1fr) auto;padding:4px 20px">${entNF(h, 26)}<span class="kg-faint" style="font:var(--kg-type-caption)" title="${top ? 'mostly ' + esc(top[0]) : ''}">← ${n}${top ? ' · ' + esc(midTrunc(top[0], 18)) : ''}</span></div>`;
    }).join('');
    const nMirror = Object.keys(MIRRORS).length;
    page('areas',
      `<h1>Areas</h1><span class="kg-faint" style="font:var(--kg-type-small)" title="${links.all.length - links.shown.length} weaker area links are not drawn">${conn.length} connected areas from ${fmt(N)} tables${loose.length ? ` · ${fmt(looseT)} tables not connected` : ''} · lines show relations that cross areas${(kinds.declared || 0) === 0 ? ' (all inferred)' : ''}</span><span class="kg-spacer"></span>${searchBox()}`,
      `<div class="kg-canvas" id="cv">${zoomCtl()}</div>`,
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Database</span></div><h2 class="kg-panel-title">${esc(S.length <= 3 ? S.join(', ') : S.length + ' schemas')}</h2>
        <div class="kg-panel-meta"><span><b>${fmt(N)}</b> tables</span><span><b>${fmt(st.columns || T.reduce((x, t) => x + t[2].length, 0))}</b> columns</span><span><b>${fmt(E.length)}</b> relations</span></div></div>
       <div class="kg-panel-body">
        <details class="kg-section"><summary>Relations<span class="kg-section-count">${fmt(kinds.declared || 0)} declared · ${fmt(kinds.inferred || 0)} inferred</span></summary><div class="kg-section-body">
          <div class="kg-stats"><div class="kg-stat"><b>${fmt(kinds.declared || 0)}</b><span>declared</span></div><div class="kg-stat"><b>${fmt(kinds.inferred || 0)}</b><span>inferred</span></div><div class="kg-stat"><b>${fmt(polyT)}</b><span>polymorphic tables</span></div></div>
          <p class="kg-faint" style="margin:0;font:var(--kg-type-small)">${state.weak ? `Showing relations down to ${WEAK_FLOOR.toFixed(2)} confidence.` : `Inferred relations below ${FLOOR.toFixed(2)} confidence are hidden${weakHidden ? ` (${fmt(weakHidden)})` : ''}. Turn on “Show weak relations” to see them.`} The map draws the ${links.shown.length} strongest of ${links.all.length} links between areas.</p></div></details>
        <details class="kg-section" open><summary>Largest connected areas</summary><div class="kg-section-body--flush">${conn.slice(0, 6).map(areaRow).join('') || '<p class="kg-muted" style="margin:8px 20px;font:var(--kg-type-small)">None.</p>'}</div></details>
        <details class="kg-section" open><summary>Hubs<span class="kg-section-count">${HUBS.length}</span></summary><div class="kg-section-body${HUBS.length ? '--flush' : ''}">
          ${HUBS.length ? `<p class="kg-muted" style="margin:12px 20px 6px;font:var(--kg-type-small)">Tables that almost everything points to. They are shown as labels on boxes, not as lines, so they don’t tangle every view.</p>${hubRows}` : '<p class="kg-muted" style="margin:0;font:var(--kg-type-small)">No table is pointed to by most of the database.</p>'}</div></details>
        ${hidden.length ? `<details class="kg-section" ${state.otherOpen === 'other' ? 'open' : ''} id="otherlist"><summary>Other areas<span class="kg-section-count">${hidden.length}</span></summary><div class="kg-section-body--flush kg-other-list">${hidden.map(areaRow).join('')}</div></details>` : ''}
        ${loose.length ? `<details class="kg-section" ${state.otherOpen === 'loose' ? 'open' : ''} id="looselist"><summary>Not connected<span class="kg-section-count">${loose.length} areas · ${fmt(looseT)} tables</span></summary><div class="kg-section-body--flush kg-other-list"><p class="kg-muted" style="margin:10px 20px 4px;font:var(--kg-type-small)">These tables have no inferred or declared relation once hub columns are set aside. They are often log, archive or lookup tables.</p>${loose.map(areaRow).join('')}</div></details>` : ''}
        ${nMirror ? `<details class="kg-section"><summary>Mirror schemas<span class="kg-section-count">${nMirror}</span></summary><div class="kg-section-body"><p class="kg-muted" style="margin:0 0 8px;font:var(--kg-type-small)">Schemas whose tables copy another schema. They are left out of areas, hubs and paths${state.mirrors ? '; “Show mirror schemas” is on' : ' unless “Show mirror schemas” is on'}.</p>${Object.entries(MIRRORS).map(([m, o]) => `<div class="kg-mono" style="font-size:12px">${esc(m)} <span class="kg-faint">copies</span> ${esc(o)}</div>`).join('')}</div></details>` : ''}
        <details class="kg-section"><summary>Curate areas</summary><div class="kg-section-body"><p class="kg-muted" style="margin:0 0 10px;font:var(--kg-type-small)">This file can’t write to disk. Rename, pin or merge areas in a context file; the next <code class="kg-mono">keygraph build</code> applies it.</p>
          <pre class="kg-code">areas:\n  - name: Orders\n    match: [${esc(AREAS[0] ? AREAS[0].id : 'a_1234abcd')}]\n    pin: [schema.table]\n  - merge: [Vouchers, Voucher codes]</pre></div></details>
       </div>`);
    const d = diagram = KGDiagram.mount($('#cv')).map({ areas: nodes, links: links.shown });
    $$('#cv .kg-area').forEach(n => { n.dataset.area = n.dataset.id; n.setAttribute('role', 'link'); n.setAttribute('aria-label', n.title || n.querySelector('.kg-area-name').textContent); if (n.dataset.id === 'loose') n.dataset.notConnected = '1'; });
    KG.maxNodesDrawn = nodes.length;
    KG.map = { boxes: nodes.length, connectedShown: shown.length, otherAreas: hidden.length, notConnected: loose.length, links: links.shown.length, allLinks: links.all.length };
    const fitOpts = { pad: 32, minK: MAP_MIN_K };
    const need = neededK(d, { top: 32, right: 32, bottom: 32, left: 32 });
    if (probe) return need;
    d.fit(fitOpts); bindZoom(d, fitOpts);
    KG.zoom = +d.container.dataset.zoom;
    const open = n => {
      if (n.other) { state.otherOpen = n.id; const sct = $(n.id === 'loose' ? '#looselist' : '#otherlist'); if (sct) { sct.open = true; sct.scrollIntoView({ block: 'start' }); } }
      else location.hash = hA(+n.id.slice(1));
    };
    d.onSelect = open;
    $$('#cv .kg-area').forEach(el => el.addEventListener('keydown', ev => { if (ev.key === 'Enter') el.click(); }));
    bindSearchBox();
    return need;
  }

  /* ---------- 2. Area (ER) ---------- */
  function cardRows(t, drawnEdges) {
    const pk = new Set(T[t][3]), fk = new Set(), uk = new Set(), rows = [];
    T[t][4].forEach(ix => { if (ix[1] && !ix[2]) ix[3].forEach(c => uk.add(c)); });
    adj[t].forEach(h => { const e = E[h.ei]; if (e[0] === t && e[4] >= floor() && (!eHubOrRole(e) || state.hubsAsEdges)) e[2].forEach(c => fk.add(c)); });
    const want = new Set([...pk, ...fk]);
    drawnEdges.forEach(e => { if (e[1] === t) e[3].forEach(c => want.add(c)); if (e[0] === t) e[2].forEach(c => want.add(c)); });
    T[t][2].forEach((col, c) => { if (want.has(c)) rows.push({ name: col[0], type: col[1], key: pk.has(c) ? 'PK' : fk.has(c) ? 'FK' : uk.has(c) ? 'UK' : '' }); });
    const order = { PK: 0, FK: 1, UK: 2, '': 3 };
    rows.sort((a, b) => order[a.key] - order[b.key]);
    const cap = 6, shown = rows.slice(0, cap); // short cards: more of them fit at a readable zoom
    return { rows: shown, more: T[t][2].length - shown.length };
  }
  function renderArea(id, limit = MAX_CANVAS_NODES, probe = false) {
    const area = areaById.get(id);
    if (!area) return notFound('area', id);
    const all = area.tables, n = all.length, showAll = !!state.showAll[id];
    const base = all.slice(0, showAll ? MAX_CANVAS_NODES : Math.min(AREA_FIRST_SHOW, MAX_CANVAS_NODES, limit));
    const drawn = base.slice(), inDrawn = new Set(drawn);
    const added = (state.added[id] || []).filter(t => !inDrawn.has(t));
    added.forEach(t => { if (drawn.length < MAX_CANVAS_NODES) { drawn.push(t); inDrawn.add(t); } });
    if (state.hubsAsEdges) {
      const hubT = new Map();
      drawn.forEach(t => adj[t].forEach(h => { const e = E[h.ei]; if (e[0] === t && eHubOrRole(e) && e[4] >= floor() && !inDrawn.has(e[1])) hubT.set(e[1], (hubT.get(e[1]) || 0) + 1); }));
      [...hubT.entries()].sort((a, b) => b[1] - a[1]).forEach(([t]) => { if (drawn.length < MAX_CANVAS_NODES) { drawn.push(t); inDrawn.add(t); } });
    }
    const edges = [];
    E.forEach((e, ei) => { if (inDrawn.has(e[0]) && inDrawn.has(e[1]) && e[0] !== e[1] && e[4] >= floor() && (!eHubOrRole(e) || state.hubsAsEdges)) edges.push(ei); });
    const inside = E.filter(e => areaOf[e[0]] === area.i && areaOf[e[1]] === area.i && e[0] !== e[1] && e[4] >= floor() && !eHubOrRole(e)).length;
    // neighbouring tables: other areas' tables that connect in, by relation count
    const nb = new Map(), areaSet = new Set(all);
    all.forEach(t => adj[t].forEach(h => { const e = E[h.ei], far = h.rev ? e[0] : e[1]; if (!areaSet.has(far) && e[4] >= floor() && !eHubOrRole(e) && (state.mirrors || !isMirrorT(far))) nb.set(far, (nb.get(far) || 0) + 1); }));
    const nbs = [...nb.entries()].sort((a, b) => b[1] - a[1] || (tid(a[0]) < tid(b[0]) ? -1 : 1));
    const tables = drawn.map(t => {
      const de = edges.map(ei => E[ei]).filter(e => e[0] === t || e[1] === t);
      const { rows, more } = cardRows(t, de);
      const hubs = state.hubsAsEdges ? [] : [...new Set(adj[t].filter(h => { const e = E[h.ei]; return e[0] === t && eHubOrRole(e) && e[4] >= floor(); }).map(h => tname(E[h.ei][1])))];
      return { id: tid(t), t, schema: tschema(t), name: tname(t), color: tcolor(t), rows, more, hubs, neighbour: areaOf[t] !== area.i, area: AREAS[areaOf[t]].name, selected: state.sel.includes(t) };
    });
    const polyCols = t => new Set(T[t][5].map(p => p[0]));
    // one line per pair of cards (the most confident join); the tooltip counts the others
    const pairs = new Map();
    edges.forEach(ei => { const e = E[ei], k = e[0] + '|' + e[1]; const cur = pairs.get(k); if (!cur) pairs.set(k, { ei, n: 1 }); else { cur.n++; if (e[4] > E[cur.ei][4]) cur.ei = ei; } });
    const dEdges = [...pairs.values()].map(({ ei, n }) => { const e = E[ei]; const poly = e[2].some(c => polyCols(e[0]).has(c)); return { from: { t: tid(e[0]), col: cname(e[0], e[2][0] ?? 0) }, to: { t: tid(e[1]), col: cname(e[1], e[3][0] ?? 0) }, kind: poly ? 'polymorphic' : eDecl(e) ? 'declared' : 'inferred', conf: e[4], reason: (n > 1 ? `${n} joins, best ${e[4].toFixed(2)} · ` : '') + (e[8][0] || '') }; });
    const capNote = n > base.length && !showAll
      ? `<div class="kg-callout kg-callout-bar">${icon('table')}<div><p><b>Showing the ${base.length} most connected of ${n} tables</b>, as many as fit at a readable size. ${n <= MAX_CANVAS_NODES ? '' : `At most ${MAX_CANVAS_NODES} are drawn; the panel lists all ${n}.`} <button class="kg-btn kg-btn--sm" data-act="show-all" style="margin-left:6px">${n <= MAX_CANVAS_NODES ? 'Show all ' + n : `Draw ${MAX_CANVAS_NODES}`}</button></p></div></div>`
      : showAll && n > MAX_CANVAS_NODES ? `<div class="kg-callout kg-callout-bar">${icon('table')}<div><p><b>Drawing the ${base.length} most connected of ${n} tables.</b> The panel lists all of them. <button class="kg-btn kg-btn--sm kg-btn--ghost" data-act="show-all" style="margin-left:6px">Show ${AREA_FIRST_SHOW}</button></p></div></div>` : '';
    const selOk = state.sel.length === 2;
    page('area',
      `<nav class="kg-crumbs"><a href="#areas">Areas</a><span class="sep">/</span><span aria-current="page" style="font:var(--kg-type-title)">${esc(area.name)}</span></nav>
       <span class="kg-faint" style="font:var(--kg-type-small)">${n} tables · ${inside} joins inside · ${nbs.length} neighbouring tables</span><span class="kg-spacer"></span>
       <span class="kg-sel-hint">${state.sel.length === 1 ? 'Shift-click a second table for a path' : ''}</span>
       <a class="kg-btn kg-btn--sm" ${selOk ? `href="${hP(state.sel[0], state.sel[1])}"` : 'aria-disabled="true" style="opacity:.5;pointer-events:none"'}>${icon('path', 'kg-icon--sm')}Path between selected</a>
       <button class="kg-btn kg-btn--sm kg-btn--ghost" data-act="copy-mermaid">${icon('copy', 'kg-icon--sm')}Mermaid</button>`,
      `<div class="kg-canvas" id="cv">${capNote}
        <div class="kg-overlay kg-legend kg-legend--inline" style="left:12px;bottom:66px"><div class="kg-legend-row">${legendSvg('declared')}declared</div><div class="kg-legend-row">${legendSvg('inferred', '5 4')}inferred, fainter when less sure</div><div class="kg-legend-row"><span class="kg-hub">→ hub</span>${state.hubsAsEdges ? 'hubs drawn as edges' : 'hub shown as a label'}</div></div>
        <div class="kg-strip"><span class="kg-strip-label">Neighbouring tables</span><div class="kg-strip-items">${nbs.length ? nbs.slice(0, 30).map(([t, c]) => { const on = inDrawn.has(t); return `<button class="kg-nchip" data-act="add-nb" data-t="${t}" ${on ? 'aria-pressed="true"' : ''} title="${esc(tid(t))} in ${esc(AREAS[areaOf[t]].name)}: ${c} relation${c > 1 ? 's' : ''}${on ? ' (click to remove)' : ' (click to add)'}"><span class="kg-swatch" style="--kg-glyph-color:${tcolor(t)}"></span>${esc(tname(t))}<small>${c}</small>${on ? '' : '<span class="plus">+</span>'}</button>`; }).join('') : '<span class="kg-faint" style="font:var(--kg-type-small)">None above the confidence floor.</span>'}</div></div>
        ${zoomCtl('bottom:66px')}</div>`,
      state.sel.length ? panelTable(state.sel[state.sel.length - 1], area) : panelArea(area, showAll));
    const d = diagram = KGDiagram.mount($('#cv'));
    d.er({ tables, edges: dEdges }, { ranksep: 60, nodesep: 22, rankdir: 'auto' }); // LR or TB, whichever fits the canvas better
    KG.maxNodesDrawn = tables.length;
    const svg = $('#cv .kg-edges');
    svg.querySelectorAll('.kg-edge').forEach((g, i) => {
      const de = dEdges[+g.dataset.i]; if (de) { const t = document.createElementNS('http://www.w3.org/2000/svg', 'title'); t.textContent = `${de.from.t}.${de.from.col} → ${de.to.t}.${de.to.col} · ${de.kind} ${de.conf.toFixed(2)}${de.reason ? ' · ' + de.reason : ''}`; g.prepend(t); }
      g.addEventListener('mouseenter', () => svg.classList.add('has-hover')); g.addEventListener('mouseleave', () => svg.classList.remove('has-hover'));
    });
    // first paint fits the drawing inside the room the overlays leave (callout on top, legend and strip below)
    const inset = { top: capNote ? 84 : 32, bottom: 118, left: 28, right: 60 };
    // first paint fits at >= ER_MIN_K by drawing fewer cards; "show all" may zoom lower and pan
    const need = neededK(d, inset);
    if (probe) return need;
    const fitOpts = { minK: showAll ? 0.5 : ER_MIN_K, anchor: 'start', inset };
    d.fit(fitOpts); bindZoom(d, fitOpts);
    KG.zoom = +d.container.dataset.zoom;
    d.onSelect = (node, ev) => {
      const t = node.t;
      if (ev && (ev.shiftKey || ev.metaKey || ev.ctrlKey) && state.sel.length && state.sel[0] !== t) { state.sel = [state.sel[state.sel.length - 1], t]; location.hash = hP(state.sel[0], state.sel[1]); return; }
      state.sel = state.sel.length === 1 && state.sel[0] === t ? [] : [t];
      $$('#cv .kg-tcard').forEach(c => c.toggleAttribute('aria-selected', state.sel.includes(resolveTable(c.dataset.id))));
      setPanel(state.sel.length ? panelTable(t, area) : panelArea(area, showAll));
      $('.kg-sel-hint').textContent = state.sel.length === 1 ? 'Shift-click a second table for a path' : '';
    };
    $$('#cv .kg-tcard').forEach(c => {
      c.querySelectorAll('[aria-selected]').forEach(() => {});
      if (c.getAttribute('aria-selected') !== 'true') c.removeAttribute('aria-selected');
      c.addEventListener('dblclick', () => { location.hash = hT(resolveTable(c.dataset.id)); });
      c.addEventListener('keydown', ev => { if (ev.key === 'Enter') location.hash = hT(resolveTable(c.dataset.id)); });
      c.title = 'Click to select, double-click to open, shift-click a second table for a path';
    });
    KG.mermaid = () => mermaid(drawn, edges);
    return showAll ? Infinity : need; // "show all" draws up to 40 whatever the zoom
  }
  function mermaid(drawn, edges) {
    const nm = t => tid(t).replace(/[^A-Za-z0-9_]/g, '_');
    const out = ['erDiagram'];
    drawn.forEach(t => { const { rows } = cardRows(t, []); out.push(`  ${nm(t)} {`); rows.forEach(r => out.push(`    ${(r.type.split(/[\s(]/)[0] || 'col').replace(/[^A-Za-z0-9_]/g, '')} ${r.name.replace(/[^A-Za-z0-9_]/g, '_')}${r.key ? ' ' + r.key : ''}`)); out.push('  }'); });
    edges.forEach(ei => { const e = E[ei]; out.push(`  ${nm(e[1])} ||--o{ ${nm(e[0])} : "${colsTxt(e[0], e[2])}"`); });
    return out.join('\n');
  }
  function panelArea(area, showAll) {
    const nbAreas = LINKS.filter(l => l.a === area.i || l.b === area.i).map(l => [l.a === area.i ? l.b : l.a, l.count]).sort((a, b) => b[1] - a[1]);
    const hubs = new Map();
    area.tables.forEach(t => adj[t].forEach(h => { const e = E[h.ei]; if (e[0] === t && eHubOrRole(e) && e[4] >= floor()) { const x = hubs.get(e[1]) || (hubs.set(e[1], { n: 0, cols: new Set() }), hubs.get(e[1])); x.n++; e[2].forEach(c => x.cols.add(cname(t, c))); } }));
    const n = area.tables.length, list = showAll || n <= 60 ? area.tables : area.tables.slice(0, 60);
    return `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-swatch" style="--kg-glyph-color:${color(area.i)}"></span><span class="kg-overline">Area${area.auto ? '' : ' · curated'}</span></div><h2 class="kg-panel-title" style="font-family:var(--kg-font-sans)">${esc(area.name)}</h2>
      <div class="kg-panel-meta"><span><b>${n}</b> tables</span><span class="kg-mono" style="font-size:11.5px">${esc(area.id)}</span></div>
      <p class="kg-faint" style="margin:10px 0 0;font:var(--kg-type-small)">Click a box to see it here. Shift-click a second box to find a path.</p></div>
     <div class="kg-panel-body">
      <details class="kg-section" open><summary>Tables<span class="kg-section-count">${n}</span></summary><div class="kg-section-body--flush">${list.map(t => `<div class="kg-level" style="grid-template-columns:minmax(0,1fr) auto;padding:4px 20px">${entNF(t, 30)}<span class="kg-faint" style="font:var(--kg-type-caption)">${deg[t]} rel.${codeT(t) ? ` · <span data-incode>${codeTotal(codeT(t))} in code</span>` : ''}</span></div>`).join('')}${list.length < n ? `<div class="kg-relcollapsed" data-group-collapsed style="padding:0 20px">${n - list.length} more tables<a data-act="show-all">Show all</a></div>` : ''}</div></details>
      <details class="kg-section" open><summary>Neighbouring areas<span class="kg-section-count">${nbAreas.length}</span></summary><div class="kg-section-body--flush">${nbAreas.map(([ai, c]) => `<a class="kg-level" href="${hA(ai)}" style="text-decoration:none;color:inherit"><span class="kg-swatch" style="--kg-glyph-color:${color(ai)};margin-left:6px"></span><span>${esc(AREAS[ai].name)}</span><span class="kg-conf">${c}</span></a>`).join('') || '<p class="kg-muted" style="margin:8px 20px;font:var(--kg-type-small)">None.</p>'}</div></details>
      <details class="kg-section" open><summary>Hub references<span class="kg-section-count">${hubs.size}</span></summary><div class="kg-section-body">${[...hubs.entries()].sort((a, b) => b[1].n - a[1].n).map(([t, x]) => `<div class="kg-hubline" style="margin:0 0 6px">${entNF(t, 26)}<span class="kg-faint">via ${esc([...x.cols].slice(0, 4).join(', '))} (${x.n})</span></div>`).join('') || '<p class="kg-muted" style="margin:0;font:var(--kg-type-small)">None.</p>'}</div></details>
     </div>`;
  }

  /* right panel: one table, compact */
  function panelTable(t, area) {
    const n = tnote[t], pk = T[t][3];
    const sameArea = adj[t].map(h => ({ h, e: E[h.ei] })).filter(({ h, e }) => { const far = h.rev ? e[0] : e[1]; return far !== t && areaOf[far] === areaOf[t] && e[4] >= floor() && !eHubOrRole(e); });
    const keys = keyRows(t).slice(0, 5);
    return `<div class="kg-panel-head"><div class="kg-panel-kicker">${glyph(t)}<span class="kg-overline">Table · ${esc(AREAS[areaOf[t]].name)}</span>${n ? `<span class="kg-badge kg-badge--${n.v ? 'verified' : 'unverified'}">${n.v ? 'verified' : 'unverified'}</span>` : ''}</div>
      <h2 class="kg-panel-title" title="${esc(tid(t))}"><span class="kg-entity-schema">${esc(midTrunc(tschema(t), 18))}.</span>${esc(tname(t))}</h2>
      <div class="kg-panel-meta">${T[t][8] != null ? `<span><b>${fmt(T[t][8])}</b> rows</span>` : ''}<span><b>${T[t][2].length}</b> columns</span><span><b>${relCount(t)}</b> relations</span></div>
      <div class="kg-panel-actions"><a class="kg-btn kg-btn--sm" href="${hT(t)}">${icon('table', 'kg-icon--sm')}Open table</a><a class="kg-btn kg-btn--sm" href="${hP(t)}">${icon('path', 'kg-icon--sm')}Path from here</a></div></div>
     <div class="kg-panel-body">
      ${n && (n.g || n.p) ? `<details class="kg-section" open><summary>What one row is</summary><div class="kg-section-body">${n.g ? `<p style="margin:0">${md(n.g)}</p>` : ''}${n.p ? `<div class="kg-callout kg-callout--pitfall" style="margin-top:12px">${icon('warn')}<div>${md(n.p, { list: true })}</div></div>` : ''}</div></details>` : ''}
      <details class="kg-section" open><summary>Keys</summary><div class="kg-section-body--flush"><table class="kg-table"><tbody>${keys.length ? keys.map(k => `<tr><td class="kg-c-keys">${keyTag(k.kind)}</td><td class="kg-c-mono">${esc(k.cols.join(', '))}</td><td class="kg-c-type">${esc(k.name)}</td></tr>`).join('') : `<tr><td>${pk.length ? '' : '<span class="kg-badge kg-badge--fanout">no primary key</span>'}</td></tr>`}</tbody></table></div></details>
      <details class="kg-section" open><summary>Joins in this area<span class="kg-section-count">${sameArea.length}</span></summary><div class="kg-section-body--flush">
        ${sameArea.slice(0, 12).map(({ h, e }) => { const far = h.rev ? e[0] : e[1]; return `<div class="kg-rel"><div class="kg-rel-line"><span class="kg-rel-dir">${icon(h.rev ? 'arrowL' : 'arrowR', 'kg-icon--sm')}</span>${entNF(far, 28)}${conf(e[4])}</div><div class="kg-rel-join">${esc(tname(e[0]))}.${esc(colsTxt(e[0], e[2]))}<span class="o">=</span>${esc(tname(e[1]))}.${esc(colsTxt(e[1], e[3]))}</div></div>`; }).join('') || '<p class="kg-muted" style="margin:8px 20px;font:var(--kg-type-small)">None above the confidence floor.</p>'}
        ${sameArea.length > 12 ? `<div class="kg-relcollapsed" style="padding:0 20px">${sameArea.length - 12} more<a href="${hT(t)}">Open table</a></div>` : ''}
      </div></details>
      ${codeSection(codeT(t), t, tid(t), true)}
      ${querySection(t, false)}
     </div>`;
  }
  const relCount = t => adj[t].filter(h => E[h.ei][4] >= floor()).length;
  function keyRows(t) {
    const out = [], pk = T[t][3];
    if (pk.length) out.push({ kind: 'PK', cols: pk.map(c => cname(t, c)), name: (T[t][4].find(ix => ix[2]) || ['PRIMARY'])[0] });
    T[t][4].forEach(ix => { if (ix[2]) return; out.push({ kind: ix[1] ? 'UK' : 'IX', cols: ix[3].map(c => cname(t, c)), name: ix[0] }); });
    return out;
  }

  /* ---------- 3. Path ---------- */
  function pathField(kind, t) {
    return `<label class="kg-pathfield"><small>${kind === 'from' ? 'From' : 'To'}</small>${t >= 0 ? glyph(t) : ''}<input data-path-${kind} value="${t >= 0 ? esc(tid(t)) : ''}" placeholder="schema.table" autocomplete="off" spellcheck="false" aria-label="${kind === 'from' ? 'From table' : 'To table'}"></label>`;
  }
  function renderPath(aName, bName) {
    const a = resolveTable(aName), b = resolveTable(bName);
    const key = a + '|' + b + '|' + state.maxHops + '|' + floor();
    if (key !== state.pathKey) { state.pathKey = key; state.pathRoute = 0; }
    const head = `<h1>Path</h1><span class="kg-faint" style="font:var(--kg-type-small)">Routes ranked by score: the product of step confidences, lowered for passing through a hub, a fan-out step or a log or scratch table</span>`;
    const bar = `<div class="kg-pathbar">${pathField('from', a)}<button class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" data-act="swap" aria-label="Swap from and to" title="Swap">${icon('swap')}</button>${pathField('to', b)}
      <span class="kg-spacer"></span><span class="kg-field-label">Max joins</span><div class="kg-seg" role="group" aria-label="Max joins">${[3, 4, 5].map(h => `<button data-act="hops" data-n="${h}" ${state.maxHops === h ? 'aria-pressed="true"' : ''}>${h}</button>`).join('')}</div></div>`;
    const wrap = inner => `<div style="display:grid;grid-template-rows:auto 1fr;min-height:0;background:var(--kg-canvas)">${bar}<div class="kg-pathscroll">${inner}</div></div>`;
    const ranking = `<details class="kg-section" open><summary>Ranking</summary><div class="kg-section-body"><p class="kg-muted" style="margin:0;font:var(--kg-type-small)">Score is the product of step confidences, then the number of joins. Passing through a hub (×${HUB_PENALTY}), a one-to-many step (×${FANOUT_PENALTY}) or a log or scratch table such as *_log, *_test, *_shadow, *_tmp or *_backup (×${LOG_PENALTY}) lowers the score; the tables you pick as From and To never count as hubs. Each penalty is shown on its step. Relations below ${floor().toFixed(2)} are not used.</p></div></details>`;
    const unknown = (aName && a < 0 ? aName : '') || (bName && b < 0 ? bName : '');
    if (a < 0 || b < 0 || a === b) {
      const msg = unknown ? `<div class="kg-empty">${bigIcon('empty')}<h3>No table called “${esc(unknown)}”</h3><p>Type part of a name in the fields above and pick a table from the list.</p></div>`
        : a === b && a >= 0 ? `<div class="kg-empty">${bigIcon('nopath')}<h3>Pick two different tables</h3><p>From and To are the same table.</p></div>`
          : `<div class="kg-empty">${bigIcon('path')}<h3>Join two tables</h3><p>Pick where you start and where you want to end. keygraph finds the most trustworthy route and writes the SQL.</p></div>`;
      page('path', head, wrap(msg), `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Path</span></div><h2 class="kg-panel-title" style="font:var(--kg-type-title)">How routes are chosen</h2></div><div class="kg-panel-body">${ranking}</div>`);
      bindPathFields(a, b);
      const f = a < 0 ? $('[data-path-from]') : $('[data-path-to]'); if (f) f.focus();
      return;
    }
    const paths = findPaths(a, b, state.maxHops, floor(), 4, 300000, state.mirrors);
    if (!paths.length) {
      page('path', head, wrap(`<div class="kg-empty">${bigIcon('nopath')}<h3>No route within ${state.maxHops} joins</h3><p>${esc(tid(a))} and ${esc(tid(b))} don’t connect above ${floor().toFixed(2)} confidence.${state.maxHops < 5 || !state.weak ? ' ' + [state.maxHops < 5 ? 'Allow 5 joins' : '', !state.weak ? 'show weak relations' : ''].filter(Boolean).join(' or ') + '.' : ''}</p><div class="kg-toolbar">${state.maxHops < 5 ? '<button class="kg-btn kg-btn--sm" data-act="hops" data-n="5">Allow 5 joins</button>' : ''}${!state.weak ? '<button class="kg-btn kg-btn--sm kg-btn--ghost" data-act="weak-on">Show weak relations</button>' : ''}</div></div>`),
        `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Path</span></div><h2 class="kg-panel-title" style="font:var(--kg-type-title)">${esc(tname(a))} → ${esc(tname(b))}</h2></div><div class="kg-panel-body">${ranking}</div>`);
      bindPathFields(a, b);
      return;
    }
    const ri = Math.min(state.pathRoute, paths.length - 1), p = paths[ri], al = aliases(p.tables);
    const used = p.tables.map(() => new Set());
    p.steps.forEach((s, i) => { s.nc.forEach(c => used[i].add(c)); s.fc.forEach(c => used[i + 1].add(c)); });
    const card = (t, i) => {
      const pk = new Set(T[t][3]);
      const rows = [...used[i]].map(c => ({ c, key: pk.has(c) ? 'PK' : 'FK' })).sort((x, y) => (x.key === 'PK' ? 0 : 1) - (y.key === 'PK' ? 0 : 1));
      const end = i === 0 || i === p.tables.length - 1;
      return `<div class="kg-chain-card ${end ? 'kg-chain-card--end' : ''}" style="--kg-glyph-color:${tcolor(t)}"><div class="kg-tcard-head"><span class="kg-glyph kg-glyph--table"></span><a class="kg-entity-name" href="${hT(t)}" style="color:inherit;text-decoration:none" title="${esc(tid(t))}">${esc(tname(t))}</a><span class="kg-alias">${esc(al[i])}</span></div><div style="padding:4px 0">${rows.map(r => `<div class="kg-tcard-row"><span>${keyTag(r.key)}</span><a class="kg-entity-name" href="${hC(t, r.c)}" style="color:inherit;text-decoration:none" title="${esc(cname(t, r.c) + ' ' + T[t][2][r.c][1])}">${esc(cname(t, r.c))}</a></div>`).join('')}</div><div class="kg-chain-area">${esc(AREAS[areaOf[t]].name)}</div></div>`;
    };
    const brk = x => esc(x).replace(/_/g, '_<wbr>');
    const warnLabel = (w, s) => w === 'hub' ? 'hub: ' + tname(s.far) : WARN_TXT[w];
    // the connector carries only the join and its confidence; cardinality, reason and warnings live in the panel
    const step = (s, i) => { const e = E[s.ei]; return `<div class="kg-step ${eDecl(e) ? '' : 'kg-step--inferred'}" data-step="${i + 1}" title="${esc(CARD_TXT[s.card] + ' · ' + e[8].join(' · '))}${s.warnings.length ? esc(' · warning: ' + s.warnings.map(w => warnLabel(w, s)).join(', ')) : ''}"><div class="kg-step-on">${s.fc.map((c, j) => `${esc(al[i + 1])}.${brk(cname(s.far, c))}<br><span class="o">=</span> ${esc(al[i])}.${brk(cname(s.near, s.nc[j]))}`).join('<br>')}</div><div class="kg-step-line"></div><div class="kg-step-meta">${conf(e[4])}${s.warnings.length ? `<span class="kg-step-warn" aria-label="warning">${icon('warn', 'kg-icon--sm')}</span>` : ''}</div></div>`; };
    let chain = card(p.tables[0], 0);
    p.steps.forEach((s, i) => { chain += `<div class="kg-chain-unit">${step(s, i)}${card(p.tables[i + 1], i + 1)}</div>`; });
    const allW = p.steps.flatMap(s => s.warnings), wc = {}; allW.forEach(w => { wc[w] = (wc[w] || 0) + 1; });
    const sqlText = sqlFor(p, al);
    const sqlHtml = esc(sqlText).replace(/^(-- .*)$/gm, '<span class="c">$1</span>').replace(/\b(SELECT|FROM|JOIN|ON|AS|AND)\b/g, '<span class="k">$1</span>');
    const inferred = p.steps.filter(s => !eDecl(E[s.ei])).length;
    page('path', head, wrap(`
      <div class="kg-route-head"><span class="kg-rank ${ri === 0 ? 'kg-rank--best' : ''}">${ri + 1}</span><h2>${ri === 0 ? 'Best route' : 'Route ' + (ri + 1)}</h2>${conf(p.confidence)}<span class="kg-chip">${p.hops} join${p.hops > 1 ? 's' : ''}</span>${Object.entries(wc).map(([w, c]) => warn(`${c} ${WARN_TXT[w]} step${c > 1 ? 's' : ''}`)).join('')}<span class="kg-chip kg-chip--mono" title="ranking score: confidence × penalties">score ${p.score.toFixed(3)}</span></div>
      <div class="kg-chain">${chain}</div>
      <div class="kg-sqlblock"><div class="kg-sqlblock-head"><span class="kg-overline">SQL</span><span class="kg-faint" style="font:var(--kg-type-caption)">aliases match the cards</span><span class="kg-spacer"></span><button class="kg-btn kg-btn--primary kg-btn--sm" data-act="copy-sql">${icon('copy', 'kg-icon--sm')}Copy SQL</button></div><pre class="kg-code" id="sql">${sqlHtml}</pre></div>
      ${paths.length > 1 ? `<div class="kg-alts"><h3 class="kg-overline">Other routes</h3>${paths.map((q, i) => i === ri ? '' : `<div class="kg-alt" data-act="route" data-i="${i}" role="button" tabindex="0" title="Show this route"><span class="kg-rank">${i + 1}</span><span class="kg-alt-route">${q.tables.map(t => `<b>${esc(tname(t))}</b>`).join('<span class="o">→</span>')}</span><span class="kg-alt-warns">${[...new Set(q.steps.flatMap(s => s.warnings.map(w => warnLabel(w, s))))].map(warn).join('')}</span><span class="kg-alt-score"><span class="kg-chip kg-chip--mono" title="ranking score">score ${q.score.toFixed(3)}</span>${conf(q.confidence)}</span></div>`).join('')}</div>` : ''}`),
      `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-overline">Route ${ri + 1} · evidence</span></div><h2 class="kg-panel-title" style="font:var(--kg-type-title)" title="${esc(tid(a))} → ${esc(tid(b))}">${esc(midTrunc(tname(a), 22))} → ${esc(midTrunc(tname(b), 22))}</h2>
        <div class="kg-panel-meta"><span>Score <b>${p.score.toFixed(3)}</b></span><span>Confidence <b>${p.confidence.toFixed(2)}</b></span><span><b>${p.hops}</b> joins</span><span><b>${inferred}</b> inferred</span></div></div>
       <div class="kg-panel-body">
        <details class="kg-section" open><summary>Why each step</summary><div class="kg-section-body--flush">
          ${p.steps.map((s, i) => { const e = E[s.ei]; return `<div class="kg-rel"><div class="kg-rel-line"><span class="kg-hop">${i + 1}</span><span class="kg-mono kg-rel-head" style="font-size:12px" title="${esc(tid(s.far) + '.' + colsTxt(s.far, s.fc))}">${esc(midTrunc(tname(s.far), 24))}.${esc(colsTxt(s.far, s.fc))}</span>${conf(e[4])}</div><div class="kg-rel-tags"><span class="kg-chip" data-kind="${eDecl(e) ? 'declared' : 'inferred'}"><span>${STATUS[e[5]] || 'inferred'}</span></span><span class="kg-chip">${esc(CARD_TXT[s.card])}</span>${s.warnings.map(w => warn(warnLabel(w, s))).join('')}${e[7] ? '<span class="kg-chip kg-chip--warn">ambiguous</span>' : ''}</div>
            <details class="kg-rel-evidence" ${i < 2 ? 'open' : ''}><summary>Evidence</summary><ul>${e[8].map(x => `<li>${esc(x)}</li>`).join('') || '<li>No evidence recorded</li>'}${s.card === 3 ? `<li data-sign="-">One ${esc(tname(s.near))} row has many ${esc(tname(s.far))} rows, so sums of ${esc(tname(s.near))} columns repeat</li>` : ''}</ul></details></div>`; }).join('')}
        </div></details>${ranking}
       </div>`);
    KG.maxNodesDrawn = p.tables.length;
    bindPathFields(a, b);
  }
  function shortWhy(e) { const ev = e[8][0] || (eDecl(e) ? 'Declared foreign key' : ''); return ev.length > 60 ? ev.slice(0, 57) + '…' : ev; }
  function sqlFor(p, al) {
    const fan = p.steps.filter(s => s.card === 3);
    return `SELECT ${al[0]}.*, ${al[al.length - 1]}.*\n` + p.sql + (fan.length ? '\n' + fan.map(s => `-- fan-out: one ${tname(s.near)} row has many ${tname(s.far)} rows; aggregate before summing ${tname(s.near)} columns`).join('\n') : '');
  }
  function bindPathFields(a, b) {
    const go = (from, to) => { location.hash = hP(from, to); };
    const from = $('[data-path-from]'), to = $('[data-path-to]');
    [from, to].forEach(x => { if (x && document.activeElement !== x) x.scrollLeft = x.scrollWidth; });
    if (from) attachAuto(from, r => go(r.e.t, b), true);
    if (to) attachAuto(to, r => go(a, r.e.t), true);
  }

  /* autocomplete popover for an input: tables only, or every kind (header search) */
  function attachAuto(input, pick, tablesOnly, onEnterEmpty) {
    const host = input.closest('.kg-pathfield, .kg-search') || input.parentElement;
    let pop = null, items = [], sel = 0;
    const close = () => { if (pop) { pop.remove(); pop = null; } };
    const show = () => {
      const q = input.value.trim(); items = q ? search(q, { tablesOnly, limit: tablesOnly ? 10 : 8 }) : [];
      if (tablesOnly && q && resolveTable(q) >= 0 && (!items[0] || items[0].e.t !== resolveTable(q))) items.unshift({ e: { k: 'table', t: resolveTable(q), label: tid(resolveTable(q)) }, reason: 'name', rtxt: '' });
      sel = 0; close();
      if (!q) return;
      pop = document.createElement('div'); pop.className = 'kg-popover'; pop.setAttribute('role', 'listbox');
      pop.innerHTML = items.length ? items.map((r, i) => `<div class="kg-menu-item" role="option" data-i="${i}" ${i === 0 ? 'aria-selected="true"' : ''}>${resultGlyph(r.e)}<span class="kg-mono" style="font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${hl(r.e.label, q)}</span><small style="margin-left:auto;white-space:nowrap">${tablesOnly ? esc(AREAS[areaOf[r.e.t]].name) : esc(r.e.k + ' · ' + reasonTxt(r.reason))}</small></div>`).join('') + (tablesOnly ? '' : `<div class="kg-menu-foot"><span><span class="kg-kbd">↵</span> open</span><span><span class="kg-kbd">↑</span><span class="kg-kbd">↓</span> move</span><a href="#search/${enc(q)}" style="margin-left:auto;color:inherit">All results</a></div>`)
        : `<div class="kg-menu-item" style="cursor:default"><small>No ${tablesOnly ? 'table' : 'match'} for “${esc(q)}”</small></div>`;
      pop.addEventListener('mousedown', ev => { const d = ev.target.closest('[data-i]'); if (d) { ev.preventDefault(); close(); pick(items[+d.dataset.i]); } });
      host.appendChild(pop);
    };
    const mark = () => { if (pop) pop.querySelectorAll('[data-i]').forEach((x, i) => x.setAttribute('aria-selected', i === sel ? 'true' : 'false')); };
    input.addEventListener('input', show);
    input.addEventListener('focus', () => { if (input.value && !tablesOnly) show(); });
    input.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowDown') { sel = Math.min(items.length - 1, sel + 1); mark(); ev.preventDefault(); }
      else if (ev.key === 'ArrowUp') { sel = Math.max(0, sel - 1); mark(); ev.preventDefault(); }
      else if (ev.key === 'Enter') {
        ev.preventDefault();
        if (!pop && input.value.trim()) show();
        if (items[sel]) { const r = items[sel]; close(); pick(r); } else if (onEnterEmpty) onEnterEmpty(input.value);
      } else if (ev.key === 'Escape') { close(); input.blur(); }
    });
    input.addEventListener('blur', () => setTimeout(close, 150));
  }
  function bindSearchBox() {
    const s = $('.kg-viewhead [data-search]'); if (!s) return;
    attachAuto(s, r => { location.hash = openHref(r); }, false, q => { location.hash = '#search/' + enc(q); });
  }

  /* ---------- 4. Table ---------- */
  function colFacts(t) {
    const pk = new Set(T[t][3]), uk = new Set(), ix = {}, fk = new Map();
    T[t][4].forEach(x => { if (x[2]) return; if (x[1]) x[3].forEach(c => uk.add(c)); else x[3].forEach((c, i) => { if (!(c in ix)) ix[c] = i + 1; }); });
    adj[t].forEach(h => { const e = E[h.ei]; if (e[0] === t && e[4] >= floor()) e[2].forEach(c => { const l = fk.get(c) || (fk.set(c, { hub: [], fk: false }), fk.get(c)); if (eHubOrRole(e)) l.hub.push(tname(e[1])); else l.fk = true; }); });
    const poly = new Set(); T[t][5].forEach(p => { poly.add(p[0]); poly.add(p[1]); });
    return { pk, uk, ix, fk, poly };
  }
  function codesOf(t, c) {
    const col = T[t][2][c], cs = colsem[t + ':' + col[0]], p = prof(t, c);
    const doc = new Map(); if (cs && cs.k) Object.entries(cs.k).forEach(([k, l]) => doc.set(String(k), l)); (col[4] || []).forEach(m => { if (!doc.has(m)) doc.set(m, ''); });
    const rec = (p && p.reconcile) || {};
    const und = new Set(rec.undocumented_codes || []), unseen = rec.unseen_codes || [];
    let rows = [];
    if (p && !p.pii && p.top_values && p.top_values.length) {
      const nn = (p.sample_size || 0) * (1 - (p.null_rate || 0)) || p.top_values.reduce((s, v) => s + v[1], 0);
      rows = p.top_values.slice(0, 16).map(([v, n]) => ({ v: String(v), label: doc.get(String(v)) || '', pc: nn ? 100 * n / nn : 0, und: und.has(String(v)) }));
    } else if (doc.size) rows = [...doc.entries()].map(([v, l]) => ({ v, label: l, pc: null, und: false }));
    return { rows, doc, und, unseen, complete: !!(p && p.values_complete) };
  }
  function valuesCell(t, c, f) {
    const col = T[t][2][c], cs = colsem[t + ':' + col[0]], p = prof(t, c);
    const codes = cs && cs.k ? Object.entries(cs.k) : (col[4] || []).map(m => [m, '']);
    if (codes.length) return `<span class="kg-enum" style="flex-wrap:nowrap">${codes.slice(0, codes.length > 4 ? 3 : 4).map(([k, l]) => `<code>${esc(k)}${l ? ' ' + esc(l) : ''}</code>`).join('')}${codes.length > 4 ? `<code>+${codes.length - 3}</code>` : ''}</span>`;
    if (p) {
      if (p.pii) return '<span class="kg-faint">values withheld (PII)</span>';
      if (p.numeric) return `<span class="kg-faint">${esc(p.numeric.min)} – ${esc(p.numeric.max)}</span>`;
      if (p.dates) return `<span class="kg-faint">${esc(String(p.dates.min).slice(0, 10))} – ${esc(String(p.dates.max).slice(0, 10))}</span>`;
      if (p.top_values && p.top_values.length) { const nn = (p.sample_size || 1) * (1 - (p.null_rate || 0)) || 1; return `<span class="kg-faint">top ${esc(String(p.top_values[0][0]).slice(0, 24))} ${pct(p.top_values[0][1] / nn)}</span>`; }
      if (p.distinct_in_sample != null) return `<span class="kg-faint">${fmt(p.distinct_in_sample)} distinct</span>`;
    }
    if (col[3] != null) return `<span class="kg-faint">default ${esc(col[3])}</span>`;
    return '';
  }
  function renderTable(name) {
    const t = resolveTable(name);
    if (t < 0) return notFound('table', name);
    pushRecent(t);
    const ai = areaOf[t], n = tnote[t], f = colFacts(t), cols = T[t][2];
    if (state.panelCol == null || state.panelCol.t !== t) state.panelCol = { t, c: defaultCol(t) };
    const rows = cols.map((col, c) => {
      const p = prof(t, c), cs = colsem[t + ':' + col[0]], fk = f.fk.get(c), cd = codesOf(t, c);
      const keys = [f.pk.has(c) ? 'PK' : '', f.uk.has(c) ? 'UK' : '', fk && fk.fk ? 'FK' : '', f.ix[c] ? 'IX ' + f.ix[c] : ''].filter(Boolean);
      const empty = p && p.null_rate != null ? Math.round(p.null_rate * 1000) / 10 : null;
      const flags = [fk && fk.hub.length ? `<span class="kg-hub">→ ${esc(fk.hub[0])}</span>` : '', (cs && cs.pii) || (p && p.pii) ? '<span class="kg-badge kg-badge--pii">PII</span>' : '',
        cd.und.size ? `<span class="kg-badge kg-badge--fanout" title="seen in the data but not documented: ${esc([...cd.und].join(', '))}">undocumented ${esc([...cd.und].slice(0, 2).join(', '))}${cd.und.size > 2 ? '…' : ''}</span>` : '',
        cd.unseen.length ? `<span class="kg-badge kg-badge--unverified" title="documented but never seen: ${esc(cd.unseen.join(', '))}">never seen ${cd.unseen.length}</span>` : '',
        f.poly.has(c) ? '<span class="kg-chip" data-kind="polymorphic"><span>polymorphic</span></span>' : ''].join('');
      return `<tr data-act="pcol" data-c="${c}" ${state.panelCol.c === c ? 'aria-selected="true"' : ''}><td class="kg-c-name"><a href="${hC(t, c)}">${esc(col[0])}</a></td><td class="kg-c-type">${esc(col[1])}${col[2] ? ' <span class="kg-chip kg-chip--mono" style="height:16px;padding:0 5px">null</span>' : ''}</td><td class="kg-c-keys"><span class="kg-flags" style="flex-wrap:nowrap">${keys.map(keyTag).join('')}</span></td>
        <td><span class="kg-flags" style="flex-wrap:nowrap">${flags}</span></td><td style="white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis">${valuesCell(t, c, f)}</td>
        <td class="kg-c-num">${empty == null ? '' : `<span class="kg-minibar" title="${empty}% empty"><i style="width:${Math.max(empty ? 3 : 0, empty)}%;background:${empty > 50 ? 'var(--kg-warning)' : 'var(--kg-text-3)'}"></i></span>${empty ? empty + '%' : '<span class="kg-faint">0</span>'}`}</td></tr>`;
    }).join('');
    const sum = relSummary(t, floor());
    const keys = keyRows(t);
    const facts = [T[t][8] != null ? `<span class="kg-fact"><b>${fmt(T[t][8])}</b> rows</span>` : '', `<span class="kg-fact"><b>${cols.length}</b> columns</span>`, `<span class="kg-fact"><b>${relCount(t)}</b> relations</span>`,
      n && n.fr ? `<span class="kg-fact">${esc(n.fr)}</span>` : '', n && n.sd ? `<span class="kg-fact">soft delete: <code class="kg-mono" style="font-size:11.5px">${esc(n.sd)}</code></span>` : '',
      n && n.dc ? `<span class="kg-fact">date: <code class="kg-mono" style="font-size:11.5px">${esc(n.dc)}</code></span>` : '', isHubT(t) ? '<span class="kg-fact">hub table</span>' : '', isLogT(t) ? '<span class="kg-fact">log-like name</span>' : '',
      T[t][5].length ? '<span class="kg-fact">polymorphic</span>' : ''].join('');
    const ctxHtml = contextBlock(t);
    page('table', `<nav class="kg-crumbs"><a href="#areas">Areas</a><span class="sep">/</span><a href="${hA(ai)}">${esc(AREAS[ai].name)}</a><span class="sep">/</span><span aria-current="page">${esc(tid(t))}</span></nav><span class="kg-spacer"></span>
        <a class="kg-btn kg-btn--sm" href="${hP(t)}">${icon('path', 'kg-icon--sm')}Path from here</a><a class="kg-btn kg-btn--sm" href="${hA(ai)}">${icon('area', 'kg-icon--sm')}Open area</a>`,
      `<div class="kg-doc"><div class="kg-doc-inner">
        <div class="kg-doc-head"><div class="kg-panel-kicker">${glyph(t)}<span class="kg-overline">Table in ${esc(AREAS[ai].name)}</span>${n ? `<span class="kg-badge kg-badge--${n.v ? 'verified' : 'unverified'}">context ${n.v ? 'verified' : 'unverified'}</span>` : ''}</div>
          <h1 class="kg-doc-title"><span class="kg-entity-schema">${esc(tschema(t))}.</span>${esc(tname(t))}</h1>${n && n.g ? `<p class="kg-doc-grain">${md(n.g)}</p>` : '<p class="kg-doc-grain kg-faint" style="font:var(--kg-type-body)">No context says what one row is yet.</p>'}
          <div class="kg-doc-facts">${facts}</div>${T[t][6] ? `<p class="kg-faint kg-mono" style="margin:4px 0 0;font-size:11.5px">${esc(T[t][6])}</p>` : ''}</div>
        ${isMirrorT(t) ? `<div class="kg-callout" style="margin-top:16px">${icon('table')}<div><p><b>Mirror schema.</b> ${esc(tschema(t))} copies ${esc(MIRRORS[tschema(t)] || '')}${resolveTable(MIRRORS[tschema(t)] + '.' + tname(t)) >= 0 ? `; the original is ${ent(resolveTable(MIRRORS[tschema(t)] + '.' + tname(t)))}` : ''}. Paths and areas skip it unless “Show mirror schemas” is on.</p></div></div>` : ''}
        ${ctxHtml}
        <h2>Columns <span class="kg-section-count">${cols.length}</span>${cols.length > 12 ? `<input class="kg-input kg-col-filter" data-col-filter placeholder="Filter columns" aria-label="Filter columns" style="margin-left:auto;width:200px;height:28px">` : ''}</h2>
        <div class="kg-boxed"><table class="kg-table kg-table--clickable kg-cols-grid"><thead><tr><th>Column</th><th>Type</th><th>Keys</th><th>Flags</th><th>Values</th><th style="text-align:right">Empty</th></tr></thead><tbody>${rows}</tbody></table></div>
        <h2>Keys and indexes <span class="kg-section-count">${keys.length}</span></h2>
        ${keys.length ? `<div class="kg-boxed"><table class="kg-table"><thead><tr><th style="width:1%">Kind</th><th>Columns, in order</th><th>Name</th></tr></thead><tbody>${keys.map(k => `<tr><td>${keyTag(k.kind)}</td><td class="kg-c-mono">${k.cols.map((x, i) => `${i ? '<span class="kg-faint"> → </span>' : ''}${esc(x)}`).join('')}</td><td class="kg-c-type">${esc(k.name)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="kg-callout kg-callout--pitfall">' + icon('warn') + '<div><p><b>No primary key or index.</b> Joins on this table scan it.</p></div></div>'}
        ${codeT(t) ? `<div class="kg-boxed" style="margin-top:24px;background:var(--kg-surface)">${codeSection(codeT(t), t, tid(t), true)}</div>` : ''}
        ${Q[t] ? `<div class="kg-boxed" style="margin-top:24px;background:var(--kg-surface)">${querySection(t, true)}</div>` : ''}
        <div class="kg-two">${relGroupsHtml(t, sum, 'out')}${relGroupsHtml(t, sum, 'in')}</div>
      </div></div>`,
      panelColumn(t, state.panelCol.c));
    const cf = $('[data-col-filter]');
    if (cf) cf.addEventListener('input', () => { const q = norm(cf.value); $$('.kg-cols-grid tbody tr').forEach(r => { r.hidden = !!q && !norm(cols[+r.dataset.c][0] + ' ' + cols[+r.dataset.c][1]).includes(q); }); });
  }
  function defaultCol(t) {
    const cols = T[t][2];
    const withCodes = cols.findIndex((col, c) => (colsem[t + ':' + col[0]] && colsem[t + ':' + col[0]].k && Object.keys(colsem[t + ':' + col[0]].k).length) || (prof(t, c) && prof(t, c).reconcile && prof(t, c).reconcile.undocumented_codes));
    if (withCodes >= 0) return withCodes;
    const withCtx = cols.findIndex(col => colsem[t + ':' + col[0]]);
    return withCtx >= 0 ? withCtx : (T[t][3][0] ?? 0);
  }
  function contextBlock(t) {
    const n = tnote[t], ms = tmetrics[t], cs = tconcepts[t];
    if (!n && !ms.length && !cs.length) return '';
    return `<h2>Context</h2><div style="display:grid;gap:12px;max-width:760px">
      ${n && n.sm ? `<p style="margin:0">${md(n.sm)}</p>` : ''}
      ${n && n.p ? `<div class="kg-callout kg-callout--pitfall">${icon('warn')}<div><p><b>Pitfall.</b></p>${md(n.p, { list: true })}</div></div>` : ''}
      ${n && n.ri && n.ri.length ? `<dl class="kg-kv"><dt>Recommended indexes</dt><dd class="kg-mono" style="font-size:12px">${n.ri.map(esc).join('<br>')}</dd></dl>` : ''}
      ${ms.length ? `<div><div class="kg-overline" style="margin-bottom:6px">Metrics that use this table</div>${ms.map(mi => { const m = C.metrics[mi]; return `<div style="margin-bottom:8px"><span class="kg-entity"><span class="kg-glyph kg-glyph--metric"></span><span class="kg-entity-name" style="font-family:var(--kg-font-sans);font-size:13px;font-weight:550">${esc(m.n)}</span></span> <span class="kg-badge kg-badge--${m.v ? 'verified' : 'unverified'}">${m.v ? 'verified' : 'unverified'}</span><div class="kg-muted" style="font:var(--kg-type-small)">${md(m.d || '')}${m.g ? ' · grain: ' + esc(m.g) : ''}</div>${m.e ? `<pre class="kg-code" style="margin:6px 0 0">${esc(m.e)}</pre>` : ''}${m.cv ? `<div class="kg-callout kg-callout--pitfall" style="margin-top:6px">${icon('warn')}<div><p><b>Caveat.</b> ${md(m.cv)}</p></div></div>` : ''}</div>`; }).join('')}</div>` : ''}
      ${cs.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span class="kg-overline">Concepts</span>${cs.map(([ci, role, col]) => `<a class="kg-chip" href="#search/${enc(C.concepts[ci].n)}" title="${esc(C.concepts[ci].d || '')}"><span class="kg-glyph kg-glyph--concept"></span>${esc(C.concepts[ci].n)}<span class="kg-faint">${esc(role)}${col ? ' · ' + esc(col) : ''}</span></a>`).join('')}</div>` : ''}
      ${n && n.s ? `<p class="kg-faint" style="margin:0;font:var(--kg-type-caption)">Source: <span class="kg-mono">${esc(n.s)}</span></p>` : ''}
    </div>`;
  }
  function relItem(t, far, g) {
    const e = g.edges.map(ei => E[ei]).find(x => (x[0] === far || x[1] === far)) || E[g.edges[0]];
    return `<div class="kg-relitem">${ent(far)}<span></span><span style="display:flex;gap:8px;align-items:center">${eDecl(e) ? '<span class="kg-chip" data-kind="declared"><span>declared</span></span>' : conf(e[4])}<span class="kg-relitem-acts"><a class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" href="${hP(t, far)}" title="Path from here to ${esc(tid(far))}" aria-label="Path from here">${icon('path', 'kg-icon--sm')}</a><a class="kg-btn kg-btn--ghost kg-btn--icon kg-btn--sm" href="${hA(areaOf[far])}" title="Open area ${esc(AREAS[areaOf[far]].name)}" aria-label="Open area">${icon('area', 'kg-icon--sm')}</a></span></span></div>`;
  }
  /* Groups are by column, then area (relation_summary). A column whose tables spread over several areas and
     exceed GROUP_COLLAPSE folds into one row first, so a hub's 500 referrers never become 150 small groups. */
  function relGroupsHtml(t, sum, dir, scopeCol) {
    const groups = sum[dir], total = groups.reduce((s, g) => s + g.count, 0);
    const title = dir === 'out' ? 'Points to' : 'Pointed to by', sc = scopeCol != null ? ':' + scopeCol : '';
    const byCol = new Map(); groups.forEach(g => (byCol.get(g.column) || byCol.set(g.column, []).get(g.column)).push(g));
    const colName = g => `${dir === 'in' ? '' : esc(tname(t)) + '.'}${esc(g.column)}`;
    const one = g => {
      const k = t + ':' + dir + ':' + g.key + sc;
      const head = `<div class="kg-relgroup-head">via <code class="kg-mono">${colName(g)}</code>${g.area >= 0 ? ` <span class="kg-faint">· ${esc(AREAS[g.area].name)}</span>` : ''}</div>`;
      if (g.collapsed && !state.expanded.has(k)) return head + g.tables.slice(0, 3).map(far => relItem(t, far, g)).join('') + `<div class="kg-relcollapsed" data-group-collapsed="${g.count}">${g.count} tables via <code class="kg-mono">${esc(g.column)}</code><span class="kg-faint">in ${esc(AREAS[g.area] ? AREAS[g.area].name : '')}</span><a data-act="expand" data-k="${esc(k)}" role="button" tabindex="0">Show all ${g.count}</a></div>`;
      return head + g.tables.map(far => relItem(t, far, g)).join('') + (g.collapsed ? `<div class="kg-relcollapsed"><span class="kg-faint">${g.count} tables</span><a data-act="expand" data-k="${esc(k)}" role="button" tabindex="0">Collapse</a></div>` : '');
    };
    const body = [...byCol.entries()].map(([col, gs]) => {
      const n = gs.reduce((s, g) => s + g.count, 0), k = t + ':' + dir + ':col:' + col + sc;
      if (gs.length < 2 || n <= GROUP_COLLAPSE) return gs.map(one).join('');
      const areas = gs.slice().sort((a, b) => b.count - a.count).slice(0, 3).map(g => AREAS[g.area] ? AREAS[g.area].name : '').join(', ');
      if (!state.expanded.has(k)) return `<div class="kg-relgroup-head">via <code class="kg-mono">${colName(gs[0])}</code> <span class="kg-faint">· ${gs.length} areas</span></div><div class="kg-relcollapsed" data-group-collapsed="${n}">${n} tables via <code class="kg-mono">${esc(col)}</code><span class="kg-faint" title="${esc(areas)}${gs.length > 3 ? '…' : ''}">in ${gs.length} areas</span><a data-act="expand" data-k="${esc(k)}" role="button" tabindex="0">Show</a></div>`;
      return gs.map(one).join('') + `<div class="kg-relcollapsed"><span class="kg-faint">${n} tables via ${esc(col)}</span><a data-act="expand" data-k="${esc(k)}" role="button" tabindex="0">Collapse</a></div>`;
    }).join('');
    const hubs = dir === 'out' && sum.hubs.length ? `<div class="kg-hubline">Hubs ${sum.hubs.map(h => `<a class="kg-hub" href="${hT(h.to)}" title="${esc(tid(h.to))} via ${esc(h.columns.join(', '))} (${h.count})">→ ${esc(tname(h.to))}</a>`).join('')}<span class="kg-faint">via ${esc([...new Set(sum.hubs.flatMap(h => h.columns))].slice(0, 4).join(', '))}</span></div>` : '';
    return `<div><h2>${title} <span class="kg-section-count">${total}</span></h2><div class="kg-relgroup">${body || (hubs ? '' : `<div class="kg-relcollapsed"><span class="kg-faint">None above ${floor().toFixed(2)} confidence</span></div>`)}${hubs}</div></div>`;
  }

  /* right panel: one column */
  function panelColumn(t, c) {
    const col = T[t][2][c]; if (!col) return '';
    const cs = colsem[t + ':' + col[0]], f = colFacts(t), cd = codesOf(t, c), p = prof(t, c);
    const ixs = T[t][4].filter(ix => ix[3].includes(c)).map(ix => `${keyTag(ix[2] ? 'PK' : ix[1] ? 'UK' : 'IX ' + (ix[3].indexOf(c) + 1))} in ${esc(ix[0])}`);
    const refs = adj[t].filter(h => { const e = E[h.ei]; return e[4] >= floor() && ((e[0] === t && e[2].includes(c)) || (e[1] === t && e[3].includes(c))); });
    return `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--column"></span><span class="kg-overline">Column</span>${cs ? `<span class="kg-badge kg-badge--${cs.v ? 'verified' : 'unverified'}">${cs.v ? 'verified' : 'unverified'}</span>` : ''}</div>
      <h2 class="kg-panel-title"><span class="kg-entity-schema">${esc(tname(t))}.</span>${esc(col[0])}</h2>
      <div class="kg-panel-meta"><span class="kg-mono">${esc(col[1])} · ${col[2] ? 'nullable' : 'not null'}${col[3] != null ? ' · default ' + esc(col[3]) : ''}</span>${ixs.slice(0, 2).map(x => `<span>${x}</span>`).join('')}</div>
      <div class="kg-panel-actions"><a class="kg-btn kg-btn--sm" href="${hC(t, c)}">${icon('table', 'kg-icon--sm')}Open column</a></div></div>
     <div class="kg-panel-body">${columnSections(t, c, cs, cd, p, refs, true)}</div>`;
  }
  function columnSections(t, c, cs, cd, p, refs, compactRefs, noMeaning) {
    const col = T[t][2][c];
    const codeRows = cd.rows.map(r => `<div class="kg-profile-row"><span>${esc(r.v)} <span class="${r.und ? '' : 'kg-muted'}" style="${r.und ? 'color:var(--kg-warning)' : ''}">${r.und ? 'not documented' : esc(r.label)}</span></span>${r.pc != null ? `<span class="kg-profile-bar"><i style="width:${Math.max(1.5, r.pc).toFixed(1)}%;${r.und ? 'background:var(--kg-warning)' : ''}"></i></span><span>${r.pc.toFixed(1)}%</span>` : '<span></span><span></span>'}</div>`).join('');
    const ctxRows = cs ? [['Unit', cs.u], ['Null means', cs.nm], ['Filter', cs.f], ['Index', cs.ih], ['Source', cs.s]].filter(x => x[1]) : [];
    const ms = tmetrics[t].map(mi => C.metrics[mi]).filter(m => (m.e || '').includes(col[0]) || (m.f || '').includes(col[0]));
    const profRows = p ? [['Sampled', `${fmt(p.sample_size || 0)} rows · ${p.method || ''} · ${String(p.profiled_at || '').slice(0, 10)}`], ['Empty', pct(p.null_rate)], ['Distinct', p.distinct_in_sample != null ? fmt(p.distinct_in_sample) + ' in sample' : ''], ['Pattern', p.pattern],
      ['Range', p.numeric ? `${p.numeric.min} – ${p.numeric.max} (median ${p.numeric.p50})` : ''], ['Dates', p.dates ? `${String(p.dates.min).slice(0, 10)} – ${String(p.dates.max).slice(0, 10)}${p.dates.recency_days != null ? ` (latest ${p.dates.recency_days} d ago)` : ''}` : ''],
      ['Length', p.length ? `${p.length.min}–${p.length.max}, avg ${p.length.avg}` : ''], ['PII', p.pii ? 'yes: raw values withheld' : '']].filter(x => x[1]) : [];
    return `${cs && cs.m && !noMeaning ? `<details class="kg-section" open><summary>Meaning</summary><div class="kg-section-body"><p style="margin:0">${md(cs.m)}</p></div></details>` : ''}
      ${codeRows ? `<details class="kg-section" open><summary>${cd.rows.some(r => r.pc != null) ? 'Values' : 'Codes'}<span class="kg-section-count">${cd.rows.some(r => r.pc != null) ? `${cd.rows.length} seen · ${cd.doc.size} documented` : cd.rows.length}</span></summary><div class="kg-section-body">${codeRows}
        ${cd.und.size ? `<div class="kg-callout kg-callout--pitfall" style="margin-top:12px">${icon('warn')}<div><p>Code${cd.und.size > 1 ? 's' : ''} <b>${esc([...cd.und].join(', '))}</b> ${cd.und.size > 1 ? 'appear' : 'appears'} in the data but no context file documents ${cd.und.size > 1 ? 'them' : 'it'}.</p></div></div>` : ''}
        ${cd.unseen.length ? `<div class="kg-callout" style="margin-top:12px">${icon('warn')}<div><p>Documented but never seen in the sample: <b>${esc(cd.unseen.join(', '))}</b>.</p></div></div>` : ''}</div></details>` : ''}
      ${profRows.length ? `<details class="kg-section" open><summary>Profile</summary><div class="kg-section-body"><dl class="kg-kv">${profRows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div></details>` : ''}
      ${ctxRows.length || ms.length ? `<details class="kg-section" open><summary>Use</summary><div class="kg-section-body"><dl class="kg-kv">${ctxRows.map(([k, v]) => `<dt>${k}</dt><dd>${k === 'Source' ? esc(v) : md(v)}</dd>`).join('')}${ms.length ? `<dt>Metrics</dt><dd>${ms.map(m => `<span class="kg-entity"><span class="kg-glyph kg-glyph--metric"></span><span class="kg-entity-name" style="font-family:var(--kg-font-sans);font-size:13px">${esc(m.n)}</span></span>`).join('<br>')}</dd>` : ''}</dl></div></details>` : ''}
      ${codeSection(codeC(t, c), t, tid(t) + '.' + col[0], !compactRefs)}
      ${col[5] ? `<details class="kg-section"><summary>Details</summary><div class="kg-section-body"><p class="kg-mono" style="margin:0;font-size:12px">${esc(col[5])}</p></div></details>` : ''}
      ${compactRefs ? `<details class="kg-section" ${refs.length ? 'open' : ''}><summary>References<span class="kg-section-count">${refs.length || 'none'}</span></summary>${refs.length ? `<div class="kg-section-body--flush">${refs.slice(0, 10).map(h => { const e = E[h.ei], far = h.rev ? e[0] : e[1]; return `<div class="kg-rel"><div class="kg-rel-line"><span class="kg-rel-dir">${icon(h.rev ? 'arrowL' : 'arrowR', 'kg-icon--sm')}</span>${entNF(far, 28)}${conf(e[4])}</div><div class="kg-rel-join">${esc(tname(e[0]))}.${esc(colsTxt(e[0], e[2]))}<span class="o">=</span>${esc(tname(e[1]))}.${esc(colsTxt(e[1], e[3]))}</div></div>`; }).join('')}${refs.length > 10 ? `<div class="kg-relcollapsed" style="padding:0 20px">${refs.length - 10} more<a href="${hC(t, c)}">Open column</a></div>` : ''}</div>` : ''}</details>` : ''}`;
  }

  /* ---------- 5. Column ---------- */
  function renderColumn(name, colName) {
    const t = resolveTable(name); if (t < 0) return notFound('table', name);
    const c = colIndex(t, colName); if (c < 0) return notFound('column', name + '.' + colName);
    pushRecent(t);
    const col = T[t][2][c], ai = areaOf[t], cs = colsem[t + ':' + col[0]], f = colFacts(t), cd = codesOf(t, c), p = prof(t, c);
    const keys = [f.pk.has(c) ? 'PK' : '', f.uk.has(c) ? 'UK' : '', f.fk.get(c) && f.fk.get(c).fk ? 'FK' : '', f.ix[c] ? 'IX ' + f.ix[c] : ''].filter(Boolean);
    const sum = relSummary(t, floor(), e => (e[0] === t && e[2].includes(c)) || (e[1] === t && e[3].includes(c)));
    const ixs = T[t][4].filter(ix => ix[3].includes(c));
    page('column', `<nav class="kg-crumbs"><a href="#areas">Areas</a><span class="sep">/</span><a href="${hA(ai)}">${esc(AREAS[ai].name)}</a><span class="sep">/</span><a href="${hT(t)}">${esc(tid(t))}</a><span class="sep">/</span><span aria-current="page">${esc(col[0])}</span></nav><span class="kg-spacer"></span>
        <a class="kg-btn kg-btn--sm" href="${hT(t)}">${icon('table', 'kg-icon--sm')}Open table</a><a class="kg-btn kg-btn--sm" href="${hP(t)}">${icon('path', 'kg-icon--sm')}Path from here</a>`,
      `<div class="kg-doc"><div class="kg-doc-inner">
        <div class="kg-doc-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--column"></span><span class="kg-overline">Column in ${esc(tid(t))}</span>${cs ? `<span class="kg-badge kg-badge--${cs.v ? 'verified' : 'unverified'}">context ${cs.v ? 'verified' : 'unverified'}</span>` : ''}</div>
          <h1 class="kg-doc-title"><span class="kg-entity-schema">${esc(tname(t))}.</span>${esc(col[0])}</h1>${cs && cs.m ? `<p class="kg-doc-grain">${md(cs.m)}</p>` : '<p class="kg-doc-grain kg-faint" style="font:var(--kg-type-body)">No context explains this column yet.</p>'}
          <div class="kg-doc-facts"><span class="kg-fact kg-mono" style="font-size:12px">${esc(col[1])}</span><span class="kg-fact">${col[2] ? 'nullable' : 'not null'}</span>${col[3] != null ? `<span class="kg-fact">default <code class="kg-mono" style="font-size:11.5px">${esc(col[3])}</code></span>` : ''}${keys.map(k => `<span class="kg-fact">${keyTag(k)}</span>`).join('')}${(cs && cs.pii) || (p && p.pii) ? '<span class="kg-fact"><span class="kg-badge kg-badge--pii">PII</span></span>' : ''}</div></div>
        ${ixs.length ? `<h2>In keys and indexes</h2><div class="kg-boxed"><table class="kg-table"><tbody>${ixs.map(ix => `<tr><td style="width:1%">${keyTag(ix[2] ? 'PK' : ix[1] ? 'UK' : 'IX ' + (ix[3].indexOf(c) + 1))}</td><td class="kg-c-mono">${ix[3].map((x, i) => `${i ? '<span class="kg-faint"> → </span>' : ''}${x === c ? '<b>' + esc(cname(t, x)) + '</b>' : esc(cname(t, x))}`).join('')}</td><td class="kg-c-type">${esc(ix[0])}</td></tr>`).join('')}</tbody></table></div>` : ''}
        <div class="kg-boxed" style="margin-top:24px;background:var(--kg-surface)">${columnSections(t, c, cs, cd, p, [], false, true).trim() || '<p class="kg-muted" style="margin:16px">No profile or context recorded for this column.</p>'}</div>
        <div class="kg-two">${relGroupsHtml(t, sum, 'out', c)}${relGroupsHtml(t, sum, 'in', c)}</div>
      </div></div>`,
      panelTable(t));
  }

  /* ---------- 6. Search ---------- */
  function renderSearch(q) {
    q = q || '';
    page('search', `<h1>Search</h1><span class="kg-faint" style="font:var(--kg-type-small)">Names, synonyms, context notes and profiled values</span>`,
      `<div class="kg-doc"><div class="kg-doc-inner" style="max-width:820px">
        <div class="kg-search-big">${icon('search')}<input data-search value="${esc(q)}" aria-label="Search" placeholder="Search tables, columns, values and business terms" autocomplete="off" spellcheck="false"></div>
        <div id="sres"></div></div></div>`, '<div id="spanel"></div>');
    const input = $('[data-search]');
    let sel = 0, flat = [];
    const draw = () => {
      const qq = input.value.trim(); const t0 = performance.now();
      const all = qq ? search(qq) : [];
      KG.lastSearchMs = performance.now() - t0;
      const counts = {}; all.forEach(r => { counts[r.e.k] = (counts[r.e.k] || 0) + 1; });
      const type = state.searchType !== 'all' && counts[state.searchType] ? state.searchType : 'all';
      const shown = all.filter(r => type === 'all' || r.e.k === type);
      const groups = KINDS.map(k => [k, shown.filter(r => r.e.k === k).slice(0, type === 'all' ? 8 : 60)]).filter(g => g[1].length)
        .sort((a, b) => b[1][0].q - a[1][0].q || b[1][0].s - a[1][0].s); // the group holding the best match comes first
      flat = groups.flatMap(g => g[1]);
      const bestR = shown[0]; sel = Math.max(0, flat.indexOf(bestR));
      let h = '';
      if (!qq) h = `<div class="kg-empty">${bigIcon('empty')}<h3>Find where data lives</h3><p>Search table and column names, business terms and their synonyms, context notes, enum labels and profiled values.</p></div>`;
      else if (!all.length) { const dm = didYouMean(qq); h = `<div class="kg-empty">${bigIcon('empty')}<h3>Nothing matches “${esc(qq)}”</h3><p>${dm ? `Did you mean <a class="kg-link-text" href="#search/${enc(dm)}">${esc(dm)}</a>? ` : ''}Search also reads synonyms and context notes.</p></div>`; }
      else {
        h = `<div class="kg-search-filters"><button class="kg-toggle" data-act="stype" data-k="all" ${type === 'all' ? 'aria-pressed="true"' : ''}>All <span class="kg-faint">${all.length}</span></button>${KINDS.filter(k => counts[k]).map(k => `<button class="kg-toggle" data-act="stype" data-k="${k}" ${type === k ? 'aria-pressed="true"' : ''}>${KIND_LABEL[k]} ${counts[k]}</button>`).join('')}<span class="kg-spacer"></span><span class="kg-faint" style="font:var(--kg-type-caption);align-self:center"><span class="kg-kbd">↵</span> open  <span class="kg-kbd">↑</span><span class="kg-kbd">↓</span> move</span></div>`;
        let i = 0;
        h += groups.map(([k, rs]) => `<div class="kg-result-group"><h3 class="kg-overline">${KIND_LABEL[k]}</h3>${rs.map(r => { const j = i++; return `<a class="kg-result" href="${openHref(r)}" data-ri="${j}" ${j === sel ? 'aria-selected="true"' : ''} style="text-decoration:none">${resultGlyph(r.e)}<span class="kg-result-title ${k === 'concept' || k === 'metric' ? 'is-sans' : ''}" title="${esc(r.e.label)}">${hl(midTrunc(r.e.label, 78), qq)}</span><span class="kg-reason">${esc(resultMeta(r.e))}</span><span class="kg-result-why"><span class="kg-chip" style="height:18px;margin-right:6px">${esc(reasonTxt(r.reason))}</span>${resultWhy(r, qq)}</span></a>`; }).join('')}</div>`).join('');
      }
      $('#sres').innerHTML = h;
      preview();
    };
    const preview = () => {
      const r = flat[sel]; const pn = $('#spanel'); if (!pn) return;
      $$('#sres .kg-result').forEach(x => x.setAttribute('aria-selected', +x.dataset.ri === sel ? 'true' : 'false'));
      if (!r) { pn.innerHTML = ''; return; }
      const e = r.e;
      if (e.k === 'table') pn.innerHTML = panelTable(e.t);
      else if (e.k === 'column' || e.k === 'value') pn.innerHTML = panelColumn(e.t, e.c);
      else if (e.k === 'concept') {
        const c = C.concepts[e.c];
        pn.innerHTML = `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--concept"></span><span class="kg-overline">Concept</span><span class="kg-badge kg-badge--${c.v ? 'verified' : 'unverified'}">${c.v ? 'verified' : 'unverified'}</span></div><h2 class="kg-panel-title" style="font-family:var(--kg-font-sans)">${esc(c.n)}</h2>${c.sy && c.sy.length ? `<div class="kg-panel-meta"><span>Also: ${esc(c.sy.join(', '))}</span></div>` : ''}</div>
          <div class="kg-panel-body">${c.d ? `<details class="kg-section" open><summary>Definition</summary><div class="kg-section-body"><p style="margin:0">${md(c.d)}</p></div></details>` : ''}
          <details class="kg-section" open><summary>Where it lives<span class="kg-section-count">${c.l.length}</span></summary><div class="kg-section-body--flush">${c.l.map(l => { const ci = l[1] ? colIndex(l[0], l[1]) : -1; return `<div class="kg-level" style="grid-template-columns:1fr auto">${ci >= 0 ? colLink(l[0], ci) : ent(l[0])}<span class="kg-faint" style="font:var(--kg-type-caption)">${esc(l[3] || l[2])}</span></div>`; }).join('')}</div></details>
          ${C.rels.filter(x => x[0] === c.n || x[2] === c.n).length ? `<details class="kg-section" open><summary>Related</summary><div class="kg-section-body"><div style="display:flex;gap:6px;flex-wrap:wrap">${C.rels.filter(x => x[0] === c.n || x[2] === c.n).map(x => `<a class="kg-chip" href="#search/${enc(x[0] === c.n ? x[2] : x[0])}">${esc(x[0] === c.n ? x[1] + ' ' + x[2] : x[0] + ' ' + x[1])}</a>`).join('')}</div></div></details>` : ''}</div>`;
      } else {
        const m = C.metrics[e.c];
        pn.innerHTML = `<div class="kg-panel-head"><div class="kg-panel-kicker"><span class="kg-glyph kg-glyph--metric"></span><span class="kg-overline">Metric</span><span class="kg-badge kg-badge--${m.v ? 'verified' : 'unverified'}">${m.v ? 'verified' : 'unverified'}</span></div><h2 class="kg-panel-title" style="font-family:var(--kg-font-sans)">${esc(m.n)}</h2>${m.g ? `<div class="kg-panel-meta"><span>Grain: ${esc(m.g)}</span></div>` : ''}</div>
          <div class="kg-panel-body"><details class="kg-section" open><summary>Definition</summary><div class="kg-section-body"><p style="margin:0">${md(m.d || '')}</p>${m.e ? `<pre class="kg-code" style="margin:10px 0 0">${esc(m.e)}</pre>` : ''}${m.cv ? `<div class="kg-callout kg-callout--pitfall" style="margin-top:12px">${icon('warn')}<div><p>${md(m.cv)}</p></div></div>` : ''}</div></details>
          <details class="kg-section" open><summary>Tables<span class="kg-section-count">${m.t.length}</span></summary><div class="kg-section-body--flush">${m.t.map(t => `<div class="kg-level" style="grid-template-columns:1fr">${ent(t)}</div>`).join('')}</div></details></div>`;
      }
    };
    let timer = null;
    input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { history.replaceState(null, '', '#search/' + enc(input.value)); draw(); }, 40); });
    input.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowDown') { sel = Math.min(flat.length - 1, sel + 1); preview(); ev.preventDefault(); const x = $(`#sres [data-ri="${sel}"]`); if (x) x.scrollIntoView({ block: 'nearest' }); }
      else if (ev.key === 'ArrowUp') { sel = Math.max(0, sel - 1); preview(); ev.preventDefault(); const x = $(`#sres [data-ri="${sel}"]`); if (x) x.scrollIntoView({ block: 'nearest' }); }
      else if (ev.key === 'Enter') { clearTimeout(timer); if (input.value.trim() !== decodeURIComponent((location.hash.split('/')[1] || ''))) draw(); if (flat[sel]) location.hash = openHref(flat[sel]); }
    });
    $('#sres').addEventListener('mousemove', ev => { const r = ev.target.closest('[data-ri]'); if (r && +r.dataset.ri !== sel) { sel = +r.dataset.ri; preview(); } });
    $('#sres').addEventListener('click', ev => { const b = ev.target.closest('[data-act="stype"]'); if (b) { state.searchType = b.dataset.k; draw(); } });
    draw();
    input.focus(); input.setSelectionRange(input.value.length, input.value.length);
  }

  function notFound(kind, name) {
    page(kind === 'area' ? 'area' : 'table', `<nav class="kg-crumbs"><a href="#areas">Areas</a></nav>`,
      `<div class="kg-doc"><div class="kg-empty">${bigIcon('empty')}<h3>No ${kind} called “${esc(name)}”</h3><p>It may have been renamed or removed since this file was built. Search for it instead.</p><div class="kg-toolbar"><a class="kg-btn kg-btn--primary kg-btn--sm" href="#search/${enc(String(name).split('.').pop())}">${icon('search', 'kg-icon--sm')}Search</a><a class="kg-btn kg-btn--sm" href="#areas">Areas</a></div></div></div>`, null);
  }

  /* ---------- router ---------- */
  function route() {
    const parts = location.hash.replace(/^#/, '').split('/').map(x => { try { return decodeURIComponent(x); } catch (_) { return x; } });
    const [v, a, b] = parts;
    if (v !== 'area') state.sel = [];
    try {
      if (v === 'area' && a) showArea(a);
      else if (v === 'table' && a) renderTable(a);
      else if (v === 'col' && a && b != null) renderColumn(a, parts.slice(2).join('/'));
      else if (v === 'path') renderPath(a || null, b || null);
      else if (v === 'search') renderSearch(parts.slice(1).join('/'));
      else showAreas();
    } catch (err) {
      console.error(err);
      page('error', '<h1>Something went wrong</h1>', `<div class="kg-doc"><div style="padding:24px"><div class="kg-error">${icon('warn')}<div><b>This view could not be drawn.</b><br><span class="kg-muted">${esc(err && err.message || err)}. Go back, or open the <a class="kg-link-text" href="#areas">area map</a>.</span></div></div></div></div>`, null);
    }
  }
  const rerender = () => { const y = $('.kg-doc') ? $('.kg-doc').scrollTop : 0; route(); const d = $('.kg-doc'); if (d) d.scrollTop = y; };

  /* ---------- events ---------- */
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-act]'); if (!el) return;
    const act = el.dataset.act;
    if (act === 'theme') { const nt = theme() === 'dark' ? 'light' : 'dark'; document.documentElement.setAttribute('data-theme', nt); try { localStorage.setItem('kg-theme', nt); } catch (_) { /* blocked */ } rerender(); }
    else if (act === 'settings') { state.settings = !state.settings; rerender(); }
    else if (act === 'show-all') { const id = (location.hash.split('/')[1] || ''); const k = decodeURIComponent(id); state.showAll[k] = !state.showAll[k]; rerender(); }
    else if (act === 'add-nb') {
      const id = decodeURIComponent(location.hash.split('/')[1] || ''), t = +el.dataset.t, list = state.added[id] || (state.added[id] = []);
      const at = list.indexOf(t);
      if (at >= 0) list.splice(at, 1);
      else if ($$('#cv .kg-tcard').length >= MAX_CANVAS_NODES) { toast(`The canvas already shows ${MAX_CANVAS_NODES} tables. Remove one first.`); return; }
      else list.push(t);
      rerender();
    }
    else if (act === 'expand') { const k = el.dataset.k; state.expanded.has(k) ? state.expanded.delete(k) : state.expanded.add(k); rerender(); }
    else if (act === 'route') { state.pathRoute = +el.dataset.i; rerender(); }
    else if (act === 'hops') { state.maxHops = +el.dataset.n; rerender(); }
    else if (act === 'weak-on') { state.weak = true; rerender(); }
    else if (act === 'swap') { const parts = location.hash.split('/'); location.hash = '#path/' + (parts[2] || '') + '/' + (parts[1] || ''); }
    else if (act === 'copy-sql') { const s = $('#sql'); if (s) copyText(s.textContent, 'SQL'); }
    else if (act === 'copy-mermaid') { if (KG.mermaid) copyText(KG.mermaid(), 'Mermaid'); }
    else if (act === 'pcol') {
      if (ev.target.closest('a')) return;
      const parts = location.hash.split('/'), t = resolveTable(decodeURIComponent(parts[1] || '')); if (t < 0) return;
      state.panelCol = { t, c: +el.dataset.c };
      $$('.kg-cols-grid tbody tr').forEach(r => r.toggleAttribute('aria-selected', +r.dataset.c === state.panelCol.c));
      setPanel(panelColumn(t, state.panelCol.c));
    }
  });
  document.addEventListener('change', ev => {
    const el = ev.target.closest('[data-act]'); if (!el) return;
    if (el.dataset.act === 'weak') { state.weak = el.checked; rerender(); }
    else if (el.dataset.act === 'hubs') { state.hubsAsEdges = el.checked; rerender(); }
    else if (el.dataset.act === 'mirrors') { state.mirrors = el.checked; rerender(); }
  });
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && ev.target.matches && ev.target.matches('[data-act="route"],[data-act="expand"]')) { ev.target.click(); return; }
    if (ev.key !== '/' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.target.closest && ev.target.closest('input, textarea, [contenteditable]')) return;
    ev.preventDefault();
    const s = $('[data-search]'); if (s && s.offsetParent) s.focus(); else location.hash = '#search';
  });
  window.addEventListener('hashchange', route);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (!document.documentElement.getAttribute('data-theme')) rerender(); });

  /* ---------- API for tests and agents ---------- */
  KG.findPaths = (a, b, maxHops = 4, minConf = 0.6, k = 3, withMirrors = false) => findPaths(resolveTable(a), resolveTable(b), maxHops, minConf, k, 300000, withMirrors).map(p => ({
    tables: p.tables.map(tid), confidence: p.confidence, score: p.score, hops: p.hops, sql: p.sql,
    steps: p.steps.map(s => ({ from: tid(s.near), to: tid(s.far), from_columns: s.nc.map(c => cname(s.near, c)), to_columns: s.fc.map(c => cname(s.far, c)), cardinality: CARDS[s.card], confidence: E[s.ei][4], warnings: s.warnings, fan_out: s.card === 3 }))
  }));
  KG.search = q => search(q).map(r => r.e.label);
  KG.searchDetail = q => search(q).slice(0, 20).map(r => ({ kind: r.e.k, label: r.e.label, reason: r.reason }));
  KG.relationSummary = (name, minConf = 0.6) => { const t = resolveTable(name), s = relSummary(t, minConf); const g = x => ({ column: x.column, area: x.area >= 0 ? AREAS[x.area].id : null, count: x.count, collapsed: x.collapsed, tables: x.tables.map(tid) }); return { out: s.out.map(g), in: s.in.map(g), hub_labels: s.hubs.map(h => ({ to: tid(h.to), columns: h.columns, count: h.count })) }; };
  KG.probeArea = (id, n) => renderArea(id, n, true); // zoom the Area view would need with n cards (diagnostics)
  KG.probeMap = n => renderAreas(n, true);
  KG.tables = N; KG.edges = E.length; KG.data = D;
  KG.timings = {};

  document.fonts && document.fonts.ready ? await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 300))]) : null;
  const t0 = performance.now();
  route();
  KG.timings.firstRender = performance.now() - t0;
  KG.ready = true; document.body.dataset.ready = '1';
  // build the search index after the first paint so the page shows at once; search builds it on demand if needed
  setTimeout(() => { buildIndex(); KG.timings.index = SX.ms; }, 0);
})();
