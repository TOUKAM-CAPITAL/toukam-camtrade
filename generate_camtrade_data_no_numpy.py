import csv
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

BASE = Path(__file__).resolve().parent
OUT = BASE / "CAMTRADE_DATA"
OUT.mkdir(exist_ok=True)

def write_csv(filename, columns, rows):
    with open(OUT / filename, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)

# 1. PRODUCTS
products = [
    ("P001","Riz parfume 25kg","Alimentaire",12500,15500,"Import"),
    ("P002","Riz ordinaire 25kg","Alimentaire",10500,13200,"Import"),
    ("P003","Huile vegetale 1L","Alimentaire",1800,2400,"Import"),
    ("P004","Huile vegetale 5L","Alimentaire",8200,10500,"Import"),
    ("P005","Sucre 1kg","Alimentaire",650,900,"Import"),
    ("P006","Lait en poudre 500g","Alimentaire",4800,6200,"Import"),
    ("P007","Spaghetti 500g","Alimentaire",550,800,"Import"),
    ("P008","Savon 250g","Hygiene",450,650,"Import"),
    ("P009","Detergent 1L","Hygiene",1300,1900,"Import"),
    ("P010","Eau minerale 1.5L","Boissons",250,450,"Local"),
    ("P011","Boisson gazeuse 50cl","Boissons",300,500,"Local"),
    ("P012","Jus de fruit 1L","Boissons",900,1450,"Local"),
    ("P013","Cafe 250g","Alimentaire",2200,3200,"Import"),
    ("P014","Tomate concentree","Alimentaire",750,1100,"Import"),
    ("P015","Farine 1kg","Alimentaire",700,980,"Import"),
    ("P016","Biscuit familial","Alimentaire",900,1350,"Import"),
    ("P017","Papier hygienique","Hygiene",1700,2450,"Import"),
    ("P018","Gel douche","Hygiene",1800,2900,"Import"),
    ("P019","Carton emballage","Emballage",2200,3000,"Local"),
    ("P020","Produit premium importe","Premium",9500,14500,"Import"),
]

# 2. CUSTOMERS
cities = ["Douala","Yaounde","Bafoussam","Bamenda","Garoua"]
segments = ["Grossiste","Supermarche","Hotel/Restaurant","Boutique","Institution"]
customers = []
for i in range(1,81):
    segment = random.choices(segments, weights=[35,22,15,23,5])[0]
    city = random.choices(cities, weights=[42,27,12,8,11])[0]
    tier = random.choices(["Normal","Important","Strategique"], weights=[58,30,12])[0]
    customers.append((f"C{i:03d}",f"Client {i:03d}",city,segment,
                      random.randint(7,30),tier))

# 3. SUPPLIERS
suppliers = [
    ("S001","Global Foods Trading","Chine","Alimentaire",18),
    ("S002","West Africa Supply","Nigeria","Alimentaire",12),
    ("S003","Gulf Consumer Goods","UAE","Alimentaire",25),
    ("S004","CleanHome Industries","Turquie","Hygiene",20),
    ("S005","CamBeverage","Cameroun","Boissons",10),
    ("S006","PackCam","Cameroun","Emballage",14),
    ("S007","Premium Imports","UAE","Premium",30),
]

# 4. SALES - 24 months
start = date(2024,1,1)
end = date(2025,12,31)
sales = []
sale_id = 1
d = start
while d <= end:
    n = random.randint(4,22) if d.weekday() < 5 else random.randint(2,12)
    for _ in range(n):
        p = random.choice(products)
        c = random.choice(customers)
        year_factor = 1.0 if d.year == 2024 else 1.18
        qty = max(1, int(random.lognormvariate(3.0,0.65) * year_factor))
        price_factor = random.gauss(1.0,0.035)
        if d.year == 2025:
            price_factor *= 0.96
        if c[5] == "Strategique":
            price_factor *= 0.95
        unit_price = round(p[4] * price_factor)
        revenue = qty * unit_price
        transport = max(0, round(qty * random.gauss(45,12)))
        discount = max(0, qty*p[4] - revenue)
        due = d + timedelta(days=c[4])
        sales.append((f"V{sale_id:06d}",str(d),p[0],c[0],qty,unit_price,
                      revenue,discount,transport,str(due)))
        sale_id += 1
    d += timedelta(days=1)

