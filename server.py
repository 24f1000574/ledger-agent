import os
import json
import sqlite3
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from openai import AsyncOpenAI
from datetime import datetime
import pytz

app = FastAPI()

# Configuration
DATA_URL = "https://exam.sanand.workers.dev/questionData?email=24f1000574%40ds.study.iitm.ac.in&quizSign=LiEIKKmBlDzxh%2ByB6Ov34ezshE6eOz9%2FtBu96mmzvNpSVTfOmernNVjP1k8wv1HmQWrQdqG2zvqGhS7IPFkemxWBjWYWuNK85ADXj2LVYCMcWo3DlBDmwul%2FoSWem66NHmrO7%2BwwVOisNS1tPcw9JM1T9Vj6ma9MYucQypnNRZ5%2Fmy6zswiHuPHnDT9C9klg4FPEOHyELXc2Zkbz1Er5hH%2Fiu%2FyoMwbcQ%2BDvkwBtZWhvG6B846nzkn35PCds4hw3GwSMGZXESvZb6cg20nKIeYiFR4hCCLR6TKQpHd0dqX8KZbnf3Y8lyGccrDdvKJUeegGKHdD9%2BEep6jNaFuEd2w%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"

# We can accept an AIPROXY_TOKEN or OPENAI_API_KEY
api_key = os.getenv("AIPROXY_TOKEN") or os.getenv("OPENAI_API_KEY")
base_url = "https://aipipe.org/openai/v1" if os.getenv("AIPROXY_TOKEN") else None
client = AsyncOpenAI(api_key=api_key, base_url=base_url)

# Setup SQLite Database
conn = sqlite3.connect(":memory:", check_same_thread=False)
cursor = conn.cursor()

def setup_db():
    print("Downloading data...")
    response = httpx.get(DATA_URL, timeout=30.0)
    lines = response.text.strip().split('\n')
    
    # Track latest updated_at for each order id
    orders = {}
    for line in lines:
        if not line.strip() or line.startswith('---') or line.startswith('Source:'):
            continue
        try:
            order = json.loads(line)
        except json.JSONDecodeError:
            continue
            
        order_id = order['id']
        if order_id not in orders:
            orders[order_id] = order
        else:
            # Parse dates to compare
            curr_updated = datetime.fromisoformat(orders[order_id]['updated_at'].replace('Z', '+00:00'))
            new_updated = datetime.fromisoformat(order['updated_at'].replace('Z', '+00:00'))
            if new_updated > curr_updated:
                orders[order_id] = order

    # Filter paid orders
    valid_orders = [o for o in orders.values() if o['status'] == 'paid']
    
    # Create table
    cursor.execute('''
        CREATE TABLE ledger (
            id TEXT PRIMARY KEY,
            customer TEXT,
            region TEXT,
            product TEXT,
            qty INTEGER,
            unit_price REAL,
            amount REAL,
            currency TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            month TEXT,
            year TEXT
        )
    ''')
    
    # Insert data
    tz = pytz.timezone('Asia/Kolkata')
    for o in valid_orders:
        # Convert created_at to Asia/Kolkata date to easily query by month/year
        dt = datetime.fromisoformat(o['created_at'].replace('Z', '+00:00')).astimezone(tz)
        month = dt.strftime('%B')
        year = dt.strftime('%Y')
        
        cursor.execute('''
            INSERT INTO ledger (id, customer, region, product, qty, unit_price, amount, currency, status, created_at, updated_at, month, year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            o['id'], o['customer'], o['region'], o['product'], o['qty'],
            o['unit_price'], o['amount'], o['currency'], o['status'],
            o['created_at'], o['updated_at'], month, year
        ))
    
    conn.commit()
    print(f"Loaded {len(valid_orders)} valid paid orders into memory.")

setup_db()

class QuestionRequest(BaseModel):
    question: str

@app.get("/{path:path}")
async def health_check():
    return {"status": "ok", "message": "Please send a POST request with {'question': '...'} to this endpoint."}

@app.post("/{path:path}")
async def ask_question(req: QuestionRequest):
    if not api_key:
        raise HTTPException(status_code=500, detail="API key is missing")
    
    # Generate SQL
    prompt = f"""
You are an expert SQLite data analyst.
We have a table named `ledger` with the following columns:
- id (TEXT)
- customer (TEXT)
- region (TEXT)
- product (TEXT)
- qty (INTEGER)
- unit_price (REAL)
- amount (REAL)
- currency (TEXT)
- status (TEXT) - all are 'paid'
- created_at (TEXT) - ISO date
- updated_at (TEXT) - ISO date
- month (TEXT) - Full month name in Asia/Kolkata timezone (e.g., 'March')
- year (TEXT) - 4-digit year in Asia/Kolkata timezone (e.g., '2026')

Note: "revenue" usually means the sum of `amount`. If asking for total revenue in USD, check `currency = 'USD'`.
Write a valid SQLite query to answer the user's question.
Return ONLY the SQL query, nothing else, no markdown formatting.

Question: {req.question}
"""
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        sql = completion.choices[0].message.content.strip()
        # Remove any markdown code blocks if the LLM added them
        if sql.startswith("```sql"):
            sql = sql[6:]
        if sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]
        sql = sql.strip()
        
        cursor.execute(sql)
        result = cursor.fetchone()
        
        # Format answer appropriately (if it's a number, return the number)
        answer = result[0] if result else None
        
        # Round money to cent if it's a float
        if isinstance(answer, float):
            answer = round(answer, 2)
            
        return {"answer": answer}
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
