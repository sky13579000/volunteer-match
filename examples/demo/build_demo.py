"""Creates workspace/erp.db (the 'existing business DB') and ingests the demo documents."""
import sqlite3
import sys
from pathlib import Path

here = Path(__file__).parent
(here / "workspace").mkdir(exist_ok=True)
db_path = here / "workspace" / "erp.db"
db_path.unlink(missing_ok=True)
db = sqlite3.connect(db_path)
db.executescript("""
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, industry TEXT, credit_grade TEXT);
CREATE TABLE contracts(id TEXT PRIMARY KEY, project_code TEXT, customer_name TEXT, amount INTEGER,
                       margin_rate REAL, due_date TEXT, owner_dept TEXT);
CREATE TABLE invoices(id TEXT PRIMARY KEY, project_code TEXT, amount INTEGER, status TEXT);
INSERT INTO customers VALUES (1,'한빛전자','전자','A'),(2,'대성정밀','기계','B+'),(3,'누리건설','건설','A-');
INSERT INTO contracts VALUES
 ('CT-2024-001','PRJ-2024','한빛전자',480000000,0.18,'2024-12-20','sales'),
 ('CT-2025-002','PRJ-2025','대성정밀',250000000,0.22,'2025-06-30','sales'),
 ('CT-2031-003','PRJ-2031','누리건설',120000000,0.15,'2025-03-31','consulting');
INSERT INTO invoices VALUES
 ('INV-001','PRJ-2024',144000000,'수금완료'),('INV-002','PRJ-2024',192000000,'미수'),
 ('INV-003','PRJ-2025',75000000,'수금완료');
""")
db.commit()
db.close()

sys.path.insert(0, str(here.parent.parent))
from edm_ai.config import App  # noqa: E402

print(App(str(here / "config.json")).ingest())