# 5. PURCHASES - costs rise in 2025
purchases = []
purchase_id = 1
d = start
while d <= end:
    n = random.randint(1,8)
    for _ in range(n):
        p = random.choice(products)
        s = random.choice(suppliers)
        qty = max(100,int(random.lognormvariate(5.2,0.55)))
        factor = random.gauss(1.0,0.04)
        if d.year == 2025:
            factor *= 1.11
            if p[5] == "Import" and p[2] == "Alimentaire":
                factor *= 1.04
        unit_cost = round(p[3] * factor)
        total = qty * unit_cost
        freight = round(total * random.uniform(0.025,0.075))
        purchases.append((f"A{purchase_id:06d}",str(d),p[0],s[0],qty,
                          unit_cost,total,freight))
        purchase_id += 1
    d += timedelta(days=2)

# 6. EXPENSES
expense_types = [
    ("Salaires",0.26),("Transport",0.20),("Loyer",0.10),
    ("Energie",0.08),("Marketing",0.08),("Maintenance",0.07),
    ("Frais bancaires",0.05),("Telecom",0.04),("Assurance",0.04),
    ("Divers",0.08)
]
expenses = []
eid = 1
for year in [2024,2025]:
    for month in range(1,13):
        # NOTE: these constants are the company's ANNUAL operating-expense
        # budget (105M FCFA in 2024, 125M in 2025). Divide by 12 to get a
        # monthly figure — a previous version of this generator used the
        # annual figure directly as the monthly base, which inflated total
        # opex ~12x and produced an impossible EBITDA margin (~-340%).
        base = (105_000_000 if year == 2024 else 125_000_000) / 12
        for name,weight in expense_types:
            amount = base*weight*random.gauss(1.0,0.06)
            if year == 2025 and name in ("Energie","Transport"):
                amount *= 1.18
            if name == "Maintenance" and (year,month) in [(2025,4),(2025,10)]:
                amount *= 2.7
            expenses.append((f"E{eid:05d}",f"{year}-{month:02d}-01",
                             name,round(amount),"Operating"))
            eid += 1

# 7. INVENTORY
inventory = []
for p in products:
    stock = random.randint(400,3500)
    reorder = int(stock*0.22)
    demand = int(stock*0.45)
    status = "Normal"
    inventory.append((p[0],p[1],stock,reorder,demand,status))

overrides = {"P002":(120,"Risque rupture"),"P009":(85,"Risque rupture"),
             "P017":(60,"Risque rupture"),"P016":(4200,"Stock dormant"),
             "P019":(3900,"Stock dormant")}
inventory = [(pid,name,(overrides[pid][0] if pid in overrides else stock),
             reorder,demand,(overrides[pid][1] if pid in overrides else status))
            for pid,name,stock,reorder,demand,status in inventory]

# 8. RECEIVABLES
receivables = []
for c in customers:
    balance = max(0,int(random.gauss(22_000_000,12_000_000)))
    if c[5] == "Strategique":
        balance = int(balance*1.25)
    overdue = max(0,int(random.gauss(18,20)))
    receivables.append((c[0],c[1],balance,overdue,
                        "En retard" if overdue > 30 else "Normal"))

# Write CSVs
write_csv("products.csv",
          ["product_id","product_name","category","purchase_cost","standard_sale_price","source"], products)
write_csv("customers.csv",
          ["customer_id","customer_name","city","segment","payment_days","tier"], customers)
