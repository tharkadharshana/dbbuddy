import os
import mysql.connector
import pandas as pd
from analytics import run_forecast
import matplotlib.pyplot as plt
from dotenv import load_dotenv

load_dotenv()

def get_db_data():
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database="datamind_db"
    )
    cursor = conn.cursor()
    
    # Query daily revenue
    query = """
    SELECT invoiceDate, SUM(invoiceTotal) 
    FROM invoices 
    GROUP BY invoiceDate 
    ORDER BY invoiceDate
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return rows

def main():
    print("Fetching historical data...")
    rows = get_db_data()
    
    print(f"Total historical days: {len(rows)}")
    
    print("Running Prophet forecast for next 30 days...")
    result = run_forecast(rows, periods=30)
    
    print("\n--- FORECAST SUMMARY ---")
    forecast = result["forecast"]
    for i in range(min(5, len(forecast))):
        f = forecast[i]
        print(f"Date: {f['date']}, Predicted Revenue: {f['yhat']}")
    
    print("\n--- WEEKLY SEASONALITY ---")
    for day, effect in result["weekly_seasonality"].items():
        print(f"{day}: {effect}")

    # Generate a simple plot if possible (or just log results)
    # Since I can't show plots directly in the terminal easily, I'll just report success.
    print("\nPrediction successful! The system can now predict revenue patterns.")

if __name__ == "__main__":
    main()
