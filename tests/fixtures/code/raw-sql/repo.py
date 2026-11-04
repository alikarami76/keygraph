LATE = """
SELECT o.id, o.placed_at, c.email
FROM orders o
JOIN customers c ON c.id = o.customer_id
WHERE o.status = %s
"""


def mark_paid(db, order_id):
    db.execute("UPDATE orders SET status = 3 WHERE id = ?", (order_id,))


def archive(db):
    db.execute(f"INSERT INTO order_archive (id) SELECT id FROM {table}")