write_csv("suppliers.csv",
          ["supplier_id","supplier_name","country","specialty","payment_days"], suppliers)
write_csv("sales.csv",
          ["sale_id","sale_date","product_id","customer_id","quantity","unit_price","revenue","discount","allocated_transport","due_date"], sales)
write_csv("purchases.csv",
          ["purchase_id","purchase_date","product_id","supplier_id","quantity","unit_cost","total_cost","freight"], purchases)
write_csv("expenses.csv",
          ["expense_id","expense_month","expense_type","amount","expense_class"], expenses)
write_csv("inventory.csv",
          ["product_id","product_name","current_stock","reorder_point","average_monthly_demand","status"], inventory)
write_csv("receivables.csv",
          ["customer_id","customer_name","outstanding_balance","days_overdue","status"], receivables)

# 9. SQLITE DATABASE - no external package required
db_path = OUT / "CAMTRADE.db"
if db_path.exists():
    db_path.unlink()

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.executescript("""
CREATE TABLE products (
 product_id TEXT PRIMARY KEY, product_name TEXT, category TEXT,
 purchase_cost REAL, standard_sale_price REAL, source TEXT);

CREATE TABLE customers (
 customer_id TEXT PRIMARY KEY, customer_name TEXT, city TEXT,
 segment TEXT, payment_days INTEGER, tier TEXT);

CREATE TABLE suppliers (
 supplier_id TEXT PRIMARY KEY, supplier_name TEXT, country TEXT,
 specialty TEXT, payment_days INTEGER);

CREATE TABLE sales (
 sale_id TEXT PRIMARY KEY, sale_date TEXT, product_id TEXT,
 customer_id TEXT, quantity INTEGER, unit_price REAL, revenue REAL,
 discount REAL, allocated_transport REAL, due_date TEXT);

CREATE TABLE purchases (
 purchase_id TEXT PRIMARY KEY, purchase_date TEXT, product_id TEXT,
 supplier_id TEXT, quantity INTEGER, unit_cost REAL, total_cost REAL,
 freight REAL);

CREATE TABLE expenses (
 expense_id TEXT PRIMARY KEY, expense_month TEXT, expense_type TEXT,
 amount REAL, expense_class TEXT);

CREATE TABLE inventory (
 product_id TEXT PRIMARY KEY, product_name TEXT, current_stock INTEGER,
 reorder_point INTEGER, average_monthly_demand INTEGER, status TEXT);

CREATE TABLE receivables (
 customer_id TEXT PRIMARY KEY, customer_name TEXT,
 outstanding_balance REAL, days_overdue INTEGER, status TEXT);
""")

for table, rows in [
    ("products",products),("customers",customers),("suppliers",suppliers),
    ("sales",sales),("purchases",purchases),("expenses",expenses),
    ("inventory",inventory),("receivables",receivables)]:
    placeholders = ",".join(["?"]*len(rows[0]))
    cur.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

conn.commit()

# Add useful indexes for the future analytics engine
cur.executescript("""
CREATE INDEX idx_sales_date ON sales(sale_date);
CREATE INDEX idx_sales_product ON sales(product_id);
CREATE INDEX idx_sales_customer ON sales(customer_id);
CREATE INDEX idx_purchases_date ON purchases(purchase_date);
CREATE INDEX idx_purchases_product ON purchases(product_id);
CREATE INDEX idx_expenses_month ON expenses(expense_month);
""")
conn.commit()
conn.close()

print()
print("="*55)
print("CAMTRADE DATASET CREATED SUCCESSFULLY")
print("="*55)
print(f"Sales transactions : {len(sales):,}")
print(f"Purchase transactions: {len(purchases):,}")
print(f"Customers          : {len(customers):,}")
print(f"Products           : {len(products):,}")
print(f"Expense records    : {len(expenses):,}")
print(f"Database            : {db_path}")
print()
print("CSV files and CAMTRADE.db are in CAMTRADE_DATA.")
