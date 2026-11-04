/* kg-diagram.js - keygraph's diagram renderer: HTML boxes + one SVG layer for edges, laid out by dagre.
 *
 * Why not cytoscape: the views draw at most ~40 boxes, and the boxes are tables that list their key columns,
 * with relations attached to the exact column rows. That is HTML (real text, CSS tokens, selectable, accessible),
 * not canvas dots. dagre (@dagrejs/dagre 1.1.4, MIT, 94 KB) does the layered layout; this file does sizing,
 * column-anchored edge routing, ER end markers, pan and zoom. Requires window.dagre.
 *
 *   const d = KGDiagram.mount(container);          // container gets .kg-viewport > .kg-world (+ svg.kg-edges)
 *   d.er({ tables, edges }, { rankdir: 'LR' });     // Area view (ER)
 *   d.map({ areas, links });                        // Area map
 *   d.fit(); d.zoomBy(1.2); d.destroy();
 *
 * ER table: { id, schema (shown muted before the name), name, color, rows: [{ name, type, key: 'PK'|'FK'|'UK'|'' , hub?: 'users' }], more: 12,
 *             hubs: ['users'], neighbour: false, selected: false }
 * ER edge:  { from: { t, col }, to: { t, col }, kind: 'declared'|'inferred'|'polymorphic', conf: 0..1, reason }
 *           "from" is the referencing (FK, many) side, "to" the referenced (PK, one) side.
 * Map area: { id, name, sub?: 'muted second line', count, top: ['orders', ...], color, other?: true }      Map link: { a, b, count }
 */
