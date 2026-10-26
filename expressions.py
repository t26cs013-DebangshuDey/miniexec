class Expr:
    def bind(self, schema):
        """Resolve column names to positions in schema. Return self."""
        return self

    def eval(self, row):
        raise NotImplementedError

    def referenced_columns(self):
        """Return set of column names referenced by this expression."""
        return set()


def _resolve_col_idx(name, schema):
    if name in schema:
        return schema.index(name)
    # Fallback: match unqualified column name against qualified schema entries
    if "." not in name:
        matches = [i for i, col in enumerate(schema) if col.endswith(f".{name}")]
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f"Column '{name}' not found in schema {schema}")


class Col(Expr):
    def __init__(self, name):
        self.name = name
        self.idx = None

    def bind(self, schema):
        self.idx = _resolve_col_idx(self.name, schema)
        return self

    def eval(self, row):
        return row[self.idx]

    def referenced_columns(self):
        return {self.name}

    def __repr__(self):
        return self.name


class Const(Expr):
    def __init__(self, value):
        self.value = value

    def eval(self, row):
        return self.value

    def __repr__(self):
        return repr(self.value)


class Cmp(Expr):
    OPS = {
        "=": lambda a, b: a == b,
        "<": lambda a, b: a < b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b,
        "!=": lambda a, b: a != b,
    }

    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right

    def bind(self, schema):
        self.left.bind(schema)
        self.right.bind(schema)
        return self

    def eval(self, row):
        return Cmp.OPS[self.op](self.left.eval(row), self.right.eval(row))

    def referenced_columns(self):
        return self.left.referenced_columns() | self.right.referenced_columns()

    def __repr__(self):
        return f"({self.left} {self.op} {self.right})"


class And(Expr):
    def __init__(self, *parts):
        self.parts = list(parts)

    def bind(self, schema):
        for p in self.parts:
            p.bind(schema)
        return self

    def eval(self, row):
        return all(p.eval(row) for p in self.parts)

    def referenced_columns(self):
        cols = set()
        for p in self.parts:
            cols |= p.referenced_columns()
        return cols

    def __repr__(self):
        return " AND ".join(repr(p) for p in self.parts)


class Or(Expr):
    def __init__(self, *parts):
        self.parts = list(parts)

    def bind(self, schema):
        for p in self.parts:
            p.bind(schema)
        return self

    def eval(self, row):
        return any(p.eval(row) for p in self.parts)

    def referenced_columns(self):
        cols = set()
        for p in self.parts:
            cols |= p.referenced_columns()
        return cols

    def __repr__(self):
        return "(" + " OR ".join(repr(p) for p in self.parts) + ")"


class Not(Expr):
    def __init__(self, expr):
        self.expr = expr

    def bind(self, schema):
        self.expr.bind(schema)
        return self

    def eval(self, row):
        return not self.expr.eval(row)

    def referenced_columns(self):
        return self.expr.referenced_columns()

    def __repr__(self):
        return f"NOT ({self.expr})"


class Add(Expr):
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def bind(self, schema):
        self.left.bind(schema)
        self.right.bind(schema)
        return self

    def eval(self, row):
        return self.left.eval(row) + self.right.eval(row)

    def referenced_columns(self):
        return self.left.referenced_columns() | self.right.referenced_columns()

    def __repr__(self):
        return f"({self.left} + {self.right})"


class Sub(Expr):
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def bind(self, schema):
        self.left.bind(schema)
        self.right.bind(schema)
        return self

    def eval(self, row):
        return self.left.eval(row) - self.right.eval(row)

    def referenced_columns(self):
        return self.left.referenced_columns() | self.right.referenced_columns()

    def __repr__(self):
        return f"({self.left} - {self.right})"


class Mul(Expr):
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def bind(self, schema):
        self.left.bind(schema)
        self.right.bind(schema)
        return self

    def eval(self, row):
        return self.left.eval(row) * self.right.eval(row)

    def referenced_columns(self):
        return self.left.referenced_columns() | self.right.referenced_columns()

    def __repr__(self):
        return f"({self.left} * {self.right})"
