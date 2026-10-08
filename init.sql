USE company_sources_practice;

CREATE TABLE IF NOT EXISTS mysql_orders (
  order_id VARCHAR(40) PRIMARY KEY,
  order_date DATE,
  customer_id VARCHAR(80),
  customer_segment VARCHAR(40),
  region VARCHAR(80),
  country VARCHAR(80),
  sales_channel VARCHAR(40),
  product_category VARCHAR(80),
  product_name VARCHAR(120),
  quantity INT,
  unit_price DECIMAL(12,2),
  discount_pct DECIMAL(7,3),
  shipping_cost DECIMAL(12,2),
  payment_method VARCHAR(50),
  order_status VARCHAR(30),
  sales_rep VARCHAR(80),
  revenue DECIMAL(14,2),
  profit DECIMAL(14,2),
  profit_margin_pct DECIMAL(9,3),
  customer_rating DECIMAL(4,2)
);

-- The app's source reader must not write to the imported data store.
REVOKE ALL PRIVILEGES ON company_sources_practice.* FROM 'analytics'@'%';
GRANT SELECT ON company_sources_practice.* TO 'analytics'@'%';
