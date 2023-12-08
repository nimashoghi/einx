from . import stage2, solver
import numpy as np
from functools import partial
import einx

class Expression:
    def __init__(self, value):
        if not isinstance(value, (int, np.int64, np.int32, np.int16, np.int8)):
            raise TypeError(f"Expected int, got {type(value)}")
        self.value = int(value)
        self.parent = None

    @property
    def shape(self):
        return tuple(x.value for x in self)

class Composition(Expression):
    def __init__(self, inner):
        Expression.__init__(self, inner.value)
        self.inner = inner
        inner.parent = self

    def __str__(self):
        return f"({self.inner})"

    def __len__(self):
        return 1

    def __iter__(self):
        yield self

    def __deepcopy__(self):
        return Composition(self.inner.__deepcopy__())

    def __eq__(self, other):
        return isinstance(other, Composition) and self.inner == other.inner

    def __hash__(self):
        return 8716123 + hash(self.inner)

    def all(self):
        yield self
        yield from self.inner.all()

class List(Expression):
    def maybe(l, *args, **kwargs):
        if not isinstance(l, list):
            raise TypeError(f"Expected list, got {type(l)}")
        if len(l) == 1:
            return l[0]
        else:
            return List(l, *args, **kwargs)

    def __init__(self, children):
        Expression.__init__(self, np.prod([c.value for c in children]).astype(int))
        self.children = children
        for c in children:
            if isinstance(c, List):
                raise ValueError("List cannot have another List as direct child")
            c.parent = self

    def __str__(self):
        return " ".join([str(c) for c in self.children])

    def __len__(self):
        return sum(len(c) for c in self.children)

    def __iter__(self):
        for c in self.children:
            yield from c

    def __deepcopy__(self):
        return List([c.__deepcopy__() for c in self.children])

    def __eq__(self, other):
        return isinstance(other, List) and self.children == other.children

    def __hash__(self):
        return 6563 + hash(tuple(self.children))

    def all(self):
        yield self
        for c in self.children:
            yield from c.all()

class Axis(Expression):
    def __init__(self, name, value):
        Expression.__init__(self, value)
        self.name = name if not name is None else f"unnamed.{id(self)}"

    def __repr__(self):
        return f"Axis({self.name}, {self.value})"

    def __str__(self):
        return self.name if not self.is_unnamed else str(self.value)

    def __len__(self):
        return 1

    def __iter__(self):
        yield self

    def __deepcopy__(self):
        return Axis(self.name, self.value)

    def __eq__(self, other):
        if not isinstance(other, Axis):
            return False
        if self.is_unnamed != other.is_unnamed:
            return False
        if self.value != other.value:
            return False
        if self.is_unnamed:
            return True
        else:
            return self.name == other.name

    def __hash__(self):
        return 9817234 + (hash(self.name) if not self.is_unnamed else 0) + hash(self.value)

    def all(self):
        yield self

    @property
    def is_unnamed(self):
        return self.name.startswith("unnamed.")

class Concatenation(Expression):
    @staticmethod
    def maybe(l, *args, **kwargs):
        if not isinstance(l, list):
            raise TypeError(f"Expected list, got {type(l)}")
        if len(l) == 1:
            return l[0]
        else:
            return Concatenation(l, *args, **kwargs)

    def __init__(self, children):
        if len(children) == 0:
            raise ValueError("Concatenation must have at least one child")
        Expression.__init__(self, np.sum([c.value for c in children]).astype("int32"))
        self.children = children
        for c in children:
            if len(c) != 1:
                raise ValueError(f"Concatenation can only be used on expressions of length 1, but got expression '{c}'")
            c.parent = self

    def __str__(self):
        return "+".join([str(c) for c in self.children])

    def __len__(self):
        return 1

    def __iter__(self):
        yield self

    def __deepcopy__(self):
        return Concatenation([c.__deepcopy__() for c in self.children])

    def __eq__(self, other):
        return isinstance(other, Concatenation) and self.children == other.children

    def __hash__(self):
        return 123 + hash(tuple(self.children))

    def all(self):
        yield self
        for c in self.children:
            yield from c.all()

class Marker(Expression):
    def __init__(self, inner):
        if len(inner) == 0:
            raise ValueError("Marker cannot have empty list as child")
        Expression.__init__(self, inner.value)
        self.inner = inner
        inner.parent = self

    def __str__(self):
        return f"[{self.inner}]"

    def __len__(self):
        return len(self.inner)

    def __iter__(self):
        yield from self.inner

    def __deepcopy__(self):
        return Marker(self.inner.__deepcopy__())

    def __eq__(self, other):
        return isinstance(other, Marker) and self.inner == other.inner

    def __hash__(self):
        return 6433236 + hash(self.inner)

    def all(self):
        yield self
        yield from self.inner.all()



