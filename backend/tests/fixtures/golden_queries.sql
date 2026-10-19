-- P0-T02 independent PostgreSQL golden queries.
-- Runtime catalog 1.1.0-offers-repairs enables all seven formulas below;
-- legacy 1.0.0-import-baseline continues to expose its four sales KPIs only.
-- Evidence baseline: SQL import SHA-256
-- c585831fc15942bfa3f3fa793d4d4ebedfde81905ae1214781431001043abbe3
--
-- Parameters for every prepared statement:
--   $1 scope: ALL, CA, or TX
--   $2 start_date: inclusive
--   $3 end_date_exclusive: exclusive
--
-- Every statement uses exactly one fact table. Properties/property_types are
-- dimensions only. Do not combine transactions, offers, and repairs in one
-- aggregate query.

PREPARE golden_sales(text, date, date) AS
SELECT
    SUM(t.sale_price) AS sales_value,
    COUNT(t.transaction_id) AS transaction_count,
    SUM(t.sale_price) / NULLIF(COUNT(t.transaction_id), 0) AS average_sale_value,
    COUNT(DISTINCT t.property_id) AS sold_property_count
FROM public.transactions AS t
JOIN public.properties AS p
  ON p.property_id = t.property_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND t.transaction_date >= $2
  AND t.transaction_date < $3;

PREPARE golden_offers(text, date, date) AS
SELECT
    COUNT(o.offer_id) AS offer_count,
    COUNT(o.offer_id) FILTER (WHERE o.status = 'accepted') AS accepted_offer_count,
    (COUNT(o.offer_id) FILTER (WHERE o.status = 'accepted'))::numeric
        / NULLIF(COUNT(o.offer_id), 0) AS accepted_offer_rate
FROM public.offers AS o
JOIN public.properties AS p
  ON p.property_id = o.property_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND o.offer_date >= $2
  AND o.offer_date < $3;

PREPARE golden_repairs(text, date, date) AS
SELECT
    COUNT(r.repair_id) AS repair_row_count,
    SUM(r.cost) AS repair_cost
FROM public.repairs AS r
JOIN public.properties AS p
  ON p.property_id = r.property_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND r.repair_date >= $2
  AND r.repair_date < $3;

-- Dimension reference queries. A compiler may select a subset of these
-- dimensions, but must preserve the fact grain and matching date column.

PREPARE golden_sales_dimensions(text, date, date) AS
SELECT
    date_trunc('month', t.transaction_date)::date AS month,
    p.state,
    p.city,
    pt.type_name AS property_type,
    t.agent_id,
    SUM(t.sale_price) AS sales_value,
    COUNT(t.transaction_id) AS transaction_count,
    SUM(t.sale_price) / NULLIF(COUNT(t.transaction_id), 0) AS average_sale_value,
    COUNT(DISTINCT t.property_id) AS sold_property_count
FROM public.transactions AS t
JOIN public.properties AS p
  ON p.property_id = t.property_id
JOIN public.property_types AS pt
  ON pt.property_type_id = p.property_type_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND t.transaction_date >= $2
  AND t.transaction_date < $3
GROUP BY 1, p.state, p.city, pt.type_name, t.agent_id
ORDER BY 1, p.state, p.city, pt.type_name, t.agent_id;

PREPARE golden_offer_dimensions(text, date, date) AS
SELECT
    date_trunc('month', o.offer_date)::date AS month,
    p.state,
    p.city,
    pt.type_name AS property_type,
    COUNT(o.offer_id) AS offer_count,
    (COUNT(o.offer_id) FILTER (WHERE o.status = 'accepted'))::numeric
        / NULLIF(COUNT(o.offer_id), 0) AS accepted_offer_rate
FROM public.offers AS o
JOIN public.properties AS p
  ON p.property_id = o.property_id
JOIN public.property_types AS pt
  ON pt.property_type_id = p.property_type_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND o.offer_date >= $2
  AND o.offer_date < $3
GROUP BY 1, p.state, p.city, pt.type_name
ORDER BY 1, p.state, p.city, pt.type_name;

PREPARE golden_repair_dimensions(text, date, date) AS
SELECT
    date_trunc('month', r.repair_date)::date AS month,
    p.state,
    p.city,
    pt.type_name AS property_type,
    COUNT(r.repair_id) AS repair_row_count,
    SUM(r.cost) AS repair_cost
FROM public.repairs AS r
JOIN public.properties AS p
  ON p.property_id = r.property_id
JOIN public.property_types AS pt
  ON pt.property_type_id = p.property_type_id
WHERE ($1 = 'ALL' OR p.state = $1)
  AND r.repair_date >= $2
  AND r.repair_date < $3
GROUP BY 1, p.state, p.city, pt.type_name
ORDER BY 1, p.state, p.city, pt.type_name;

-- Case matrix (expected output is in golden_results.json):
--   all_time_{all,ca,tx}:       DATE '0001-01-01' -> DATE '9999-12-31'
--   calendar_2025_{all,ca,tx}:  DATE '2025-01-01' -> DATE '2026-01-01'
--   empty_2030_{all,ca,tx}:     DATE '2030-01-01' -> DATE '2031-01-01'
-- Boundary cases use one-day half-open intervals around the min/max date of
-- each fact. The same three prepared aggregate statements cover every scope,
-- period, and empty case without joining fact tables.
-- Representative invocations:
-- EXECUTE golden_sales('ALL', DATE '0001-01-01', DATE '9999-12-31');
-- EXECUTE golden_offers('CA', DATE '2025-01-01', DATE '2026-01-01');
-- EXECUTE golden_repairs('TX', DATE '2030-01-01', DATE '2031-01-01');

