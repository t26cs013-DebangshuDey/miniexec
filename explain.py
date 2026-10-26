import time


def explain(plan, indent=0):
    """Return formatted plan tree string with indentation."""
    lines = ["  " * indent + plan.describe()]
    for child in plan.children():
        lines.append(explain(child, indent + 1))
    res = "\n".join(lines)
    if indent == 0:
        print(res)
    return res


def _instrument_tree(node):
    if getattr(node, "_is_instrumented", False):
        node.stats["rows_out"] = 0
        node.stats["time_s"] = 0.0
        node._inclusive_s = 0.0
        for c in node.children():
            _instrument_tree(c)
        return

    orig_open = node.open
    orig_next = node.next
    orig_close = node.close

    node.stats["rows_out"] = 0
    node.stats["time_s"] = 0.0
    node._inclusive_s = 0.0

    def timed_open():
        t0 = time.perf_counter()
        res = orig_open()
        node._inclusive_s += time.perf_counter() - t0
        return res

    def timed_next():
        t0 = time.perf_counter()
        res = orig_next()
        node._inclusive_s += time.perf_counter() - t0
        return res

    def timed_close():
        t0 = time.perf_counter()
        res = orig_close()
        node._inclusive_s += time.perf_counter() - t0
        return res

    node.open = timed_open
    node.next = timed_next
    node.close = timed_close
    node._is_instrumented = True

    for c in node.children():
        _instrument_tree(c)


def _compute_exclusive_times(node):
    child_inclusive = 0.0
    for c in node.children():
        _compute_exclusive_times(c)
        child_inclusive += getattr(c, "_inclusive_s", 0.0)
    node.stats["time_s"] = max(0.0, getattr(node, "_inclusive_s", 0.0) - child_inclusive)


def _format_analyze(node, indent=0):
    prefix = "  " * indent + node.describe()
    rows = node.stats.get("rows_out", 0)
    ms = node.stats.get("time_s", 0.0) * 1000.0
    line = f"{prefix:<42} rows={rows:<8} time={ms:.2f} ms"
    lines = [line]
    for c in node.children():
        lines.append(_format_analyze(c, indent + 1))
    return "\n".join(lines)


def explain_analyze(plan):
    _instrument_tree(plan)
    rows = plan.run()
    _compute_exclusive_times(plan)
    report = _format_analyze(plan)
    print(report)
    return report, rows