class SolveValueException(Exception):
    def __init__(self, expressions, values, message):
        self.expressions = expressions
        self.values = values
        self.message = f"Failed to solve values of expressions. {message}\nInput:\n"
        for expr, value in zip(expressions, values):
            self.message += f"    '{expr}' has shape {einx.expr.util._to_str(value)}\n"
        super().__init__(self.message)

def solve(expressions, values):
    if any(not isinstance(expr, stage2.Expression) for expr in expressions):
        raise ValueError("Can only expand stage2.Expression")
    if len(values) != len(expressions):
        raise ValueError("Number of expressions and values must be equal")
    values = [(np.asarray(value) if not value is None else None) for value in values]

    equations = []

    symbolic_expr_values = {}
    for root in expressions:
        for expr in root.all():
            symbolic_expr_values[id(expr)] = solver.Variable(str(id(expr)), str(expr))

    # Add equations: Relations between expressions and their children
    for root in expressions:
        for expr in root.all():
            if isinstance(expr, stage2.List):
                equations.append((
                    solver.Product([symbolic_expr_values[id(c)] for c in expr.children]),
                    symbolic_expr_values[id(expr)],
                ))
            elif isinstance(expr, stage2.Concatenation):
                equations.append((
                    solver.Sum([symbolic_expr_values[id(c)] for c in expr.children]),
                    symbolic_expr_values[id(expr)],
                ))
            elif isinstance(expr, stage2.Marker) or isinstance(expr, stage2.Composition):
                equations.append((
                    symbolic_expr_values[id(expr)],
                    symbolic_expr_values[id(expr.inner)],
                ))

    # Add equations: Root values
    for i, (root, value) in enumerate(zip(expressions, values)):
        if not value is None:
            assert len(value) == len(root)
            for expr, value in zip(root, value):
                equations.append((
                    symbolic_expr_values[id(expr)],
                    int(value),
                ))

    # Add equations: Unnamed axes
    for root in expressions:
        for expr in root.all():
            if isinstance(expr, stage2.UnnamedAxis):
                equations.append((
                    symbolic_expr_values[id(expr)],
                    int(expr.value),
                ))

    # Add equations: Multiple occurrences of the same named axis must have the same value
    sympy_axis_values = {}
    for root in expressions:
        for axis in root.all():
            if isinstance(axis, stage2.NamedAxis):
                if not axis.name in sympy_axis_values:
                    sympy_axis_values[axis.name] = solver.Variable(axis.name, axis.name)
                equations.append((
                    symbolic_expr_values[id(axis)],
                    sympy_axis_values[axis.name],
                ))

    # Solve
    try:
        axis_values = solver.solve(equations)
    except solver.SolveException as e:
        raise SolveValueException(expressions, values, str(e))
    axis_values = {int(k): int(v) for k, v in axis_values.items() if not str(k) in sympy_axis_values}

    failed_axes = set()
    for root in expressions:
        for expr in root.all():
            if isinstance(expr, stage2.NamedAxis):
                if not id(expr) in axis_values:
                    failed_axes.add(str(expr))
    if len(failed_axes) == 1:
        raise SolveValueException(expressions, values, f"Found no unique solution for '{failed_axes.pop()}'")
    elif len(failed_axes) > 1:
        raise SolveValueException(expressions, values, f"Found no unique solutions for {failed_axes}")

    # Map stage2 expressions to stage3 expressions
    def map(expr):
        if isinstance(expr, stage2.NamedAxis):
            assert id(expr) in axis_values
            if axis_values[id(expr)] <= 0:
                raise SolveValueException(expressions, values, f"Axis '{expr}' has value {axis_values[id(expr)]} <= 0")
            return Axis(expr.name, axis_values[id(expr)])
        elif isinstance(expr, stage2.UnnamedAxis):
            assert id(expr) in axis_values
            if axis_values[id(expr)] <= 0:
                raise SolveValueException(expressions, values, f"Axis '{expr}' has value {axis_values[id(expr)]} <= 0")
            return Axis(None, axis_values[id(expr)])
        elif isinstance(expr, stage2.List):
            return List([map(child) for child in expr.children])
        elif isinstance(expr, stage2.Concatenation):
            return Concatenation([map(child) for child in expr.children])
        elif isinstance(expr, stage2.Marker):
            return Marker(map(expr.inner))
        elif isinstance(expr, stage2.Composition):
            return Composition(map(expr.inner))
        else:
            assert False, type(expr)
    expressions = [map(root) for root in expressions]

    return expressions





