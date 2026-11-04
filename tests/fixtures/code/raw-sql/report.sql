-- revenue per order
SELECT p.order_id, SUM(p.amount) AS revenue
FROM payments p
GROUP BY p.order_id;

DELETE FROM shipments WHERE status = 9;
