import unittest
from storage import Table, Catalog
from expressions import Col, Const, Cmp, And
from operators import (
    SeqScan,
    Filter,
    Project,
    Limit,
    Sort,
    Distinct,
    NestedLoopJoin,
    HashJoin,
    SortMergeJoin,
    HashAggregate,
    SortAggregate,
)
from data_gen import build_catalog


class TestMiniExec(unittest.TestCase):
    def setUp(self):
        self.cat = build_catalog(n_customers=100, n_orders=200, n_products=20, seed=42)

    def test_seq_scan_and_filter(self):
        orders_tbl = self.cat.get("orders")
        scan = SeqScan(orders_tbl)
        # amount > 4000
        filt = Filter(scan, Cmp(">", Col("orders.amount"), Const(4000.0)))
        rows = filt.run()
        for r in rows:
            self.assertGreater(r[4], 4000.0)

    def test_limit(self):
        customers_tbl = self.cat.get("customers")
        scan = SeqScan(customers_tbl)
        limit_op = Limit(scan, 5)
        rows = limit_op.run()
        self.assertEqual(len(rows), 5)
        # Verify early termination: child rows_out should equal 5
        self.assertEqual(scan.stats["rows_out"], 5)

    def test_sort(self):
        products_tbl = self.cat.get("products")
        scan = SeqScan(products_tbl)
        sort_op = Sort(scan, [("products.price", "DESC")])
        rows = sort_op.run()
        prices = [r[3] for r in rows]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_distinct(self):
        customers_tbl = self.cat.get("customers")
        scan = SeqScan(customers_tbl)
        proj = Project(scan, ["customers.city"])
        dist = Distinct(proj)
        rows = dist.run()
        cities = [r[0] for r in rows]
        self.assertEqual(len(cities), len(set(cities)))

    def test_join_equivalence(self):
        # Compare NestedLoopJoin, HashJoin, and SortMergeJoin correctness
        c_tbl = self.cat.get("customers")
        o_tbl = self.cat.get("orders")

        def run_join(join_cls):
            left = SeqScan(c_tbl)
            right = SeqScan(o_tbl)
            j = join_cls(left, right, "customers.id", "orders.customer_id")
            return set(j.run())

        res_nlj = run_join(NestedLoopJoin)
        res_hash = run_join(HashJoin)
        res_smj = run_join(SortMergeJoin)

        self.assertEqual(res_nlj, res_hash)
        self.assertEqual(res_nlj, res_smj)

    def test_aggregation(self):
        orders_tbl = self.cat.get("orders")
        scan = SeqScan(orders_tbl)
        agg = HashAggregate(scan, ["orders.customer_id"], [("SUM", "orders.amount", "total_amt")])
        rows = agg.run()
        self.assertGreater(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