def expr_map(f):
    def outer(expr, *args, **kwargs):
        # Wrap the user function to return a list of expressions
        def f2(expr):
            t = f(expr, *args, **kwargs)
            if t is None:
                return None, expr_map.CONTINUE
            expr, signal = t

            if isinstance(expr, list) or expr is None:
                return expr, signal
            if isinstance(expr, List):
                return expr.children, signal
            elif isinstance(expr, Expression):
                return [expr], signal
            else:
                raise TypeError(f"Invalid return type {type(expr)}")
        return List.maybe(_expr_map(expr, f2))
    return outer

expr_map.CONTINUE = 1
expr_map.COPY_AND_STOP = 2
expr_map.REPLACE_AND_STOP = 3
expr_map.REPLACE_AND_CONTINUE = 4

def _expr_map(expr, f):
    exprs, signal = f(expr)
    if signal == expr_map.REPLACE_AND_STOP:
        assert isinstance(exprs, list)
        return exprs
    elif signal == expr_map.COPY_AND_STOP:
        return [expr.__deepcopy__()]
    elif signal == expr_map.REPLACE_AND_CONTINUE:
        return [c for expr in exprs for c in _expr_map(expr, f)]

    if isinstance(expr, Axis):
        return [expr.__deepcopy__()]
    elif isinstance(expr, Composition):
        return [Composition(List.maybe(_expr_map(expr.inner, f)))]
    elif isinstance(expr, List):
        return [c2 for c1 in expr.children for c2 in _expr_map(c1, f)]
    elif isinstance(expr, Concatenation):
        children = [List.maybe(_expr_map(c, f)) for c in expr.children]
        children = [c if len(c) > 0 else Axis(None, 1) for c in children]
        return [Concatenation(children)]
    elif isinstance(expr, Marker):
        x = _expr_map(expr.inner, f)
        if len(x) == 0:
            # Drop empty marker
            return []
        else:
            return [Marker(List.maybe(x))]
    else:
        raise TypeError(f"Invalid expression type {type(expr)}")



@expr_map
def decompose(expr):
    if isinstance(expr, Composition):
        return expr.inner, expr_map.REPLACE_AND_CONTINUE
    elif isinstance(expr, Concatenation):
        return None, expr_map.COPY_AND_STOP

@expr_map
def demark(expr):
    if isinstance(expr, Marker):
        return expr.inner, expr_map.REPLACE_AND_CONTINUE

@expr_map
def replace(expr, f):
    expr = f(expr)
    if not expr is None:
        return expr, expr_map.REPLACE_AND_STOP

@expr_map
def remove(expr, pred):
    if pred(expr):
        return [], expr_map.REPLACE_AND_STOP

def remove_unnamed_trivial_axes(expr):
    def is_concat_child(expr): # Do not remove direct children of concatenations
        return not expr.parent is None and (isinstance(expr.parent, Concatenation) or (isinstance(expr.parent, Marker) and is_concat_child(expr.parent)))
    return remove(expr, lambda expr: isinstance(expr, Axis) and expr.is_unnamed and expr.value == 1 and not is_concat_child(expr))

@expr_map
def mark(expr, pred):
    if not isinstance(expr, Marker) and (expr.parent is None or not isinstance(expr.parent, Marker)) and pred(expr):
        return Marker(expr.__deepcopy__()), expr_map.REPLACE_AND_CONTINUE

def any_parent_is(expr, pred, include_self=True):
    if not include_self:
        if expr.parent is None:
            return False
        expr = expr.parent
    while not expr is None:
        if pred(expr):
            return True
        expr = expr.parent
    return False

def is_marked(expr):
    return any_parent_is(expr, lambda expr: isinstance(expr, Marker))

def is_at_root(expr):
    return not any_parent_is(expr, lambda expr: isinstance(expr, Composition))

def is_flat(expr):
    return all(not isinstance(expr, Composition) and not isinstance(expr, Concatenation) for expr in expr.all())

def get_axes(expr):
    return [expr for expr in expr.all() if isinstance(expr, Axis)]

def get_named_axes(expr):
    return [expr for expr in expr.all() if isinstance(expr, Axis) and not expr.is_unnamed]

def _get_marked(expr):
    if isinstance(expr, Axis):
        return []
    elif isinstance(expr, Marker):
        return [expr.inner.__deepcopy__()]
    elif isinstance(expr, Concatenation):
        return [Concatenation.maybe([x for c in expr.children for x in _get_marked(c)])]
    elif isinstance(expr, Composition):
        return [Composition(List.maybe(_get_marked(expr.inner)))]
    elif isinstance(expr, List):
        return [List.maybe([x for c in expr.children for x in _get_marked(c)])]
    else:
        raise TypeError(f"Invalid expression type {type(expr)}")

def get_marked(expr):
    return List.maybe(_get_marked(expr))

def get_unmarked(expr):
    return remove(expr, lambda expr: not einx.expr.stage3.is_marked(expr))