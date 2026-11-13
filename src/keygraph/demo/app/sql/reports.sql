-- Daily revenue by payment method
SELECT pm.code, SUM(p.amount) AS revenue
FROM billing.payments p
JOIN billing.payment_methods pm ON pm.id = p.method_id
WHERE p.status = 2
GROUP BY pm.code;

-- Refund every payment that was charged back
INSERT INTO billing.refunds (payment_id, amount, reason, created_at)
SELECT id, amount, 'chargeback', CURRENT_TIMESTAMP FROM billing.payments WHERE status = 9;
