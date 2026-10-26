from functools import cmp_to_key
from expressions import Col, Expr, _resolve_col_idx


class Operator:
    def __init__(self):
        self.schema = []  # list of output column names
        self.stats = {"rows_out": 0, "time_s": 0.0}

    def open(self):
        raise NotImplementedError

    def next(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def children(self):
        return []

    def describe(self):
        return self.__class__.__name__

    def run(self):
        """Drive the operator to completion and return all rows."""
        out = []
        self.open()
        while (row := self.next()) is not None:
            out.append(row)
        self.close()
        return out


# ==============================================================================
# Base & Part 1: Core Operators
# ==============================================================================

class SeqScan(Operator):
    def __init__(self, table, alias=None):
        super().__init__()
        self.table = table
        self.alias = alias
        prefix = alias or table.name
        self.schema = [f"{prefix}.{c}" for c in table.columns]
        self.pos = 0

    def open(self):
        self.pos = 0

    def next(self):
        if self.pos >= len(self.table.rows):
            return None
        row = self.table.rows[self.pos]
        self.pos += 1
        self.stats["rows_out"] += 1
        return row

    def close(self):
        pass

    def describe(self):
        return f"SeqScan({self.table.name})"


class Filter(Operator):
    def __init__(self, child, predicate):
        super().__init__()
        self.child = child
        self.schema = list(child.schema)
        self.predicate = predicate.bind(self.schema)

    def open(self):
        self.child.open()

    def next(self):
        while (row := self.child.next()) is not None:
            if self.predicate.eval(row):
                self.stats["rows_out"] += 1
                return row
        return None

    def close(self):
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        return f"Filter({self.predicate})"


class Project(Operator):
    def __init__(self, child, columns):
        super().__init__()
        self.child = child
        self.exprs = []
        self.schema = []

        for item in columns:
            if isinstance(item, tuple):
                col_or_expr, alias = item
                expr = Col(col_or_expr) if isinstance(col_or_expr, str) else col_or_expr
                expr.bind(child.schema)
                self.exprs.append(expr)
                self.schema.append(alias)
            elif isinstance(item, str):
                expr = Col(item).bind(child.schema)
                self.exprs.append(expr)
                self.schema.append(child.schema[expr.idx] if item in child.schema else item)
            elif isinstance(item, Expr):
                item.bind(child.schema)
                self.exprs.append(item)
                self.schema.append(repr(item))
            else:
                raise ValueError(f"Unsupported projection specification: {item}")

    def open(self):
        self.child.open()

    def next(self):
        row = self.child.next()
        if row is None:
            return None
        self.stats["rows_out"] += 1
        return tuple(expr.eval(row) for expr in self.exprs)

    def close(self):
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        return f"Project({', '.join(self.schema)})"


class Limit(Operator):
    def __init__(self, child, n):
        super().__init__()
        self.child = child
        self.n = n
        self.schema = list(child.schema)
        self.count = 0

    def open(self):
        self.count = 0
        self.child.open()

    def next(self):
        if self.count >= self.n:
            return None
        row = self.child.next()
        if row is None:
            return None
        self.count += 1
        self.stats["rows_out"] += 1
        return row

    def close(self):
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        return f"Limit({self.n})"


class Sort(Operator):
    def __init__(self, child, keys):
        super().__init__()
        self.child = child
        self.schema = list(child.schema)
        self.keys = keys
        self.sort_specs = []

        for k in keys:
            if isinstance(k, tuple):
                col_name, order = k
                if isinstance(order, str):
                    desc = order.upper() == "DESC"
                else:
                    desc = not bool(order)
            else:
                col_name, desc = k, False
            idx = _resolve_col_idx(col_name, self.schema)
            self.sort_specs.append((idx, desc, col_name))

        self.buffer = []
        self.pos = 0

    def open(self):
        self.child.open()
        rows = []
        while (row := self.child.next()) is not None:
            rows.append(row)

        def compare(a, b):
            for idx, desc, _ in self.sort_specs:
                va, vb = a[idx], b[idx]
                if va == vb:
                    continue
                if va is None:
                    res = -1
                elif vb is None:
                    res = 1
                else:
                    res = -1 if va < vb else 1
                return -res if desc else res
            return 0

        rows.sort(key=cmp_to_key(compare))
        self.buffer = rows
        self.pos = 0

    def next(self):
        if self.pos >= len(self.buffer):
            return None
        row = self.buffer[self.pos]
        self.pos += 1
        self.stats["rows_out"] += 1
        return row

    def close(self):
        self.buffer = []
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        desc_parts = [f"{col} {'DESC' if desc else 'ASC'}" for _, desc, col in self.sort_specs]
        return f"Sort({', '.join(desc_parts)})"


class Distinct(Operator):
    def __init__(self, child):
        super().__init__()
        self.child = child
        self.schema = list(child.schema)
        self.seen = set()

    def open(self):
        self.seen = set()
        self.child.open()

    def next(self):
        while (row := self.child.next()) is not None:
            if row not in self.seen:
                self.seen.add(row)
                self.stats["rows_out"] += 1
                return row
        return None

    def close(self):
        self.seen.clear()
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        return "Distinct()"


# ==============================================================================
# Part 2: Join Algorithms
# ==============================================================================

class NestedLoopJoin(Operator):
    def __init__(self, left, right, left_key, right_key):
        super().__init__()
        self.left = left
        self.right = right
        self.left_key = left_key
        self.right_key = right_key
        self.lk = _resolve_col_idx(left_key, left.schema)
        self.rk = _resolve_col_idx(right_key, right.schema)
        self.schema = list(left.schema) + list(right.schema)
        self.current_left = None
        self.right_open = False

    def open(self):
        self.left.open()
        self.current_left = self.left.next()
        if self.current_left is not None:
            self.right.open()
            self.right_open = True
        else:
            self.right_open = False

    def next(self):
        while self.current_left is not None:
            right_row = self.right.next()
            if right_row is None:
                self.right.close()
                self.right_open = False
                self.current_left = self.left.next()
                if self.current_left is not None:
                    self.right.open()
                    self.right_open = True
                continue
            if self.current_left[self.lk] == right_row[self.rk]:
                self.stats["rows_out"] += 1
                return self.current_left + right_row
        return None

    def close(self):
        if self.right_open:
            self.right.close()
            self.right_open = False
        self.left.close()

    def children(self):
        return [self.left, self.right]

    def describe(self):
        return f"NestedLoopJoin({self.left_key} = {self.right_key})"


class HashJoin(Operator):
    def __init__(self, left, right, left_key, right_key):
        super().__init__()
        self.left = left
        self.right = right
        self.left_key = left_key
        self.right_key = right_key
        self.lk = _resolve_col_idx(left_key, left.schema)
        self.rk = _resolve_col_idx(right_key, right.schema)
        self.schema = list(left.schema) + list(right.schema)
        self.table = {}
        self.pending = []

    def open(self):
        self.table = {}
        self.pending = []
        self.right.open()
        while (r := self.right.next()) is not None:
            key = r[self.rk]
            self.table.setdefault(key, []).append(r)
        self.left.open()

    def next(self):
        while True:
            if self.pending:
                self.stats["rows_out"] += 1
                return self.pending.pop()
            left_row = self.left.next()
            if left_row is None:
                return None
            matches = self.table.get(left_row[self.lk], [])
            if matches:
                self.pending = [left_row + r for r in reversed(matches)]

    def close(self):
        self.table.clear()
        self.pending.clear()
        self.left.close()
        self.right.close()

    def children(self):
        return [self.left, self.right]

    def describe(self):
        return f"HashJoin({self.left_key} = {self.right_key})"


class SortMergeJoin(Operator):
    def __init__(self, left, right, left_key, right_key):
        super().__init__()
        self.left = left
        self.right = right
        self.left_key = left_key
        self.right_key = right_key
        self.lk = _resolve_col_idx(left_key, left.schema)
        self.rk = _resolve_col_idx(right_key, right.schema)
        self.schema = list(left.schema) + list(right.schema)
        self.left_rows = []
        self.right_rows = []
        self.i = 0
        self.j = 0
        self.pending = []

    def open(self):
        self.left.open()
        self.left_rows = []
        while (r := self.left.next()) is not None:
            self.left_rows.append(r)
        self.left_rows.sort(key=lambda r: r[self.lk])

        self.right.open()
        self.right_rows = []
        while (r := self.right.next()) is not None:
            self.right_rows.append(r)
        self.right_rows.sort(key=lambda r: r[self.rk])

        self.i = 0
        self.j = 0
        self.pending = []

    def next(self):
        while True:
            if self.pending:
                self.stats["rows_out"] += 1
                return self.pending.pop()

            if self.i >= len(self.left_rows) or self.j >= len(self.right_rows):
                return None

            lk_val = self.left_rows[self.i][self.lk]
            rk_val = self.right_rows[self.j][self.rk]

            if lk_val < rk_val:
                self.i += 1
            elif lk_val > rk_val:
                self.j += 1
            else:
                key = lk_val
                left_run = []
                while self.i < len(self.left_rows) and self.left_rows[self.i][self.lk] == key:
                    left_run.append(self.left_rows[self.i])
                    self.i += 1
                right_run = []
                while self.j < len(self.right_rows) and self.right_rows[self.j][self.rk] == key:
                    right_run.append(self.right_rows[self.j])
                    self.j += 1

                pairs = [l_row + r_row for l_row in left_run for r_row in right_run]
                self.pending = list(reversed(pairs))

    def close(self):
        self.left_rows = []
        self.right_rows = []
        self.pending = []
        self.left.close()
        self.right.close()

    def children(self):
        return [self.left, self.right]

    def describe(self):
        return f"SortMergeJoin({self.left_key} = {self.right_key})"


# ==============================================================================
# Part 3: Aggregation
# ==============================================================================

def _init_agg_states(aggs):
    states = []
    for func, _, _ in aggs:
        f = func.upper()
        if f == "COUNT":
            states.append(0)
        elif f in ("SUM", "MIN", "MAX"):
            states.append(None)
        elif f == "AVG":
            states.append([0, 0])
        else:
            raise ValueError(f"Unknown aggregate function: {func}")
    return states


def _update_agg_states(states, agg_specs, row):
    for i, (func, col_idx, _) in enumerate(agg_specs):
        val = None if col_idx is None else row[col_idx]
        if func == "COUNT":
            if col_idx is None or val is not None:
                states[i] += 1
        elif func == "SUM":
            if val is not None:
                states[i] = val if states[i] is None else states[i] + val
        elif func == "MIN":
            if val is not None:
                states[i] = val if states[i] is None or val < states[i] else states[i]
        elif func == "MAX":
            if val is not None:
                states[i] = val if states[i] is None or val > states[i] else states[i]
        elif func == "AVG":
            if val is not None:
                states[i][0] += val
                states[i][1] += 1


def _finalize_agg_states(states, agg_specs):
    out = []
    for state, (func, _, _) in zip(states, agg_specs):
        if func == "AVG":
            total, cnt = state
            out.append(None if cnt == 0 else total / cnt)
        else:
            out.append(state)
    return tuple(out)


class HashAggregate(Operator):
    def __init__(self, child, group_cols, aggs):
        super().__init__()
        self.child = child
        self.group_cols = list(group_cols) if group_cols else []
        self.aggs = list(aggs)
        self.group_idxs = [_resolve_col_idx(c, child.schema) for c in self.group_cols]

        self.agg_specs = []
        for func, col, out_name in self.aggs:
            col_idx = None if col in ("*", None) else _resolve_col_idx(col, child.schema)
            self.agg_specs.append((func.upper(), col_idx, out_name))

        self.schema = list(self.group_cols) + [out_name for _, _, out_name in self.aggs]
        self.results = []
        self.pos = 0

    def open(self):
        self.child.open()
        groups = {}

        if not self.group_idxs:
            groups[()] = _init_agg_states(self.aggs)

        while (row := self.child.next()) is not None:
            g_key = tuple(row[idx] for idx in self.group_idxs)
            if g_key not in groups:
                groups[g_key] = _init_agg_states(self.aggs)
            _update_agg_states(groups[g_key], self.agg_specs, row)

        self.results = [
            g_key + _finalize_agg_states(state, self.agg_specs)
            for g_key, state in groups.items()
        ]
        self.pos = 0

    def next(self):
        if self.pos >= len(self.results):
            return None
        row = self.results[self.pos]
        self.pos += 1
        self.stats["rows_out"] += 1
        return row

    def close(self):
        self.results = []
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        agg_str = ", ".join(f"{f}({c})" for f, c, _ in self.aggs)
        return f"HashAggregate(group={self.group_cols}, agg=[{agg_str}])"


class SortAggregate(Operator):
    def __init__(self, child, group_cols, aggs):
        super().__init__()
        self.child = child
        self.group_cols = list(group_cols) if group_cols else []
        self.aggs = list(aggs)
        self.group_idxs = [_resolve_col_idx(c, child.schema) for c in self.group_cols]

        self.agg_specs = []
        for func, col, out_name in self.aggs:
            col_idx = None if col in ("*", None) else _resolve_col_idx(col, child.schema)
            self.agg_specs.append((func.upper(), col_idx, out_name))

        self.schema = list(self.group_cols) + [out_name for _, _, out_name in self.aggs]
        self.current_key = None
        self.current_state = None
        self.exhausted = False
        self.global_emitted = False

    def open(self):
        self.child.open()
        self.current_key = None
        self.current_state = None
        self.exhausted = False
        self.global_emitted = False

    def next(self):
        if not self.group_idxs:
            if self.global_emitted:
                return None
            state = _init_agg_states(self.aggs)
            while (row := self.child.next()) is not None:
                _update_agg_states(state, self.agg_specs, row)
            self.global_emitted = True
            self.stats["rows_out"] += 1
            return _finalize_agg_states(state, self.agg_specs)

        if self.exhausted:
            return None

        while True:
            row = self.child.next()
            if row is None:
                self.exhausted = True
                if self.current_key is None:
                    return None
                res = self.current_key + _finalize_agg_states(self.current_state, self.agg_specs)
                self.current_key = None
                self.stats["rows_out"] += 1
                return res

            g_key = tuple(row[idx] for idx in self.group_idxs)
            if self.current_key is None:
                self.current_key = g_key
                self.current_state = _init_agg_states(self.aggs)
                _update_agg_states(self.current_state, self.agg_specs, row)
            elif g_key == self.current_key:
                _update_agg_states(self.current_state, self.agg_specs, row)
            else:
                res = self.current_key + _finalize_agg_states(self.current_state, self.agg_specs)
                self.current_key = g_key
                self.current_state = _init_agg_states(self.aggs)
                _update_agg_states(self.current_state, self.agg_specs, row)
                self.stats["rows_out"] += 1
                return res

    def close(self):
        self.current_key = None
        self.current_state = None
        self.child.close()

    def children(self):
        return [self.child]

    def describe(self):
        agg_str = ", ".join(f"{f}({c})" for f, c, _ in self.aggs)
        return f"SortAggregate(group={self.group_cols}, agg=[{agg_str}])"


# ==============================================================================
# Part 4: Index Access Paths & Index Join
# ==============================================================================

class IndexScan(Operator):
    def __init__(self, table, index, low, high, alias=None):
        super().__init__()
        self.table = table
        self.index = index
        self.low = low
        self.high = high
        prefix = alias or table.name
        self.schema = [f"{prefix}.{c}" for c in table.columns]
        self.row_positions = []
        self.pos = 0

    def open(self):
        if self.low is not None and self.low == self.high:
            self.row_positions = self.index.lookup_eq(self.low)
        else:
            self.row_positions = self.index.lookup_range(self.low, self.high)
        self.pos = 0

    def next(self):
        if self.pos >= len(self.row_positions):
            return None
        row_idx = self.row_positions[self.pos]
        self.pos += 1
        self.stats["rows_out"] += 1
        return self.table.rows[row_idx]

    def close(self):
        self.row_positions = []

    def describe(self):
        return f"IndexScan({self.table.name}.{self.index.col_name} [{self.low}, {self.high}])"


class IndexNestedLoopJoin(Operator):
    def __init__(self, left, right, left_key, right_key, index=None):
        super().__init__()
        self.left = left
        if isinstance(right, SeqScan):
            self.right_table = right.table
            right_schema = right.schema
            self.right_child = right
        else:
            self.right_table = right
            right_schema = [f"{right.name}.{c}" for c in right.columns]
            self.right_child = SeqScan(right)

        self.left_key = left_key
        self.right_key = right_key
        self.lk = _resolve_col_idx(left_key, left.schema)

        raw_col = right_key.split(".", 1)[1] if "." in right_key else right_key
        if index is not None:
            self.index = index
        elif raw_col in self.right_table.indexes:
            self.index = self.right_table.indexes[raw_col]
        else:
            self.index = self.right_table.create_index(raw_col)

        self.schema = list(left.schema) + list(right_schema)
        self.pending = []

    def open(self):
        self.left.open()
        self.pending = []

    def next(self):
        while True:
            if self.pending:
                self.stats["rows_out"] += 1
                return self.pending.pop()
            left_row = self.left.next()
            if left_row is None:
                return None
            key = left_row[self.lk]
            positions = self.index.lookup_eq(key)
            if positions:
                self.right_child.stats["rows_out"] += len(positions)
                matches = [left_row + self.right_table.rows[p] for p in positions]
                self.pending = list(reversed(matches))

    def close(self):
        self.pending = []
        self.left.close()

    def children(self):
        return [self.left, self.right_child]

    def describe(self):
        return f"IndexNestedLoopJoin({self.left_key} = {self.right_key})"