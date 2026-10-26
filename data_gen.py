import random
from storage import Table, Catalog


def build_catalog(n_customers=5000, n_orders=5000, n_products=500, seed=42):
    random.seed(seed)
    cities = ["Imphal", "Delhi", "Mumbai", "Chennai", "Kolkata"]
    customers = [
        (i, f"Customer{i}", random.choice(cities)) for i in range(n_customers)
    ]
    products = [
        (
            i,
            f"Product{i}",
            random.choice(["Books", "Toys", "Grocery"]),
            round(random.uniform(10, 2000), 2),
        )
        for i in range(n_products)
    ]
    orders = [
        (
            i,
            random.randrange(n_customers),
            random.randrange(n_products),
            random.randint(1, 10),
            round(random.uniform(10, 5000), 2),
        )
        for i in range(n_orders)
    ]

    cat = Catalog()
    cat.add(Table("customers", ["id", "name", "city"], customers))
    cat.add(Table("products", ["id", "name", "category", "price"], products))
    cat.add(Table("orders", ["id", "customer_id", "product_id", "qty", "amount"], orders))
    return cat
