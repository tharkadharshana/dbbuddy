import mysql.connector
import random
import datetime
from decimal import Decimal

def generate_data():
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        database="datamind_db"
    )
    cursor = conn.cursor()

    print("Generating products...")
    categories = ["Electronics", "Clothing", "Home & Garden", "Books", "Toys"]
    products = []
    for i in range(50):
        name = f"Product {i+1}"
        cat = random.choice(categories)
        price = round(random.uniform(10, 1000), 2)
        cursor.execute("INSERT INTO products (name, category, base_price) VALUES (%s, %s, %s)", (name, cat, price))
        products.append(cursor.lastrowid)

    print("Generating customers...")
    countries = ["USA", "UK", "Canada", "Germany", "France", "Japan", "Australia"]
    customers = []
    for i in range(500):
        name = f"Customer {i+1}"
        email = f"customer{i+1}@example.com"
        country = random.choice(countries)
        signup_date = datetime.date(2024, 1, 1) + datetime.timedelta(days=random.randint(0, 365))
        cursor.execute("INSERT INTO customers (name, email, country, signup_date) VALUES (%s, %s, %s, %s)", (name, email, country, signup_date))
        customers.append(cursor.lastrowid)

    print("Generating sales (20,000 rows)...")
    start_date = datetime.date(2024, 1, 1)
    for i in range(20000):
        p_id = random.choice(products)
        c_id = random.choice(customers)
        # Generate some trends and seasonality
        days_offset = random.randint(0, 850) # Approx 2.3 years
        sale_date = start_date + datetime.timedelta(days=days_offset)
        
        qty = random.randint(1, 5)
        # Base price lookup would be slow here, let's just use random for mock
        total = round(qty * random.uniform(10, 500), 2)
        
        # Add some "anomalies" (huge spikes)
        if random.random() < 0.01:
            total *= 10
            
        cursor.execute("INSERT INTO sales (product_id, customer_id, sale_date, quantity, total_amount) VALUES (%s, %s, %s, %s, %s)", 
                       (p_id, c_id, sale_date, qty, total))
        
        if i % 5000 == 0:
            print(f"  Inserted {i} rows...")

    print("Generating web analytics...")
    for i in range(1000):
        event_date = start_date + datetime.timedelta(days=random.randint(0, 850))
        path = random.choice(["/home", "/products", "/cart", "/checkout", "/about"])
        visits = random.randint(100, 5000)
        bounce = round(random.uniform(20, 80), 2)
        duration = random.randint(30, 300)
        cursor.execute("INSERT INTO web_analytics (event_date, page_path, visits, bounce_rate, avg_session_duration) VALUES (%s, %s, %s, %s, %s)",
                       (event_date, path, visits, bounce, duration))

    conn.commit()
    cursor.close()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    generate_data()
