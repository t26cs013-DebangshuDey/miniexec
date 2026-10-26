import bisect


class SortedIndex:
    """Sorted index over a single column using a sorted list of (key, row_pos) pairs."""

    def __init__(self, table, col_name):
        self.table = table
        self.col_name = col_name
        self.col_idx = table.columns.index(col_name)
        self.entries = sorted(
            (row[self.col_idx], idx) for idx, row in enumerate(table.rows)
        )
        self.keys = [e[0] for e in self.entries]

    def lookup_eq(self, key):
        """Return list of row positions where column == key."""
        left = bisect.bisect_left(self.keys, key)
        right = bisect.bisect_right(self.keys, key)
        return [self.entries[i][1] for i in range(left, right)]

    def lookup_range(self, low, high):
        """Return list of row positions where low <= column <= high."""
        left = 0 if low is None else bisect.bisect_left(self.keys, low)
        right = len(self.keys) if high is None else bisect.bisect_right(self.keys, high)
        return [self.entries[i][1] for i in range(left, right)]


class Table:
    def __init__(self, name, columns, rows):
        self.name = name
        self.columns = list(columns)  # e.g. ["id", "name", "city"]
        self.rows = list(rows)        # list of tuples
        self.indexes = {}             # column name -> SortedIndex

    def __len__(self):
        return len(self.rows)

    def create_index(self, col_name):
        idx = SortedIndex(self, col_name)
        self.indexes[col_name] = idx
        return idx

    def column_index(self, col_name):
        if "." in col_name:
            col_name = col_name.split(".", 1)[1]
        return self.columns.index(col_name)


class Catalog:
    def __init__(self):
        self.tables = {}

    def add(self, table):
        self.tables[table.name] = table

    def get(self, name):
        return self.tables[name]
