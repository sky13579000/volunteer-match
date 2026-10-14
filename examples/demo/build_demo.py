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
INSERT INTO customers VALUES (1,'제주관광공사','공공','A'),(2,'제주해양포럼','국제행사','B+'),(3,'제주도청','공공','A-');
INSERT INTO contracts VALUES
 ('CT-2024-001','EVT-2024','제주관광공사',480000000,0.18,'2026-12-20','mice'),
 ('CT-2025-002','EVT-2025','제주해양포럼',250000000,0.22,'2027-03-31','mice'),
 ('CT-2031-003','EVT-2031','제주도청',120000000,0.15,'2026-10-31','provincial');
INSERT INTO invoices VALUES
 ('INV-001','EVT-2024',144000000,'수금완료'),('INV-002','EVT-2024',192000000,'미수'),
 ('INV-003','EVT-2025',75000000,'수금완료');
""")
db.commit()
db.close()

sys.path.insert(0, str(here.parent.parent))
from edm_ai.config import App  # noqa: E402

app = App(str(here / "config.json"))
print(app.ingest())
sys.path.insert(0, str(here))
from seed import seed  # noqa: E402

print("demo documents seeded:", seed(app))
