"""SQL value semantics: NULL three-valued logic, comparisons, functions.

NULL is not a value, it is the absence of one. Any arithmetic or
comparison that touches NULL yields NULL, and WHERE only keeps rows
where the predicate is genuinely TRUE, so NULL and FALSE both reject.
AND/OR/NOT follow the SQL truth tables.
"""

import re


class Null:
    """Marker for SQL NULL; eval uses None, this is only for clarity."""

TRUE, FALSE, UNKNOWN = True, False, None


def truthy(value):
    """How a value behaves in a boolean position: True, False or None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    return bool(value)


def sql_not(value):
    t = truthy(value)
    return None if t is None else (not t)


def sql_and(a, b):
    ta, tb = truthy(a), truthy(b)
    if ta is False or tb is False:
        return False
    if ta is None or tb is None:
        return None
    return True


def sql_or(a, b):
    ta, tb = truthy(a), truthy(b)
    if ta is True or tb is True:
        return True
    if ta is None or tb is None:
        return None
    return False


def compare(op, a, b):
    """=, !=, <, <=, >, >= with SQL semantics. NULL in, NULL out.

    Mixed types never raise: numbers always sort before text, so a CSV
    column demoted to text compares sanely instead of exploding.
    """
    if a is None or b is None:
        return None
    if op == "=":
        return _equal(a, b)
    if op == "!=":
        eq = _equal(a, b)
        return None if eq is None else (not eq)
    key_a, key_b = _sort_key(a), _sort_key(b)
    if op == "<":
        return key_a < key_b
    if op == "<=":
        return key_a <= key_b
    if op == ">":
        return key_a > key_b
    if op == ">=":
        return key_a >= key_b
    raise ValueError("unknown operator " + op)


def _equal(a, b):
    if isinstance(a, bool) != isinstance(b, bool):
        if isinstance(a, bool) or isinstance(b, bool):
            a, b = (1 if a is True else 0 if a is False else a), \
                   (1 if b is True else 0 if b is False else b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    return a == b


def _sort_key(v):
    if isinstance(v, bool):
        v = 1 if v else 0
    if isinstance(v, (int, float)):
        return (0, float(v))
    return (1, str(v))


def like(value, pattern):
    """SQL LIKE: % matches any run, _ matches one character. Case sensitive."""
    if value is None or pattern is None:
        return None
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c)
                    for c in str(pattern))
    return re.fullmatch(regex, str(value)) is not None


def arith(op, a, b):
    if a is None or b is None:
        return None
    if op == "||":
        return str(a) + str(b)
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        from .errors import SquealError
        raise SquealError("cannot apply %s to %r and %r" % (op, a, b))
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        from .errors import SquealError
        if b == 0:
            raise SquealError("division by zero")
        return a / b
    if op == "%":
        from .errors import SquealError
        if b == 0:
            raise SquealError("division by zero")
        return a % b
    raise ValueError("unknown operator " + op)


def neg(value):
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        from .errors import SquealError
        raise SquealError("cannot negate %r" % (value,))
    return -value


# scalar functions --------------------------------------------------------

def _fn_upper(args):
    return None if args[0] is None else str(args[0]).upper()


def _fn_lower(args):
    return None if args[0] is None else str(args[0]).lower()


def _fn_length(args):
    return None if args[0] is None else len(str(args[0]))


def _fn_trim(args):
    return None if args[0] is None else str(args[0]).strip()


def _fn_abs(args):
    return None if args[0] is None else abs(args[0])


def _fn_round(args):
    if args[0] is None:
        return None
    digits = args[1] if len(args) > 1 else 0
    result = round(args[0], int(digits) if digits is not None else 0)
    return int(result) if (len(args) == 1 or digits == 0) else result


def _fn_coalesce(args):
    for arg in args:
        if arg is not None:
            return arg
    return None


def _fn_nullif(args):
    if compare("=", args[0], args[1]):
        return None
    return args[0]


def _fn_substr(args):
    if args[0] is None or args[1] is None:
        return None
    s, start = str(args[0]), int(args[1]) - 1  # SQL strings are 1-based
    if len(args) > 2 and args[2] is not None:
        return s[start:start + int(args[2])]
    return s[start:]


def _fn_replace(args):
    if any(a is None for a in args):
        return None
    return str(args[0]).replace(str(args[1]), str(args[2]))


def _fn_typeof(args):
    v = args[0]
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "float"
    return "text"


def _fn_ifnull(args):
    return args[0] if args[0] is not None else args[1]


def _fn_iif(args):
    return args[1] if truthy(args[0]) is True else args[2]


def _fn_instr(args):
    if args[0] is None or args[1] is None:
        return None
    return str(args[0]).find(str(args[1])) + 1  # 1-based, 0 = not found


def _fn_ltrim(args):
    return None if args[0] is None else str(args[0]).lstrip()


def _fn_rtrim(args):
    return None if args[0] is None else str(args[0]).rstrip()


def _fn_sign(args):
    if args[0] is None:
        return None
    return (args[0] > 0) - (args[0] < 0)


def _fn_floor(args):
    import math
    return None if args[0] is None else math.floor(args[0])


def _fn_ceil(args):
    import math
    return None if args[0] is None else math.ceil(args[0])


def _fn_power(args):
    if args[0] is None or args[1] is None:
        return None
    return args[0] ** args[1]


def _fn_sqrt(args):
    import math
    if args[0] is None:
        return None
    if args[0] < 0:
        from .errors import SquealError
        raise SquealError("sqrt of a negative number")
    return math.sqrt(args[0])


SCALAR_FUNCTIONS = {
    "upper": (1, 1, _fn_upper),
    "lower": (1, 1, _fn_lower),
    "length": (1, 1, _fn_length),
    "trim": (1, 1, _fn_trim),
    "abs": (1, 1, _fn_abs),
    "round": (1, 2, _fn_round),
    "coalesce": (1, 99, _fn_coalesce),
    "nullif": (2, 2, _fn_nullif),
    "substr": (2, 3, _fn_substr),
    "replace": (3, 3, _fn_replace),
    "typeof": (1, 1, _fn_typeof),
    "ifnull": (2, 2, _fn_ifnull),
    "iif": (3, 3, _fn_iif),
    "instr": (2, 2, _fn_instr),
    "ltrim": (1, 1, _fn_ltrim),
    "rtrim": (1, 1, _fn_rtrim),
    "sign": (1, 1, _fn_sign),
    "floor": (1, 1, _fn_floor),
    "ceil": (1, 1, _fn_ceil),
    "ceiling": (1, 1, _fn_ceil),
    "power": (2, 2, _fn_power),
    "pow": (2, 2, _fn_power),
    "sqrt": (1, 1, _fn_sqrt),
}


# aggregate functions -----------------------------------------------------

def _agg_count(values, star):
    if star:
        return len(values)
    return sum(1 for v in values if v is not None)


def _agg_sum(values, star):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _agg_avg(values, star):
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _agg_min(values, star):
    present = [v for v in values if v is not None]
    return min(present, key=_sort_key) if present else None


def _agg_max(values, star):
    present = [v for v in values if v is not None]
    return max(present, key=_sort_key) if present else None


def _agg_group_concat(values, star, separator=","):
    present = [str(v) for v in values if v is not None]
    return separator.join(present) if present else None


AGGREGATES = {
    "count": _agg_count,
    "sum": _agg_sum,
    "avg": _agg_avg,
    "min": _agg_min,
    "max": _agg_max,
    "group_concat": _agg_group_concat,
}
