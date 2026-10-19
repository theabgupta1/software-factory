SELECT c.name, SUM(o.total_cents) / 100.0 AS revenue
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.created_at BETWEEN '2026-10-01' AND '2026-10-31'
GROUP BY c.name ORDER BY revenue DESC LIMIT 3;
