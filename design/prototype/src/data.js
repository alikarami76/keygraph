/* Fake but realistic data for the prototype: a generic online shop with four schemas.
   Shapes mirror what the generator will embed; they are illustrative, not a contract. */
window.KGDATA = (function () {
  const S = { shop: 0, billing: 1, logistics: 2, audit: 3 };
  // area colour comes from the schema palette index of the area (areas are ordered by size)
  const areas = [
    { id: 'orders', name: 'Orders', count: 9, top: ['orders', 'order_items', 'returns'], ci: 0 },
    { id: 'catalog', name: 'Catalog', count: 11, top: ['products', 'product_variants', 'categories'], ci: 1 },
    { id: 'customers', name: 'Customers', count: 5, top: ['users', 'addresses', 'user_sessions'], ci: 2 },
    { id: 'sellers', name: 'Sellers', count: 6, top: ['sellers', 'seller_warehouses', 'seller_payouts'], ci: 3 },
    { id: 'payments', name: 'Payments', count: 7, top: ['payments', 'payment_refunds', 'invoices'], ci: 4 },
    { id: 'shipping', name: 'Shipping', count: 6, top: ['shipments', 'shipment_items', 'carriers'], ci: 5 },
    { id: 'promotions', name: 'Promotions', count: 5, top: ['vouchers', 'voucher_redemptions', 'campaigns'], ci: 6 },
    { id: 'reviews', name: 'Reviews', count: 3, top: ['product_reviews', 'review_votes', 'review_reports'], ci: 7 },
    { id: 'audit', name: 'Audit log', count: 4, top: ['audit_log', 'admin_actions', 'login_events'], ci: 8 },
    { id: 'other', name: 'Other areas', count: 7, top: ['feature_flags', 'cms_pages', '+ 3 more areas'], ci: 9, other: true }
  ];
  const links = [
    ['orders', 'catalog', 14], ['orders', 'customers', 9], ['orders', 'payments', 11], ['orders', 'shipping', 8],
    ['orders', 'promotions', 5], ['catalog', 'sellers', 12], ['catalog', 'reviews', 6], ['catalog', 'promotions', 4],
    ['sellers', 'payments', 3], ['customers', 'reviews', 3], ['shipping', 'sellers', 4], ['payments', 'customers', 2],
    ['audit', 'customers', 2], ['other', 'catalog', 1]
  ].map(([a, b, count]) => ({ a, b, count }));

  // Orders area as an ER diagram. Rows list PK and FK columns only; the rest fold into "+N columns".
  const T = (id, name, rows, more, hubs, extra) => ({ id, schema: 'shop', name, rows, more, hubs: hubs || [], area: 'Orders', ...(extra || {}) });
  const orderTables = [
    T('orders', 'orders', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'cart_id', type: 'bigint', key: 'FK' }, { name: 'voucher_id', type: 'bigint', key: 'FK' }, { name: 'shipping_address_id', type: 'bigint', key: 'FK' }], 14, ['users'], { focus: true }),
    T('order_items', 'order_items', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'order_id', type: 'bigint', key: 'FK' }, { name: 'product_variant_id', type: 'bigint', key: 'FK' }], 9, ['sellers']),
    T('order_status_history', 'order_status_history', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'order_id', type: 'bigint', key: 'FK' }], 5, ['users']),
    T('order_notes', 'order_notes', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'order_id', type: 'bigint', key: 'FK' }], 4, ['users']),
    T('carts', 'carts', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'session_id', type: 'char(32)', key: 'FK' }], 6, ['users']),
    T('cart_items', 'cart_items', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'cart_id', type: 'bigint', key: 'FK' }, { name: 'product_variant_id', type: 'bigint', key: 'FK' }], 4),
    T('returns', 'returns', [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'order_item_id', type: 'bigint', key: 'FK' }, { name: 'refund_id', type: 'bigint', key: 'FK' }], 8, ['users']),
    T('order_tags', 'order_tags', [{ name: 'order_id', type: 'bigint', key: 'PK' }, { name: 'tag', type: 'varchar(40)', key: 'PK' }], 1),
    // neighbour pulled in from the strip: drawn dashed, with its area named
    { id: 'product_variants', schema: 'shop', name: 'product_variants', area: 'Catalog', neighbour: true, rows: [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'product_id', type: 'bigint', key: 'FK' }], more: 11, hubs: [] }
  ];
  const E = (ft, fc, tt, tc, kind, conf, reason) => ({ from: { t: ft, col: fc }, to: { t: tt, col: tc }, kind, conf, reason });
  const orderEdges = [
    E('order_items', 'order_id', 'orders', 'id', 'declared', 1, 'Declared foreign key fk_order_items_order'),
    E('order_status_history', 'order_id', 'orders', 'id', 'declared', 1, 'Declared foreign key'),
    E('order_notes', 'order_id', 'orders', 'id', 'inferred', 0.81, 'Name matches orders; no index on order_id'),
    E('orders', 'cart_id', 'carts', 'id', 'inferred', 0.92, 'Name matches carts, types match, indexed'),
    E('cart_items', 'cart_id', 'carts', 'id', 'declared', 1, 'Declared foreign key'),
    E('returns', 'order_item_id', 'order_items', 'id', 'inferred', 0.9, 'Name matches order_items, indexed'),
    E('order_tags', 'order_id', 'orders', 'id', 'inferred', 0.88, 'Leading column of the primary key'),
    E('order_items', 'product_variant_id', 'product_variants', 'id', 'inferred', 0.95, 'Name matches product_variants, indexed'),
    E('cart_items', 'product_variant_id', 'product_variants', 'id', 'inferred', 0.95, 'Name matches product_variants, indexed')
  ];
  const neighbours = [
    { t: 'product_variants', area: 'Catalog', n: 2, added: true }, { t: 'payments', area: 'Payments', n: 3 },
    { t: 'shipments', area: 'Shipping', n: 3 }, { t: 'addresses', area: 'Customers', n: 1 }, { t: 'vouchers', area: 'Promotions', n: 1 },
    { t: 'payment_refunds', area: 'Payments', n: 1 }, { t: 'invoices', area: 'Payments', n: 1 }, { t: 'user_sessions', area: 'Customers', n: 1 }
  ];

  // Path: orders -> categories
  const path = {
    from: 'shop.orders', to: 'shop.categories', conf: 0.88, hops: 4,
    tables: [
      { name: 'orders', area: 'Orders', ci: 0, rows: [{ name: 'id', type: 'bigint', key: 'PK' }] },
      { name: 'order_items', area: 'Orders', ci: 0, rows: [{ name: 'order_id', type: 'bigint', key: 'FK' }, { name: 'product_variant_id', type: 'bigint', key: 'FK' }] },
      { name: 'product_variants', area: 'Catalog', ci: 1, rows: [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'product_id', type: 'bigint', key: 'FK' }] },
      { name: 'products', area: 'Catalog', ci: 1, rows: [{ name: 'id', type: 'bigint', key: 'PK' }, { name: 'category_id', type: 'int', key: 'FK' }] },
      { name: 'categories', area: 'Catalog', ci: 1, rows: [{ name: 'id', type: 'int', key: 'PK' }] }
    ],
    steps: [
      { on: ['orders.id', 'order_items.order_id'], kind: 'declared', conf: 1, why: 'Declared foreign key', warns: ['fan-out'], card: 'one → many' },
      { on: ['order_items.product_variant_id', 'product_variants.id'], kind: 'inferred', conf: 0.95, why: 'Name matches, types match, indexed', warns: [], card: 'many → one' },
      { on: ['product_variants.product_id', 'products.id'], kind: 'declared', conf: 1, why: 'Declared foreign key', warns: [], card: 'many → one' },
      { on: ['products.category_id', 'categories.id'], kind: 'inferred', conf: 0.93, why: 'Name matches categories, indexed', warns: [], card: 'many → one' }
    ],
    alts: [
      { route: ['orders', 'carts', 'cart_items', 'product_variants', 'products', 'categories'], conf: 0.77, hops: 5, warns: ['fan-out'] },
      { route: ['orders', 'users', 'products', 'categories'], conf: 0.52, hops: 3, warns: ['hub: users'] },
      { route: ['orders', 'audit_log', 'products', 'categories'], conf: 0.41, hops: 3, warns: ['log table', 'polymorphic'] }
    ],
    sql: [
      ['k', 'SELECT'], ['', ' o.id, c.name\n'], ['k', 'FROM'], ['', ' shop.orders '], ['k', 'AS'], ['a', ' o'], ['', '\n'],
      ['k', 'JOIN'], ['', ' shop.order_items '], ['k', 'AS'], ['a', ' oi'], ['k', ' ON'], ['a', ' oi'], ['', '.order_id '], ['o', '='], ['a', ' o'], ['', '.id\n'],
      ['k', 'JOIN'], ['', ' shop.product_variants '], ['k', 'AS'], ['a', ' pv'], ['k', ' ON'], ['a', ' pv'], ['', '.id '], ['o', '='], ['a', ' oi'], ['', '.product_variant_id\n'],
      ['k', 'JOIN'], ['', ' shop.products '], ['k', 'AS'], ['a', ' p'], ['k', ' ON'], ['a', ' p'], ['', '.id '], ['o', '='], ['a', ' pv'], ['', '.product_id\n'],
      ['k', 'JOIN'], ['', ' shop.categories '], ['k', 'AS'], ['a', ' c'], ['k', ' ON'], ['a', ' c'], ['', '.id '], ['o', '='], ['a', ' p'], ['', '.category_id\n'],
      ['c', '-- fan-out: one order has many items; aggregate before summing order totals']
    ]
  };

  // Table view: shop.orders
  const table = {
    schema: 'shop', name: 'orders', area: 'Orders', grain: 'One row per customer order. A checkout that pays for items from several sellers is still one order.',
    rows: '4.21M', freshness: 'profiled 2 days ago', cols: 18, relations: 23, verified: true,
    keys: [
      { name: 'PRIMARY', kind: 'PK', cols: ['id'] }, { name: 'uq_orders_code', kind: 'UK', cols: ['code'] },
      { name: 'ix_orders_user_created', kind: 'IX', cols: ['user_id', 'created_at'] }, { name: 'ix_orders_status_updated', kind: 'IX', cols: ['status', 'updated_at'] },
      { name: 'ix_orders_cart', kind: 'IX', cols: ['cart_id'] }
    ],
    columns: [
      { n: 'id', t: 'bigint unsigned', null: false, keys: ['PK'], prof: { empty: 0, distinct: '4.21M' } },
      { n: 'code', t: 'varchar(16)', null: false, keys: ['UK'], prof: { empty: 0, distinct: '4.21M' } },
      { n: 'user_id', t: 'bigint unsigned', null: false, keys: ['FK', 'IX 1'], prof: { empty: 0, distinct: '1.38M' }, hub: 'users' },
      { n: 'cart_id', t: 'bigint unsigned', null: true, keys: ['FK', 'IX 1'], prof: { empty: 3.1, distinct: '4.08M' } },
      { n: 'status', t: 'tinyint', null: false, keys: ['IX 1'], enum: [['1', 'new'], ['2', 'paid'], ['3', 'packed'], ['4', 'shipped'], ['5', 'delivered'], ['6', 'cancelled'], ['9', '?', true]], selected: true, prof: { empty: 0, distinct: '7' } },
      { n: 'channel', t: "enum('web','app','pos')", null: false, keys: [], enum: [['web'], ['app'], ['pos']], prof: { empty: 0, distinct: '3' } },
      { n: 'total_amount', t: 'decimal(14,2)', null: false, keys: [], prof: { empty: 0, range: '0.00 – 48,920.00' } },
      { n: 'currency', t: 'char(3)', null: false, keys: [], prof: { empty: 0, top: 'USD 91%' } },
      { n: 'voucher_id', t: 'bigint unsigned', null: true, keys: ['FK'], prof: { empty: 82.4, distinct: '11.2K' } },
      { n: 'shipping_address_id', t: 'bigint unsigned', null: true, keys: ['FK'], prof: { empty: 0.4, distinct: '2.02M' } },
      { n: 'customer_email', t: 'varchar(190)', null: true, keys: [], pii: true, prof: { empty: 1.2, distinct: '1.36M' } },
      { n: 'customer_phone', t: 'varchar(32)', null: true, keys: [], pii: true, prof: { empty: 14.8, distinct: '1.19M' } },
      { n: 'created_at', t: 'datetime', null: false, keys: ['IX 2'], prof: { empty: 0, range: '2019-03-02 – 2026-11-01' } },
      { n: 'updated_at', t: 'datetime', null: false, keys: ['IX 2'], prof: { empty: 0, range: '2019-03-02 – 2026-11-01' } },
      { n: 'created_by_id', t: 'bigint unsigned', null: true, keys: [], hub: 'users', prof: { empty: 97.9, distinct: '214' } },
      { n: 'is_deleted', t: 'tinyint(1)', null: false, keys: [], enum: [['0'], ['1']], prof: { empty: 0, top: '0 99.6%' } }
    ],
    pointsTo: [
      { col: 'cart_id', items: [{ t: 'carts', area: 'Orders', kind: 'inferred', conf: 0.92 }] },
      { col: 'shipping_address_id', items: [{ t: 'addresses', area: 'Customers', kind: 'declared', conf: 1 }] },
      { col: 'voucher_id', items: [{ t: 'vouchers', area: 'Promotions', kind: 'inferred', conf: 0.88 }] }
    ],
    pointedBy: [
      { col: 'id', items: [
        { t: 'order_items.order_id', area: 'Orders', kind: 'declared', conf: 1 }, { t: 'order_status_history.order_id', area: 'Orders', kind: 'declared', conf: 1 },
        { t: 'payments.order_id', area: 'Payments', kind: 'declared', conf: 1 }, { t: 'shipments.order_id', area: 'Shipping', kind: 'inferred', conf: 0.94 },
        { t: 'invoices.order_id', area: 'Payments', kind: 'inferred', conf: 0.9 }, { t: 'order_notes.order_id', area: 'Orders', kind: 'inferred', conf: 0.81 }
      ], collapsed: [{ n: 9, via: 'order_id', areas: 'Analytics, Audit log' }] }
    ],
    hubs: ['users via user_id', 'users via created_by_id']
  };
  const column = {
    t: 'orders', c: 'status', type: 'tinyint', meaning: 'Where the order is in its life cycle. Set by the checkout and fulfilment services; never moves backwards except from shipped to cancelled.',
    codes: [['1', 'new', 2.1], ['2', 'paid', 4.4], ['3', 'packed', 1.6], ['4', 'shipped', 3.9], ['5', 'delivered', 79.2], ['6', 'cancelled', 8.5], ['9', 'not documented', 0.3, true]],
    metrics: ['Delivered orders', 'Cancellation rate'],
    filters: 'status = 5 for completed revenue; pair with updated_at for the delivery date',
    index: 'Leading column of ix_orders_status_updated'
  };
  const search = {
    q: 'refund',
    groups: [
      { type: 'Concepts', glyph: 'concept', items: [{ title: 'Refund', sans: true, why: ['synonym', 'Synonyms: <b>refund</b>, money back, chargeback'], meta: '4 tables' }] },
      { type: 'Metrics', glyph: 'metric', items: [{ title: 'Refund rate', sans: true, why: ['name', 'Refunded amount ÷ paid amount, by payment date'], meta: 'billing.payment_refunds' }] },
      { type: 'Tables', glyph: 'table', items: [
        { title: 'billing.payment_<mark>refund</mark>s', why: ['name', 'One row per <b>refund</b> issued against a payment'], meta: 'Payments' },
        { title: 'shop.returns', why: ['context', 'Notes: a return creates a <mark>refund</mark> once the item is received'], meta: 'Orders' }] },
      { type: 'Columns', glyph: 'column', items: [
        { title: 'shop.returns.<mark>refund</mark>_id', why: ['name', 'bigint · points to billing.payment_refunds.id (0.74)'], meta: 'Orders' },
        { title: 'billing.payments.<mark>refund</mark>ed_amount', why: ['name', 'decimal(14,2) · 6.8% non-zero'], meta: 'Payments' }] },
      { type: 'Values', glyph: 'column', items: [
        { title: 'billing.payments.status = 7', why: ['enum label', 'Code 7 means “<mark>refund</mark>ed” (1.9% of rows)'], meta: 'Payments' },
        { title: 'shop.order_status_history.reason', why: ['profiled value', 'Top value “customer_<mark>refund</mark>” (3.2% of rows)'], meta: 'Orders' }] }
    ]
  };
  const stats = { tables: 63, columns: 742, relations: 188, declared: 41, inferred: 139, polymorphic: 8, areas: 13, hubs: 2, built: '2026-11-03 09:12' };
  const recent = ['shop.orders', 'shop.order_items', 'billing.payments', 'shop.products', 'logistics.shipments'];
  return { S, areas, links, orderTables, orderEdges, neighbours, path, table, column, search, stats, recent };
})();
