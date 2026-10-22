"""A schema: the column list of an intermediate row stream.

Every column knows a qualifier (the table or alias it came from) and a
bare name. Lookups accept `region`, `orders.region` or an alias, and a
bare name that matches two columns is an error, not a coin flip.
"""

from .errors import SquealError


class Schema:
    def __init__(self, pairs):
        # pairs: [(qualifier or None, bare name)]
        self.pairs = list(pairs)
        self.names = [name for _, name in self.pairs]

    def __len__(self):
        return len(self.pairs)

    def merge(self, other):
        return Schema(self.pairs + other.pairs)

    def resolve(self, name):
        """Return the slot for a possibly qualified column name."""
        if "." in name:
            matches = [i for i, (q, n) in enumerate(self.pairs)
                       if q is not None and name == q + "." + n]
            if len(matches) == 1:
                return matches[0]
            raise SquealError("no such column: %s" % name)
        matches = [i for i, (_, n) in enumerate(self.pairs) if n == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            qualified = ["%s.%s" % (q, n) for i, (q, n) in enumerate(self.pairs)
                         if n == name and q is not None]
            hint = " (did you mean %s?)" % " or ".join(qualified) if qualified else ""
            raise SquealError("ambiguous column: %s%s" % (name, hint))
        raise SquealError("no such column: %s" % name)
