SELECT c.name, ROUND(SUM(o.total_cents) / 100.0, 2) AS revenue
FROM orders o
JOIN customers c ON c.id = o.customer_id
WHERE o.status = 'paid'
  AND o.created_at >= '2026-10-01 00:00:00'
  AND o.created_at < '2026-11-01 00:00:00'
GROUP BY c.id, c.name
ORDER BY revenue DESC, c.name ASC
LIMIT 3;
