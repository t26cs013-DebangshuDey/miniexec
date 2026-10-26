import itertools
import math
from expressions import And, Cmp, Col, Const
from operators import (
    Filter,
    HashJoin,
    IndexNestedLoopJoin,
    IndexScan,
    NestedLoopJoin,
    Project,
    SeqScan,
    SortMergeJoin,
)


def split_conjuncts(predicate):
    if isinstance(predicate, And):
        out = []
        for p in predicate.parts:
            out.extend(split_conjuncts(p))
        return out
    return [predicate]


def combine_conjuncts(preds):
    if not preds:
        return None
    if len(preds) == 1:
        return preds[0]
    return And(*preds)


def _tables_in_logical(node):
    op = node[0]
    if op == "scan":
        return {node[1]}
    if op in ("filter", "project"):
        return _tables_in_logical(node[2])
    if op == "join":
        return _tables_in_logical(node[3]) | _tables_in_logical(node[4])
    return set()


def _pred_tables(pred):
    tables = set()
    for col in pred.referenced_columns():
        if "." in col:
            tables.add(col.split(".", 1)[0])
    return tables


def pushdown_predicates(logical_plan, pushed_preds=None):
    if pushed_preds is None:
        pushed_preds = []

    op = logical_plan[0]

    if op == "project":
        _, cols, child = logical_plan
        return ("project", cols, pushdown_predicates(child, pushed_preds))

    if op == "filter":
        _, pred, child = logical_plan
        all_preds = pushed_preds + split_conjuncts(pred)
        return pushdown_predicates(child, all_preds)

    if op == "join":
        _, lk, rk, left, right = logical_plan
        left_tables = _tables_in_logical(left)
        right_tables = _tables_in_logical(right)

        left_preds, right_preds, remain_preds = [], [], []
        for p in pushed_preds:
            pt = _pred_tables(p)
            if pt and pt.issubset(left_tables):
                left_preds.append(p)
            elif pt and pt.issubset(right_tables):
                right_preds.append(p)
            else:
                remain_preds.append(p)

        new_left = pushdown_predicates(left, left_preds)
        new_right = pushdown_predicates(right, right_preds)
        join_node = ("join", lk, rk, new_left, new_right)
        comb = combine_conjuncts(remain_preds)
        return ("filter", comb, join_node) if comb else join_node

    if op == "scan":
        comb = combine_conjuncts(pushed_preds)
        return ("filter", comb, logical_plan) if comb else logical_plan

    return logical_plan


class TableStats:
    def __init__(self, table):
        self.row_count = len(table.rows)
        self.distinct_counts = {}
        for idx, col in enumerate(table.columns):
            vals = {r[idx] for r in table.rows}
            self.distinct_counts[col] = max(1, len(vals))


class Optimizer:
    def __init__(self, catalog):
        self.catalog = catalog
        self.stats = {
            name: TableStats(tbl) for name, tbl in catalog.tables.items()
        }

    def estimate_selectivity(self, pred):
        if isinstance(pred, And):
            sel = 1.0
            for p in pred.parts:
                sel *= self.estimate_selectivity(p)
            return sel
        if isinstance(pred, Cmp):
            cols = list(pred.referenced_columns())
            if pred.op == "=" and len(cols) == 1:
                col_full = cols[0]
                if "." in col_full:
                    tname, cname = col_full.split(".", 1)
                    if tname in self.stats and cname in self.stats[tname].distinct_counts:
                        return 1.0 / self.stats[tname].distinct_counts[cname]
                return 0.1
            if pred.op in ("<", ">", "<=", ">="):
                return 1.0 / 3.0
        return 0.5

    def estimate_cardinality(self, logical_node):
        op = logical_node[0]
        if op == "scan":
            tname = logical_node[1]
            return float(self.stats[tname].row_count)
        if op == "filter":
            _, pred, child = logical_node
            return max(1.0, self.estimate_cardinality(child) * self.estimate_selectivity(pred))
        if op == "project":
            return self.estimate_cardinality(logical_node[2])
        if op == "join":
            _, lk, rk, left, right = logical_node
            n_left = self.estimate_cardinality(left)
            n_right = self.estimate_cardinality(right)
            lt, lc = lk.split(".", 1)
            rt, rc = rk.split(".", 1)
            d_left = self.stats[lt].distinct_counts.get(lc, 100)
            d_right = self.stats[rt].distinct_counts.get(rc, 100)
            return max(1.0, (n_left * n_right) / max(d_left, d_right, 1))
        return 100.0

    def join_cost(self, algo, n_left, n_right):
        n_left = max(1.0, n_left)
        n_right = max(1.0, n_right)
        if algo == "nlj":
            return n_left * n_right
        if algo == "hash":
            return n_left + 1.2 * n_right
        if algo == "smj":
            return (
                n_left * math.log2(max(2.0, n_left))
                + n_right * math.log2(max(2.0, n_right))
                + n_left
                + n_right
            )
        if algo == "inlj":
            return n_left * math.log2(max(2.0, n_right))
        return float("inf")

    def choose_join_algorithm(self, left_logical, right_logical, lk, rk):
        n_left = self.estimate_cardinality(left_logical)
        n_right = self.estimate_cardinality(right_logical)

        rt, rc = rk.split(".", 1)
        right_table = self.catalog.get(rt)
        has_index = right_logical[0] == "scan" and rc in right_table.indexes

        candidates = ["nlj", "hash", "smj"]
        if has_index and n_left <= 0.1 * n_right:
            candidates.append("inlj")

        best_algo = min(candidates, key=lambda a: self.join_cost(a, n_left, n_right))
        return best_algo, self.join_cost(best_algo, n_left, n_right)

    def build_physical(self, logical_node, force_join_algo=None):
        op = logical_node[0]
        if op == "scan":
            tbl = self.catalog.get(logical_node[1])
            return SeqScan(tbl)
        if op == "filter":
            _, pred, child = logical_node
            return Filter(self.build_physical(child, force_join_algo), pred)
        if op == "project":
            _, cols, child = logical_node
            return Project(self.build_physical(child, force_join_algo), cols)
        if op == "join":
            _, lk, rk, left, right = logical_node
            phys_left = self.build_physical(left, force_join_algo)
            phys_right = self.build_physical(right, force_join_algo)
            algo = force_join_algo or self.choose_join_algorithm(left, right, lk, rk)[0]
            if algo == "nlj":
                return NestedLoopJoin(phys_left, phys_right, lk, rk)
            if algo == "smj":
                return SortMergeJoin(phys_left, phys_right, lk, rk)
            if algo == "inlj":
                return IndexNestedLoopJoin(phys_left, phys_right, lk, rk)
            return HashJoin(phys_left, phys_right, lk, rk)
        raise ValueError(f"Unknown logical node: {op}")

    def optimize(self, logical_plan, enable_pushdown=True, force_join_algo=None):
        plan = pushdown_predicates(logical_plan) if enable_pushdown else logical_plan
        return self.build_physical(plan, force_join_algo=force_join_algo)
