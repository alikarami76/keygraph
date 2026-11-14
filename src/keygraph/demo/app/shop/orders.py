"""Order service with raw SQL (fictional)."""
from .models import Order

LATE_ORDERS = """
SELECT o.id, o.placed_at, c.email
FROM orders o
JOIN customers c ON c.id = o.customer_id
WHERE o.status = %s AND o.placed_at < %s
"""


def late_orders(db, status, before):
    return db.execute(LATE_ORDERS, (status, before)).fetchall()


def mark_paid(db, order_id):
    db.execute("UPDATE orders SET status = 2 WHERE id = ?", (order_id,))


def order_total(order: Order):
    return order.total
