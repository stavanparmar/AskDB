"""Create and seed the local sample SQLite database."""

import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine, URL


DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "askdb.sqlite3"
metadata = MetaData()

customers = Table(
    "customers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("email", String, nullable=False, unique=True),
    Column("city", String, nullable=False),
    Column("signup_date", Date, nullable=False),
)
products = Table(
    "products",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("category", String, nullable=False),
    Column("price", Numeric(10, 2), nullable=False),
)
orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("product_id", ForeignKey("products.id"), nullable=False),
    Column("order_date", Date, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Numeric(10, 2), nullable=False),
    Column("total", Numeric(10, 2), nullable=False),
)

_FIRST_NAMES = (
    "Avery", "Blake", "Casey", "Drew", "Elliot", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Logan", "Morgan", "Noel", "Oakley", "Parker",
    "Quinn", "Riley", "Sage", "Taylor",
)
_LAST_NAMES = (
    "Adams", "Bennett", "Carter", "Diaz", "Ellis", "Foster", "Garcia",
    "Hayes", "Ivanov", "James", "Kim", "Lopez", "Miller", "Nguyen", "Owens",
    "Patel", "Reed", "Singh", "Turner", "Wong",
)
_CITIES = (
    "Austin", "Boston", "Chicago", "Denver", "Miami", "Portland", "Seattle",
    "Atlanta", "Phoenix", "San Diego",
)
_PRODUCTS = (
    ("Wireless Keyboard", "Electronics", 64.99),
    ("Noise-Canceling Headphones", "Electronics", 149.50),
    ("USB-C Hub", "Electronics", 39.95),
    ("Smart Desk Lamp", "Home", 54.00),
    ("Ceramic Mug Set", "Home", 28.75),
    ("Cotton Throw Blanket", "Home", 72.00),
    ("Trail Running Shoes", "Sports", 118.00),
    ("Yoga Mat", "Sports", 34.99),
    ("Insulated Water Bottle", "Sports", 26.50),
    ("Cookbook Stand", "Kitchen", 31.25),
    ("Cast-Iron Pan", "Kitchen", 58.00),
    ("Espresso Grinder", "Kitchen", 129.99),
    ("Plant Starter Kit", "Garden", 42.00),
    ("Pruning Shears", "Garden", 19.95),
    ("Canvas Tote", "Accessories", 18.00),
    ("Leather Card Holder", "Accessories", 44.50),
    ("Mechanical Pencil Set", "Office", 16.75),
    ("Desk Organizer", "Office", 33.00),
    ("Paperback Journal", "Office", 12.99),
    ("Travel Backpack", "Accessories", 89.00),
)


def create_database_engine(database_path: Path = DATABASE_PATH) -> Engine:
    """Create the writable SQLAlchemy engine used for database initialization."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite", database=str(database_path.resolve()))
    return create_engine(url)


def _customer_rows(rng: random.Random) -> list[dict[str, object]]:
    """Generate 100 distinct customer records."""
    rows = []
    for index in range(100):
        first = _FIRST_NAMES[index % len(_FIRST_NAMES)]
        last = _LAST_NAMES[index // len(_FIRST_NAMES)]
        rows.append(
            {
                "id": index + 1,
                "name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}{index + 1}@example.com",
                "city": rng.choice(_CITIES),
                "signup_date": date(2022, 1, 1) + timedelta(days=rng.randrange(1400)),
            }
        )
    return rows


def _product_rows() -> list[dict[str, object]]:
    """Convert the sample product catalog into database records."""
    return [
        {
            "id": index,
            "name": name,
            "category": category,
            "price": price,
        }
        for index, (name, category, price) in enumerate(_PRODUCTS, start=1)
    ]


def _order_rows(
    rng: random.Random,
    product_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Generate 380 orders linked to seeded customers and products."""
    rows = []
    for order_id in range(1, 381):
        product = rng.choice(product_rows)
        quantity = rng.randint(1, 5)
        unit_price = float(product["price"])
        rows.append(
            {
                "id": order_id,
                "customer_id": rng.randint(1, 100),
                "product_id": product["id"],
                "order_date": date.today() - timedelta(days=rng.randrange(730)),
                "quantity": quantity,
                "unit_price": unit_price,
                "total": round(unit_price * quantity, 2),
            }
        )
    return rows


def seed_database(engine: Engine) -> None:
    """Create tables and seed the database once with exactly 500 records."""
    metadata.create_all(engine)
    with engine.begin() as connection:
        row_counts = [
            connection.scalar(select(func.count()).select_from(table)) or 0
            for table in (customers, products, orders)
        ]
        if any(row_counts):
            return

        rng = random.Random(42)
        customer_rows = _customer_rows(rng)
        product_rows = _product_rows()
        connection.execute(customers.insert(), customer_rows)
        connection.execute(products.insert(), product_rows)
        connection.execute(
            orders.insert(),
            _order_rows(rng, product_rows),
        )
