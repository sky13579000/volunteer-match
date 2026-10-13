-- Demo ERP schema + a SELECT-only account for EDM-AI (least privilege).
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, industry TEXT, credit_grade TEXT);
CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY, project_code TEXT, customer_name TEXT, amount BIGINT,
                                     margin_rate NUMERIC(5,2), due_date TEXT, owner_dept TEXT);
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, project_code TEXT, amount BIGINT, status TEXT);
TRUNCATE customers, contracts, invoices;
INSERT INTO customers VALUES (1,'한빛전자','전자','A'),(2,'대성정밀','기계','B+'),(3,'누리건설','건설','A-');
INSERT INTO contracts VALUES
 ('CT-2024-001','PRJ-2024','한빛전자',480000000,0.18,'2024-12-20','sales'),
 ('CT-2025-002','PRJ-2025','대성정밀',250000000,0.22,'2025-06-30','sales'),
 ('CT-2031-003','PRJ-2031','누리건설',120000000,0.15,'2025-03-31','consulting');
INSERT INTO invoices VALUES
 ('INV-001','PRJ-2024',144000000,'수금완료'),('INV-002','PRJ-2024',192000000,'미수'),
 ('INV-003','PRJ-2025',75000000,'수금완료');

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'edm_ro') THEN
    CREATE ROLE edm_ro LOGIN PASSWORD 'change-me-edm-ro';   -- change in production
  END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM edm_ro;
GRANT USAGE ON SCHEMA public TO edm_ro;
GRANT SELECT ON customers, contracts, invoices TO edm_ro;   -- only what config.json whitelists