(function (root) {
  const ROW = 24, HEAD = 38, MORE = 26, CARD_W = 196, NS = 'http://www.w3.org/2000/svg';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const sv = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const opacity = c => c >= 0.95 ? 1 : c >= 0.85 ? 0.8 : c >= 0.7 ? 0.6 : 0.4;

  function mount(host) {
    // the viewport is a child of host, so overlays already inside host (legend, zoom, strips) are kept
    const container = el('div', 'kg-viewport'); host.prepend(container);
    const world = el('div', 'kg-world'); const svg = sv('svg', { class: 'kg-edges' });
    world.appendChild(svg); container.appendChild(world);
    let tx = 0, ty = 0, k = 1, W = 0, H = 0;
    const apply = () => { world.style.transform = `translate(${tx}px, ${ty}px) scale(${k})`; container.dataset.zoom = k.toFixed(2); };
    const api = { container, world, svg, onSelect: null };

    // pan and zoom
    let drag = null;
    container.addEventListener('pointerdown', e => { if (e.target.closest('.kg-node, button, a')) return; drag = { x: e.clientX - tx, y: e.clientY - ty }; container.setPointerCapture(e.pointerId); container.classList.add('is-panning'); });
    container.addEventListener('pointermove', e => { if (!drag) return; tx = e.clientX - drag.x; ty = e.clientY - drag.y; apply(); });
    container.addEventListener('pointerup', () => { drag = null; container.classList.remove('is-panning'); });
    container.addEventListener('wheel', e => { e.preventDefault(); const r = container.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
    function zoomAt(f, cx, cy) { const nk = Math.min(2, Math.max(0.3, k * f)); tx = cx - (cx - tx) * nk / k; ty = cy - (cy - ty) * nk / k; k = nk; apply(); }
    api.zoomBy = f => { const r = container.getBoundingClientRect(); zoomAt(f, r.width / 2, r.height / 2); };
    /* Fit the drawing, but never below minK: below ~0.8 the mono labels stop being readable, so a large
       drawing is shown at minK centred on the focus box (or the middle) and the reader pans. */
    api.fit = ({ pad = 48, maxK = 1, minK = 0.8, focus = null, offsetY = 0, inset = null, anchor = 'focus' } = {}) => {
      // inset: { top, right, bottom, left } in px reserved for overlays (strips, callouts), overriding pad per side;
      // anchor 'start' shows a drawing that cannot fit from its top-left corner instead of centring on `focus`
      const r = container.getBoundingClientRect(); if (!W || !H) return;
      const p = Object.assign({ top: pad, right: pad, bottom: pad, left: pad }, inset || {});
      const aw = Math.max(40, r.width - p.left - p.right), ah = Math.max(40, r.height - p.top - p.bottom);
      k = Math.min(maxK, aw / W, ah / H);
      if (k >= minK) { tx = p.left + (aw - W * k) / 2; ty = p.top + (ah - H * k) / 2 + offsetY; return apply(); }
      k = minK;
      if (anchor === 'start') { tx = p.left; ty = p.top + offsetY; return apply(); } // too big: show the top-left, below any overlay
      const f = focus && world.querySelector(`.kg-node[data-id="${CSS.escape(focus)}"]`);
      const cx = f ? f.offsetLeft + f.offsetWidth / 2 : W / 2, cy = f ? f.offsetTop + f.offsetHeight / 2 : H / 2;
      tx = p.left + aw / 2 - cx * k; ty = p.top + ah / 2 - cy * k + offsetY;
      tx = Math.min(p.left, Math.max(r.width - p.right - W * k, tx)); ty = Math.min(p.top, Math.max(r.height - p.bottom - H * k, ty));
      apply();
    };
    api.destroy = () => container.remove();

    /* rankdir 'auto' lays the graph out both ways (LR and TB) and keeps the one that fits the viewport at the
       larger zoom, so a wide canvas gets a wide drawing and a tall one a tall drawing. */
    function layout(nodes, edges, opts) {
      const run = dir => {
        const g = new dagre.graphlib.Graph({ multigraph: true });
        g.setGraph({ rankdir: dir, nodesep: opts.nodesep || 28, ranksep: opts.ranksep || 96, marginx: 8, marginy: 8, ranker: opts.ranker || 'network-simplex' });
        g.setDefaultEdgeLabel(() => ({}));
        nodes.forEach(n => g.setNode(n.id, { width: n.w, height: n.h }));
        edges.forEach((e, i) => { if (g.hasNode(e.s) && g.hasNode(e.t) && e.s !== e.t) g.setEdge(e.s, e.t, { weight: e.weight || 1, minlen: e.minlen || 1 }, 'e' + i); });
        dagre.layout(g); return g;
      };
      const r = container.getBoundingClientRect();
      const score = g => Math.min((r.width - 96) / g.graph().width, (r.height - 96) / g.graph().height);
      let g = run(opts.rankdir === 'auto' ? 'LR' : (opts.rankdir || 'LR'));
      if (opts.rankdir === 'auto') { const t = run('TB'); if (score(t) > score(g) * 1.05) g = t; }
      nodes.forEach(n => { const p = g.node(n.id); n.x = p.x - n.w / 2; n.y = p.y - n.h / 2; });
      W = g.graph().width; H = g.graph().height; api.graph = g; api.rankdir = g.graph().rankdir;
      pack(nodes, edges, r);
      world.style.width = W + 'px'; world.style.height = H + 'px';
      svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    }

    /* dagre stacks disconnected components in one long column. Shelf-pack them instead, to the viewport's
       aspect, so an area of loosely connected tables fills the canvas rather than one strip. */
    function pack(nodes, edges, r) {
      const parent = {}; const find = x => parent[x] === x ? x : (parent[x] = find(parent[x]));
      nodes.forEach(n => { parent[n.id] = n.id; });
      edges.forEach(e => { if (parent[e.s] != null && parent[e.t] != null) parent[find(e.s)] = find(e.t); });
      const comps = {}; nodes.forEach(n => (comps[find(n.id)] = comps[find(n.id)] || []).push(n));
      const list = Object.values(comps); if (list.length < 2) return;
      const GAP = 28, boxes = list.map(ns => { const x0 = Math.min(...ns.map(n => n.x)), y0 = Math.min(...ns.map(n => n.y));
        return { ns, x0, y0, w: Math.max(...ns.map(n => n.x + n.w)) - x0, h: Math.max(...ns.map(n => n.y + n.h)) - y0 }; });
      boxes.sort((a, b) => b.ns.length - a.ns.length || b.h - a.h);
      const area = boxes.reduce((s, b) => s + (b.w + GAP) * (b.h + GAP), 0), aspect = Math.max(0.6, (r.width || 900) / (r.height || 700));
      const rowW = Math.max(Math.max(...boxes.map(b => b.w)), Math.sqrt(area * aspect));
      let x = 8, y = 8, rowH = 0;
      boxes.forEach(b => {
        if (x > 8 && x + b.w > rowW + 8) { x = 8; y += rowH + GAP; rowH = 0; }
        b.ns.forEach(n => { n.x += x - b.x0; n.y += y - b.y0; });
        x += b.w + GAP; rowH = Math.max(rowH, b.h);
      });
      W = Math.max(...nodes.map(n => n.x + n.w)) + 8; H = Math.max(...nodes.map(n => n.y + n.h)) + 8;
      world.style.width = W + 'px'; world.style.height = H + 'px';
      svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    }

    /* ---------- ER (Area view) ---------- */
    api.er = (data, opts = {}) => {
      world.querySelectorAll('.kg-node').forEach(n => n.remove()); svg.innerHTML = '';
      const byId = {};
      const nodes = data.tables.map(t => {
        const h = HEAD + t.rows.length * ROW + (t.more ? MORE : 0) + 2;
        return byId[t.id] = { ...t, w: CARD_W, h };
      });
      // rank direction: referenced (parent) tables first, so edges run parent -> child
      layout(nodes, data.edges.map(e => ({ s: e.to.t, t: e.from.t, weight: e.kind === 'declared' ? 3 : 1 })), opts);
      nodes.forEach(n => world.appendChild(card(n)));
      const rowY = (n, col) => { const i = n.rows.findIndex(r => r.name === col); const top = n.y + HEAD; return i < 0 ? n.y + HEAD / 2 : top + i * ROW + ROW / 2; };
      data.edges.forEach((e, i) => {
        const a = byId[e.from.t], b = byId[e.to.t]; if (!a || !b) return;
        const ay = rowY(a, e.from.col), by = rowY(b, e.to.col);
        const aRight = a.x + a.w / 2 < b.x + b.w / 2;          // child left of parent?
        const ax = aRight ? a.x + a.w : a.x, bx = aRight ? b.x : b.x + b.w;
        const dir = aRight ? 1 : -1, dx = Math.max(40, Math.abs(bx - ax) / 2);
        const d = `M${ax},${ay} C${ax + dir * dx},${ay} ${bx - dir * dx},${by} ${bx},${by}`;
        const g = sv('g', { class: `kg-edge kg-edge--${e.kind}`, 'data-i': i, 'data-from': e.from.t + '.' + e.from.col, 'data-to': e.to.t + '.' + e.to.col, style: `--kg-edge-opacity:${opacity(e.conf)}` });
        g.appendChild(sv('path', { class: 'kg-edge-hit', d }));
        g.appendChild(sv('path', { class: 'kg-edge-line', d }));
        // many end (crow's foot) at the FK row, one end (bar) at the PK row
        g.appendChild(sv('path', { class: 'kg-edge-end', d: `M${ax + dir * 12},${ay} L${ax},${ay - 5} M${ax + dir * 12},${ay} L${ax},${ay} M${ax + dir * 12},${ay} L${ax},${ay + 5}` }));
        g.appendChild(sv('path', { class: 'kg-edge-end', d: `M${bx - dir * 7},${by - 5} L${bx - dir * 7},${by + 5}` }));
        if (e.kind !== 'declared' && opts.confLabels !== false) {
          const mx = (ax + bx) / 2, my = (ay + by) / 2, t = e.conf.toFixed(2);
          g.appendChild(sv('rect', { class: 'kg-edge-pill', x: mx - 17, y: my - 9, width: 34, height: 18, rx: 9 }));
          const tt = sv('text', { class: 'kg-edge-pilltext', x: mx, y: my + 4, 'text-anchor': 'middle' }); tt.textContent = t; g.appendChild(tt);
        }
        g.addEventListener('mouseenter', () => highlight(g, true)); g.addEventListener('mouseleave', () => highlight(g, false));
        svg.appendChild(g);
      });
      return api;
    };
    function highlight(g, on) {
      g.classList.toggle('is-hover', on);
      [g.dataset.from, g.dataset.to].forEach(k => world.querySelectorAll(`[data-col="${CSS.escape(k)}"]`).forEach(r => r.classList.toggle('is-linked', on)));
    }
    function card(n, opts = {}) {
      const c = el('div', 'kg-node kg-tcard' + (n.neighbour ? ' kg-tcard--neighbour' : '') + (n.focus ? ' kg-tcard--focus' : ''));
      c.style.cssText = `left:${n.x}px;top:${n.y}px;width:${n.w}px;--kg-glyph-color:${n.color}`;
      if (n.selected) c.setAttribute('aria-selected', 'true');
      c.dataset.id = n.id; c.tabIndex = 0;
      const hubs = n.hubs && n.hubs.length ? `<span class="kg-tcard-hubs">${n.hubs.map(h => `<span class="kg-hub" title="hub relation to ${esc(h)}, drawn as a label">→ ${esc(h)}</span>`).join('')}</span>` : '';
      const schema = n.schema ? `<span class="kg-tcard-schema">${esc(n.schema)}</span>` : '';
      c.title = (n.schema ? n.schema + '.' : '') + n.name;
      c.innerHTML = `<div class="kg-tcard-head"><span class="kg-glyph kg-glyph--table"></span>${schema}<span class="kg-entity-name">${esc(n.name)}</span>${n.neighbour ? `<span class="kg-tcard-area">${esc(n.area || '')}</span>` : hubs}</div>`
        + n.rows.map(r => `<div class="kg-tcard-row" data-col="${esc(n.id + '.' + r.name)}"><span>${r.key ? `<span class="kg-key kg-key--${r.key.toLowerCase()}">${r.key}</span>` : ''}</span><span class="kg-entity-name" title="${esc(r.name + ' ' + r.type)}">${esc(r.name)}</span>${opts.types ? `<span class="t">${esc(r.type)}</span>` : '<span></span>'}</div>`).join('')
        + (n.more ? `<div class="kg-tcard-more">+${n.more} column${n.more === 1 ? '' : 's'}</div>` : '');
      c.addEventListener('click', ev => api.onSelect && api.onSelect(n, ev));
      return c;
    }

    /* ---------- Area map ----------
       Radial, not layered: the busiest area sits in the middle, the areas it connects to on an inner ring (ordered
       so linked areas sit side by side), everything else on an outer ring next to its strongest inner neighbour.
       Lines are straight and clipped to box edges; their width grows with the relation count. The ellipse is
       stretched to the viewport's aspect, so the map fills a laptop canvas without scrolling. */
    api.map = (data, opts = {}) => {
      world.querySelectorAll('.kg-node').forEach(n => n.remove()); svg.innerHTML = '';
      const byId = {}, w = {}, deg = {};
      const nodes = data.areas.map(a => { const bw = Math.round(176 + Math.min(56, Math.sqrt(a.count) * 9)); return byId[a.id] = { ...a, w: bw, h: 44 + 20 * Math.min(3, a.top.length) + 24 + (a.sub ? 16 : 0) + (a.name.length > bw / 9 ? 18 : 0) }; });
      const key = (a, b) => a < b ? a + '|' + b : b + '|' + a;
      data.links.forEach(l => { w[key(l.a, l.b)] = l.count; deg[l.a] = (deg[l.a] || 0) + l.count; deg[l.b] = (deg[l.b] || 0) + l.count; });
      const wt = (a, b) => w[key(a, b)] || 0;
      const order = nodes.slice().sort((a, b) => (deg[b.id] || 0) - (deg[a.id] || 0) || a.id.localeCompare(b.id));
      const center = order.find(n => !n.other) || order[0]; // the grouped "Other areas" box never takes the middle
      let ring1 = order.filter(n => n !== center && wt(n.id, center.id) > 0 && !n.other);
      const rest = order.filter(n => n !== center && !ring1.includes(n));
      // greedy ordering of ring 1: start from the strongest, then always the area most linked to the previous one
      const seq = []; let pool = ring1.slice();
      while (pool.length) { const prev = seq[seq.length - 1]; pool.sort((a, b) => (prev ? wt(b.id, prev.id) - wt(a.id, prev.id) : 0) || wt(b.id, center.id) - wt(a.id, center.id)); seq.push(pool.shift()); }
      ring1 = seq;
      const r = container.getBoundingClientRect(), aspect = Math.max(0.8, Math.min(1.8, (r.width || 900) / (r.height || 700)));
      const maxW = Math.max(...nodes.map(n => n.w)), maxH = Math.max(...nodes.map(n => n.h));
      const grow = Math.max(1, ring1.length / 6);
      const ry1 = (maxH + 48) * grow, rx1 = Math.max(maxW + 40, ry1 * aspect) * grow, ry2 = ry1 + maxH + 48, rx2 = rx1 + maxW + 36;
      const place = (n, ang, ring) => { n.cx = Math.cos(ang) * (ring === 1 ? rx1 : rx2); n.cy = Math.sin(ang) * (ring === 1 ? ry1 : ry2); n.ang = ang; };
      center.cx = 0; center.cy = 0;
      ring1.forEach((n, i) => place(n, -Math.PI / 2 + (i / ring1.length) * 2 * Math.PI, 1));
      // outer ring: near the strongest inner neighbour, nudged apart
      const taken = [];
      rest.forEach(n => {
        const anchor = ring1.slice().sort((a, b) => wt(b.id, n.id) - wt(a.id, n.id))[0];
        let ang = anchor && wt(anchor.id, n.id) ? anchor.ang : Math.PI / 2;
        const step = Math.PI / Math.max(6, rest.length + 2);
        for (let k = 0; taken.some(t => Math.abs(((t - ang + 3 * Math.PI) % (2 * Math.PI)) - Math.PI) < step) && k < 40; k++) ang += (k % 2 ? -1 : 1) * step * (k + 1);
        taken.push(ang); place(n, ang, 2);
      });
      // settle with a small deterministic force pass: links pull (more relations, shorter line), boxes repel,
      // rectangles never overlap (28px gap), and the picture is stretched to the viewport's aspect.
      const L = data.links.filter(l => byId[l.a] && byId[l.b]);
      for (let it = 0; it < 400; it++) {
        const cool = 1 - it / 400;
        nodes.forEach(n => { n.fx = -n.cx * 0.03 / aspect; n.fy = -n.cy * 0.03 * aspect; });
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j]; let dx = (b.cx - a.cx) / aspect, dy = b.cy - a.cy; const d2 = Math.max(400, dx * dx + dy * dy), f = 4e5 / d2, d = Math.sqrt(d2);
          a.fx -= f * dx / d; a.fy -= f * dy / d; b.fx += f * dx / d; b.fy += f * dy / d;
        }
        L.forEach(l => { const a = byId[l.a], b = byId[l.b], dx = b.cx - a.cx, dy = b.cy - a.cy, d = Math.hypot(dx, dy) || 1, rest = maxW * 1.1 - Math.min(60, l.count * 4), f = (d - rest) * 0.02 * Math.log2(1 + l.count);
          a.fx += f * dx / d; a.fy += f * dy / d; b.fx -= f * dx / d; b.fy -= f * dy / d; });
        nodes.forEach(n => { if (n === center) return; n.cx += Math.max(-30, Math.min(30, n.fx)) * cool; n.cy += Math.max(-30, Math.min(30, n.fy)) * cool; });
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j], ox = (a.w + b.w) / 2 + 64 - Math.abs(a.cx - b.cx), oy = (a.h + b.h) / 2 + 48 - Math.abs(a.cy - b.cy);
          if (ox > 0 && oy > 0) {
            const sx = Math.sign(b.cx - a.cx) || 1, sy = Math.sign(b.cy - a.cy) || 1, ka = a === center ? 0 : b === center ? 1 : 0.5, kb = 1 - ka;
            if (ox < oy) { a.cx -= sx * ox * ka; b.cx += sx * ox * kb; } else { a.cy -= sy * oy * ka; b.cy += sy * oy * kb; }
          }
        }
      }
      const minX = Math.min(...nodes.map(n => n.cx - n.w / 2)) - 8, minY = Math.min(...nodes.map(n => n.cy - n.h / 2)) - 8;
      nodes.forEach(n => { n.x = n.cx - n.w / 2 - minX; n.y = n.cy - n.h / 2 - minY; });
      W = Math.max(...nodes.map(n => n.x + n.w)) + 8; H = Math.max(...nodes.map(n => n.y + n.h)) + 8;
      world.style.width = W + 'px'; world.style.height = H + 'px'; svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      const clip = (n, tx, ty) => { const cx = n.x + n.w / 2, cy = n.y + n.h / 2, dx = tx - cx, dy = ty - cy; const t = Math.min(Math.abs((n.w / 2 + 4) / (dx || 1e-9)), Math.abs((n.h / 2 + 4) / (dy || 1e-9))); return { x: cx + dx * t, y: cy + dy * t }; };
      const maxC = Math.max(...data.links.map(l => l.count));
      data.links.slice().sort((a, b) => a.count - b.count).forEach(l => {
        const a = byId[l.a], b = byId[l.b]; if (!a || !b) return;
        const p = clip(a, b.x + b.w / 2, b.y + b.h / 2), q = clip(b, a.x + a.w / 2, a.y + a.h / 2);
        const grp = sv('g', { class: 'kg-link', style: `--kg-link-w:${(1 + 5 * Math.sqrt(l.count / maxC)).toFixed(1)}px` });
        grp.appendChild(sv('path', { class: 'kg-link-line', d: `M${p.x},${p.y} L${q.x},${q.y}` }));
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        grp.appendChild(sv('rect', { class: 'kg-edge-pill', x: mx - 15, y: my - 9, width: 30, height: 18, rx: 9 }));
        const tt = sv('text', { class: 'kg-edge-pilltext', x: mx, y: my + 4, 'text-anchor': 'middle' }); tt.textContent = l.count; grp.appendChild(tt);
        svg.appendChild(grp);
      });
      nodes.forEach(n => {
        const c = el('div', 'kg-node kg-area' + (n.other ? ' kg-area--other' : '') + (n === center ? ' kg-area--center' : ''));
        c.style.cssText = `left:${n.x}px;top:${n.y}px;width:${n.w}px;min-height:${n.h}px;--kg-glyph-color:${n.color}`;
        c.tabIndex = 0; c.dataset.id = n.id;
        const dots = Array.from({ length: Math.min(n.count, 24) }, () => '<i></i>').join('');
        c.title = n.name + (n.sub ? ' · ' + n.sub : '');
        c.innerHTML = `<div class="kg-area-head"><span class="kg-area-name">${esc(n.name)}</span><span class="kg-area-count">${n.count} tables</span></div>${n.sub ? `<div class="kg-area-sub">${esc(n.sub)}</div>` : ''}`
          + `<div class="kg-area-dots" aria-hidden="true">${dots}${n.count > 24 ? '<b>+</b>' : ''}</div>`
          + `<ul class="kg-area-top">${n.top.slice(0, 3).map(t => `<li>${esc(t)}</li>`).join('')}</ul>`;
        c.addEventListener('click', ev => api.onSelect && api.onSelect(n, ev));
        world.appendChild(c);
      });
      return api;
    };
    /* smooth curve through dagre's routed points (Catmull-Rom converted to cubic Bezier) */
    function curve(p) {
      if (p.length < 3) return `M${p[0].x},${p[0].y} L${p[p.length - 1].x},${p[p.length - 1].y}`;
      let d = `M${p[0].x},${p[0].y}`;
      for (let i = 0; i < p.length - 1; i++) {
        const p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
        d += ` C${p1.x + (p2.x - p0.x) / 6},${p1.y + (p2.y - p0.y) / 6} ${p2.x - (p3.x - p1.x) / 6},${p2.y - (p3.y - p1.y) / 6} ${p2.x},${p2.y}`;
      }
      return d;
    }
    function midpoint(d) { const p = sv('path', { d }); svg.appendChild(p); const L = p.getTotalLength(); const m = p.getPointAtLength(L / 2); p.remove(); return m; }

    apply();
    return api;
  }

  root.KGDiagram = { mount, ROW, HEAD, CARD_W };
})(typeof window !== 'undefined' ? window : globalThis);
