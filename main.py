import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="Acme Ledger Agent")

EMAIL = "24f1000574@ds.study.iitm.ac.in"

BASE_URL = (
    "https://exam.sanand.workers.dev/questionData"
    "?email=24f1000574%40ds.study.iitm.ac.in"
    "&quizSign=LiEIKKmBlDzxh%2ByB6Ov34ezshE6eOz9%2FtBu96mmzvNpSVTfOmernNVjP1k8wv1HmQWrQdqG2zvqGhS7IPFkemxWBjWYWuNK85ADXj2LVYCMcWo3DlBDmwul%2FoSWem66NHmrO7%2BwwVOisNS1tPcw9JM1T9Vj6ma9MYucQypnNRZ5%2Fmy6zswiHuPHnDT9C9klg4FPEOHyELXc2Zkbz1Er5hH%2Fiu%2FyoMwbcQ%2BDvkwBtZWhvG6B846nzkn35PCds4hw3GwSMGZXESvZb6cg20nKIeYiFR4hCCLR6TKQpHd0dqX8KZbnf3Y8lyGccrDdvKJUeegGKHdD9%2BEep6jNaFuEd2w%3D%3D"
    "&questionId=q-ledger-agent-server"
)

EXPORT_URL = BASE_URL + "&path=%2Fexport"
RATES_URL = BASE_URL + "&path=%2Frates"

IST = ZoneInfo("Asia/Kolkata")

ORDERS = []
RATES = {
    "USD": 1.0,
    "EUR": 1.11,
    "INR": 0.01245,
}


class Question(BaseModel):
    question: str


