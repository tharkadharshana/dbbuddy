import mysql.connector
import os
import random
import json
from faker import Faker
from datetime import datetime, timedelta
from decimal import Decimal
import uuid

from dotenv import load_dotenv

# Initialize Faker
fake = Faker()
load_dotenv()

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "datamind_db")
    )

def seed():
    conn = get_connection()
    cursor = conn.cursor()

    print("Cleaning up old data...")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("TRUNCATE TABLE invoice_items")
    cursor.execute("TRUNCATE TABLE invoices")
    cursor.execute("TRUNCATE TABLE employees")
    cursor.execute("TRUNCATE TABLE products")
    cursor.execute("TRUNCATE TABLE customers")
    cursor.execute("TRUNCATE TABLE locations")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    # 1. Seed Locations
    print("Seeding locations...")
    locations = []
    location_names = ["Colombo Main", "Kandy Express", "Galle Fort", "Negombo Beach", "Jaffna Central"]
    start_date = datetime.now() - timedelta(days=180)
    for name in location_names:
        l_key = f"LIC-{fake.unique.bothify(text='??-####-####').upper()}"
        created_at = start_date + timedelta(days=random.randint(0, 30))
        cursor.execute(
            "INSERT INTO locations (location_name, address, licenceKey, created_at) VALUES (%s, %s, %s, %s)",
            (name, fake.address(), l_key, created_at)
        )
        locations.append((cursor.lastrowid, l_key))

    # 2. Seed Products
    print("Seeding products...")
    categories = ["Fast Food", "Beverages", "Desserts", "Grocery", "Electronics"]
    products = []
    for _ in range(100):
        code = f"PRD-{fake.unique.bothify(text='####')}"
        name = fake.catch_phrase()
        cat = random.choice(categories)
        price = Decimal(random.uniform(10.0, 500.0)).quantize(Decimal("0.00"))
        cost = (price * Decimal(random.uniform(0.4, 0.7))).quantize(Decimal("0.00"))
        created_at = start_date + timedelta(days=random.randint(0, 60))
        cursor.execute(
            "INSERT INTO products (itemCode, name, category, basePrice, baseCost, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (code, name, cat, price, cost, created_at)
        )
        products.append({"itemCode": code, "name": name, "price": price, "cost": cost})

    # 3. Seed Employees
    print("Seeding employees...")
    employees = []
    for loc_id, _ in locations:
        for _ in range(4):
            e_id = f"EMP-{fake.unique.bothify(text='####')}"
            name = fake.name()
            created_at = start_date + timedelta(days=random.randint(0, 100))
            cursor.execute(
                "INSERT INTO employees (empId, cashierName, location_id, created_at) VALUES (%s, %s, %s, %s)",
                (e_id, name, loc_id, created_at)
            )
            employees.append({"empId": e_id, "cashierName": name, "location_id": loc_id})

    # 4. Seed Customers
    print("Seeding customers...")
    customer_ids = []
    for _ in range(500):
        c_id = f"CUST-{fake.unique.bothify(text='####')}"
        created_at = start_date + timedelta(days=random.randint(0, 180))
        cursor.execute(
            "INSERT INTO customers (customerId, name, email, loyaltyPoints, created_at) VALUES (%s, %s, %s, %s, %s)",
            (c_id, fake.name(), fake.email(), random.randint(0, 1000), created_at)
        )
        customer_ids.append(c_id)

    # 5. Seed Invoices & Items & Inventory Logs
    print("Seeding invoices and inventory logs (this may take a while)...")
    pay_methods = ["CASH", "CARD", "CHEQUE", "SPLIT", "LOYALTY"]
    order_types = ["DINE-IN", "TAKEAWAY", "DELIVERY"]
    channels = ["POS-COUNTER", "MOBILE-APP", "UBER-EATS", "PICKME"]
    
    start_date = datetime.now() - timedelta(days=180)
    
    for i in range(2000): # Seed 2000 invoices for now
        inv_num = f"INV-{100000 + i}"
        main_inv = inv_num
        cent_id = str(uuid.uuid4())[:8].upper()
        uniq_id = str(uuid.uuid4())
        ref = f"REF-{fake.bothify(text='####')}"
        kot = f"KOT-{random.randint(100, 999)}"
        ord_num = f"ORD-{random.randint(5000, 9999)}"
        
        emp = random.choice(employees)
        loc = [l for l in locations if l[0] == emp["location_id"]][0]
        lic_key = loc[1]
        
        cust_id = random.choice(customer_ids)
        
        # Temporal Data
        trans_date = start_date + timedelta(days=random.randint(0, 180), hours=random.randint(0, 23), minutes=random.randint(0, 59))
        inv_date = trans_date.date()
        inv_time = trans_date.time()
        start_dt = trans_date - timedelta(minutes=random.randint(5, 30))
        end_time = (trans_date + timedelta(minutes=random.randint(2, 10))).time()
        
        pay_method = random.choice(pay_methods)
        
        # Line Items
        num_items = random.randint(1, 8)
        invoice_items = []
        header_total = Decimal("0.00")
        header_discount = Decimal("0.00")
        header_tax = Decimal("0.00")
        
        selected_prods = random.sample(products, num_items)
        
        item_rows = []
        inv_log_rows = []
        for prod in selected_prods:
            qty = Decimal(random.randint(1, 5))
            price = prod["price"]
            cost = prod["cost"]
            item_discount = (price * qty * Decimal(random.uniform(0, 0.1))).quantize(Decimal("0.00"))
            tax = (price * qty * Decimal(0.1)).quantize(Decimal("0.00"))
            
            header_total += (price * qty) - item_discount + tax
            header_discount += item_discount
            header_tax += tax
            
            item_rows.append((
                inv_num, prod["itemCode"], qty, price, cost, item_discount, tax,
                fake.sentence(nb_words=3),
                json.dumps({"name": prod["name"], "cat": "Food"}), # itemsDataList
                json.dumps([{"type": "PROMO", "amount": float(item_discount)}]), # invoiceLineDiscountList
                json.dumps([{"addon": "Extra Cheese", "price": 1.5}]) # invoiceItemAddonList
            ))

            # Inventory Log (SALE)
            inv_log_rows.append((
                prod["itemCode"], emp["location_id"], -float(qty), "SALE", inv_date, inv_time
            ))

        # Header Financials
        cash_paid = header_total + Decimal(random.randint(0, 50)) if pay_method == "CASH" else header_total
        balance = cash_paid - header_total if pay_method == "CASH" else Decimal("0.00")
        
        cursor.execute(
            """INSERT INTO invoices (
                invoiceNumber, mainInvoiceNumber, invoiceCentralizeId, uniqueId, reference, kotNumber, orderNumber, licenceKey,
                customerId, customerCash, customerBalance, invoiceCustomerSignatures,
                invoiceTotal, totalDiscount, chargeTotalTax, chargeTotalCharge, creditAmount, creditComplete, creditDays,
                payMethod, chequeNo, cardNo, payment, splitPayment, loyaltyPoints,
                invoiceDate, invoiceTime, customizeTime, startDateTime, startTime, endTime,
                cashierName, empId, tableCode, orderType, pickupDetails, billNote, channel, location_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                inv_num, main_inv, cent_id, uniq_id, ref, kot, ord_num, lic_key,
                cust_id, cash_paid, balance, "https://cdn.example.com/sigs/s1.png",
                header_total, header_discount, header_tax, Decimal("0.00"), Decimal("0.00"), False, 0,
                pay_method, 
                fake.bothify(text='CHQ-####') if pay_method == "CHEQUE" else None,
                fake.credit_card_number() if pay_method == "CARD" else None,
                json.dumps({"method": pay_method, "amount": float(header_total)}),
                json.dumps([]) if pay_method != "SPLIT" else json.dumps([{"method": "CASH", "amount": float(header_total/2)}, {"method": "CARD", "amount": float(header_total/2)}]),
                random.randint(1, 20),
                inv_date, inv_time, inv_time, start_dt, start_dt.time(), end_time,
                emp["cashierName"], emp["empId"], f"T-{random.randint(1, 20)}", random.choice(order_types),
                None, fake.sentence(), random.choice(channels), emp["location_id"]
            )
        )
        
        # Insert Line Items
        cursor.executemany(
            """INSERT INTO invoice_items (
                invoiceNumber, itemCode, qty, itemPrice, itemCost, discount, taxValue, itemRemark, 
                itemsDataList, invoiceLineDiscountList, invoiceItemAddonList
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            item_rows
        )

        # Insert Inventory Logs
        cursor.executemany(
            """INSERT INTO inventory_logs (
                itemCode, location_id, change_qty, reason, log_date, log_time
            ) VALUES (%s, %s, %s, %s, %s, %s)""",
            inv_log_rows
        )

    # 6. Seed some Restocks
    print("Seeding restocks...")
    for _ in range(200):
        prod = random.choice(products)
        loc_id = random.choice(locations)[0]
        qty = random.randint(50, 200)
        log_date = start_date + timedelta(days=random.randint(0, 180))
        cursor.execute(
            "INSERT INTO inventory_logs (itemCode, location_id, change_qty, reason, log_date, log_time) VALUES (%s, %s, %s, %s, %s, %s)",
            (prod["itemCode"], loc_id, float(qty), "RESTOCK", log_date.date(), log_date.time())
        )

    conn.commit()
    cursor.close()
    conn.close()
    print("Seeding completed successfully!")

if __name__ == "__main__":
    seed()
