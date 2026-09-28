"""Small helper API for writing validation rule plugins."""
from __future__ import annotations

from .project import definition_ref, raw_value, value_elements, is_auto_value, is_user_defined  # noqa: F401
from .validation.basic import param_obj, parse_bool, parse_float, parse_int  # noqa: F401


def containers(ctx, def_path: str):
    """Yield (element, path) of all containers with definition *def_path* (vendor or standard)."""
    for el in ctx.model.containers_of_def(def_path, ctx.defs):
        yield el, ctx.model.path_of(el)


def values(container, param_name: str):
    """Value elements of the parameter/reference with short name *param_name*."""
    return [v for v in value_elements(container) if definition_ref(v).rsplit("/", 1)[-1] == param_name]


def value(container, param_name: str, default=None):
    """Stored text of the first instance of *param_name* in *container*."""
    vs = values(container, param_name)
    if not vs:
        return default
    r = raw_value(vs[0])
    return default if r is None else r


def pdef(ctx, container, param_name: str):
    """Definition of parameter *param_name* of *container*."""
    cdef = ctx.defs.find(definition_ref(container))
    return cdef.child(param_name) if cdef is not None else None
