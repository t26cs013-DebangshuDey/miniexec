import time
import matplotlib.pyplot as plt
from data_gen import build_catalog
from operators import SeqScan, HashJoin, SortMergeJoin, NestedLoopJoin
from optimizer import Optimizer


def run_benchmarks():
    print("Running benchmarks...")
    cat = build_catalog(n_customers=1000, n_orders=5000, n_products=100)
    optimizer = Optimizer(cat)

    # Simple join scaling benchmark
    sizes = [1000, 2000, 5000]
    times_hash = []
    times_smj = []

    for size in sizes:
        sub_orders = cat.get("orders")
        sub_orders.rows = sub_orders.rows[:size]

        left = SeqScan(cat.get("customers"))
        right = SeqScan(sub_orders)
        hj = HashJoin(left, right, "customers.id", "orders.customer_id")
        
        t0 = time.perf_counter()
        hj.run()
        times_hash.append((time.perf_counter() - t0) * 1000)

        left = SeqScan(cat.get("customers"))
        right = SeqScan(sub_orders)
        smj = SortMergeJoin(left, right, "customers.id", "orders.customer_id")
        
        t0 = time.perf_counter()
        smj.run()
        times_smj.append((time.perf_counter() - t0) * 1000)

    print("Join Benchmark Results:")
    print(f"Sizes: {sizes}")
    print(f"HashJoin times (ms): {times_hash}")
    print(f"SortMergeJoin times (ms): {times_smj}")


if __name__ == "__main__":
    run_benchmarks()