def parse_date(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(IST)


def load_data():
    global ORDERS, RATES

    print("Downloading ledger export...")

    with httpx.Client(timeout=30) as client:
        export_response = client.get(EXPORT_URL)
        export_response.raise_for_status()

        rate_response = client.get(RATES_URL)
        rate_response.raise_for_status()

    # NDJSON
    rows = []

    for line in export_response.text.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Keep latest updated_at for duplicate order IDs
    latest = {}

    for row in rows:
        order_id = row["id"]

        if order_id not in latest:
            latest[order_id] = row
        else:
            old_time = parse_date(latest[order_id]["updated_at"])
            new_time = parse_date(row["updated_at"])

            if new_time > old_time:
                latest[order_id] = row

    ORDERS = list(latest.values())

    # Parse rates
    try:
        rate_data = rate_response.json()

        if isinstance(rate_data, dict):
            for currency, value in rate_data.items():
                if isinstance(value, (int, float)):
                    RATES[currency.upper()] = float(value)

            if "rates" in rate_data and isinstance(rate_data["rates"], dict):
                for currency, value in rate_data["rates"].items():
                    if isinstance(value, (int, float)):
                        RATES[currency.upper()] = float(value)

    except Exception:
        pass

    print(f"Loaded {len(rows)} raw rows")
    print(f"Loaded {len(ORDERS)} current orders")
    print(f"Rates: {RATES}")


@app.on_event("startup")
def startup():
    load_data()


def usd_amount(order):
    amount = float(order["amount"])
    currency = order["currency"].upper()

    return amount * RATES.get(currency, 1.0)


def order_date(order):
    return parse_date(order["created_at"])


def in_period(order, year=None, month=None):
    dt = order_date(order)

    if year is not None and dt.year != year:
        return False

    if month is not None and dt.month != month:
        return False

    return True


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def extract_period(question):
    q = question.lower()

    year = None
    month = None

    year_match = re.search(r"\b(20\d{2})\b", q)

    if year_match:
        year = int(year_match.group(1))

    for name, number in MONTHS.items():
        if re.search(r"\b" + name + r"\b", q):
            month = number
            break

    return year, month


def extract_region(question):
    regions = ["north", "south", "east", "west", "central"]

    q = question.lower()

    for region in regions:
        if re.search(r"\b" + region + r"\b", q):
            return region.title()

    return None


def extract_product(question):
    products = sorted(
        {o["product"] for o in ORDERS},
        key=len,
        reverse=True,
    )

    q = question.lower()

    for product in products:
        if product.lower() in q:
            return product

    return None


def extract_customer(question):
    match = re.search(r"\bC\d{4}\b", question.upper())

    if match:
        return match.group(0)

    return None


def filtered_orders(question):
    year, month = extract_period(question)
    region = extract_region(question)
    product = extract_product(question)
    customer = extract_customer(question)

    result = []

    for order in ORDERS:

        if year is not None or month is not None:
            if not in_period(order, year, month):
                continue

        if region and order["region"].lower() != region.lower():
            continue

        if product and order["product"].lower() != product.lower():
            continue

        if customer and order["customer"].upper() != customer.upper():
            continue

        result.append(order)

    return result


def round_money(value):
    return round(value + 1e-9, 2)


def answer_question(question):
    q = question.lower().strip()

    orders = filtered_orders(question)

    # ---------------------------------------------------------
    # Revenue
    # ---------------------------------------------------------

    if "revenue" in q or "sales" in q or "turnover" in q:

        paid = [
            o for o in orders
            if o["status"].lower() == "paid"
        ]

        total = sum(usd_amount(o) for o in paid)

        return round_money(total)

    # ---------------------------------------------------------
    # Refunds
    # ---------------------------------------------------------

    if "refund" in q or "refunded" in q:

        refunded = [
            o for o in orders
            if o["status"].lower() == "refunded"
        ]

        total = sum(usd_amount(o) for o in refunded)

        return round_money(total)

    # ---------------------------------------------------------
    # Number of orders
    # ---------------------------------------------------------

    if (
        "how many orders" in q
        or "number of orders" in q
        or "count of orders" in q
    ):
        return len(orders)

    # ---------------------------------------------------------
    # Quantity / units
    # ---------------------------------------------------------

    if (
        "units" in q
        or "quantity" in q
        or "qty" in q
        or "items sold" in q
    ):
        paid = [
            o for o in orders
            if o["status"].lower() == "paid"
        ]

        return sum(int(o["qty"]) for o in paid)

    # ---------------------------------------------------------
    # Unique customers
    # ---------------------------------------------------------

    if (
        "unique customers" in q
        or "distinct customers" in q
        or "how many customers" in q
    ):
        return len({o["customer"] for o in orders})

    # ---------------------------------------------------------
    # Average order value
    # ---------------------------------------------------------

    if "average" in q:

        paid = [
            o for o in orders
            if o["status"].lower() == "paid"
        ]

        if not paid:
            return 0

        total = sum(usd_amount(o) for o in paid)

        return round_money(total / len(paid))

    # ---------------------------------------------------------
    # Product ranking / best product
    # ---------------------------------------------------------

    if (
        "best product" in q
        or "top product" in q
        or "highest revenue product" in q
        or "most revenue" in q
    ):

        paid = [
            o for o in orders
            if o["status"].lower() == "paid"
        ]

        totals = {}

        for o in paid:
            totals[o["product"]] = (
                totals.get(o["product"], 0)
                + usd_amount(o)
            )

        if not totals:
            return None

        return max(
            totals,
            key=totals.get
        )

    # ---------------------------------------------------------
    # Product revenue / product sales
    # ---------------------------------------------------------

    product = extract_product(question)

    if product:
        paid = [
            o for o in orders
            if o["status"].lower() == "paid"
        ]

        total = sum(usd_amount(o) for o in paid)

        return round_money(total)

    # ---------------------------------------------------------
    # Fallback
    # ---------------------------------------------------------

    return 0


@app.get("/")
def health():
    return {
        "service": "Acme Ledger Agent",
        "orders_loaded": len(ORDERS),
        "status": "ok",
    }


@app.post("/")
@app.post("/answer")
def answer(request: Question):
    result = answer_question(request.question)

    return {
        "answer": result
    }