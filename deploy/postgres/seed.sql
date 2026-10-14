-- Demo ERP schema + a SELECT-only account for EDM-AI (least privilege).
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, industry TEXT, credit_grade TEXT);
CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY, project_code TEXT, customer_name TEXT, amount BIGINT,
                                     margin_rate NUMERIC(5,2), due_date TEXT, owner_dept TEXT);
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, project_code TEXT, amount BIGINT, status TEXT);
TRUNCATE customers, contracts, invoices;
INSERT INTO customers VALUES (1,'제주관광공사','공공','A'),(2,'제주해양포럼','국제행사','B+'),(3,'제주도청','공공','A-');
INSERT INTO contracts VALUES
 ('CT-2024-001','EVT-2024','제주관광공사',480000000,0.18,'2026-12-20','mice'),
 ('CT-2025-002','EVT-2025','제주해양포럼',250000000,0.22,'2027-03-31','mice'),
 ('CT-2031-003','EVT-2031','제주도청',120000000,0.15,'2026-10-31','provincial');
INSERT INTO invoices VALUES
 ('INV-001','EVT-2024',144000000,'수금완료'),('INV-002','EVT-2024',192000000,'미수'),
 ('INV-003','EVT-2025',75000000,'수금완료');

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'edm_ro') THEN
    CREATE ROLE edm_ro LOGIN PASSWORD 'change-me-edm-ro';   -- change in production
  END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM edm_ro;
GRANT USAGE ON SCHEMA public TO edm_ro;
GRANT SELECT ON customers, contracts, invoices TO edm_ro;   -- only what config.json whitelists
