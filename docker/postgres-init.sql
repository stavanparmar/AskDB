\getenv analyst_password ASKDB_ANALYST_PASSWORD
\getenv manager_password ASKDB_MANAGER_PASSWORD

CREATE ROLE askdb_readonly NOLOGIN;
SELECT format(
    'CREATE ROLE askdb_analyst LOGIN PASSWORD %L',
    :'analyst_password'
) \gexec
SELECT format(
    'CREATE ROLE askdb_manager LOGIN PASSWORD %L',
    :'manager_password'
) \gexec
GRANT askdb_readonly TO askdb_analyst, askdb_manager;
GRANT CONNECT ON DATABASE askdb TO askdb_readonly;
ALTER ROLE askdb_analyst SET default_transaction_read_only = on;
ALTER ROLE askdb_manager SET default_transaction_read_only = on;
ALTER ROLE askdb_analyst IN DATABASE askdb SET search_path TO askdb_analyst, public;
ALTER ROLE askdb_manager IN DATABASE askdb SET search_path TO askdb_manager, public;

CREATE TABLE public.customers (
    id integer PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    city text NOT NULL,
    signup_date date NOT NULL
);
CREATE TABLE public.products (
    id integer PRIMARY KEY,
    name text NOT NULL,
    category text NOT NULL,
    price numeric(10, 2) NOT NULL
);
CREATE TABLE public.orders (
    id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES public.customers(id),
    product_id integer NOT NULL REFERENCES public.products(id),
    order_date date NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(10, 2) NOT NULL,
    total numeric(10, 2) NOT NULL
);

INSERT INTO public.customers (id, name, email, city, signup_date) VALUES
    (1, 'Avery Adams', 'avery.adams@example.com', 'Austin', '2024-01-10'),
    (2, 'Blake Bennett', 'blake.bennett@example.com', 'Boston', '2024-02-15'),
    (3, 'Casey Carter', 'casey.carter@example.com', 'Austin', '2024-03-20');
INSERT INTO public.products (id, name, category, price) VALUES
    (1, 'Wireless Keyboard', 'Electronics', 64.99),
    (2, 'Ceramic Mug Set', 'Home', 28.75),
    (3, 'Trail Running Shoes', 'Sports', 118.00);
INSERT INTO public.orders (
    id, customer_id, product_id, order_date, quantity, unit_price, total
) VALUES
    (1, 1, 1, '2025-01-05', 2, 64.99, 129.98),
    (2, 2, 2, '2025-01-12', 1, 28.75, 28.75),
    (3, 3, 3, '2025-02-02', 1, 118.00, 118.00);

CREATE SCHEMA askdb_analyst;
CREATE SCHEMA askdb_manager;
CREATE VIEW askdb_analyst.customers AS
    SELECT id, name, city, signup_date FROM public.customers;
CREATE VIEW askdb_manager.customers AS
    SELECT id, name, email, city, signup_date FROM public.customers;
CREATE VIEW askdb_analyst.products AS
    SELECT id, name, category, price FROM public.products;
CREATE VIEW askdb_manager.products AS
    SELECT id, name, category, price FROM public.products;
CREATE VIEW askdb_analyst.orders AS
    SELECT id, customer_id, product_id, order_date, quantity, unit_price, total
    FROM public.orders;
CREATE VIEW askdb_manager.orders AS
    SELECT id, customer_id, product_id, order_date, quantity, unit_price, total
    FROM public.orders;

GRANT USAGE ON SCHEMA askdb_analyst, askdb_manager TO askdb_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA askdb_analyst TO askdb_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA askdb_manager TO askdb_readonly;
