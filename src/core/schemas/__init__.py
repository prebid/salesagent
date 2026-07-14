"""Schema package — re-exports all names for backward compatibility.

``from src.core.schemas import Creative`` continues to work unchanged.
Creative-domain classes live in ``src.core.schemas.creative``;
product-domain classes in ``src.core.schemas.product``;
delivery-domain classes in ``src.core.schemas.delivery``;
everything else lives in ``src.core.schemas._base``.
"""

# isort: off
# Import order matters: product/delivery shadow _base duplicates, creative resolves forward refs.
from src.core.schemas._base import *  # noqa: F401, F403
from src.core.schemas._base import GetMediaBuysPackage as _GetMediaBuysPackage
from src.core.schemas._base import PackageRequest as _PackageRequest
from src.core.schemas.product import *  # noqa: F401,F403
from src.core.schemas.delivery import *  # noqa: F401,F403
from src.core.schemas.creative import *  # noqa: F401, F403
from src.core.schemas.account import *  # noqa: F401, F403
from src.core.schemas.creative import Creative as _Creative
from src.core.schemas.creative import CreativeApproval as _CreativeApproval
# isort: on

_PackageRequest.model_rebuild(_types_namespace={"Creative": _Creative})
_GetMediaBuysPackage.model_rebuild(_types_namespace={"CreativeApproval": _CreativeApproval})

# adcp 6.6 ships its model tree with deferred pydantic-core builds (forward refs unresolved
# at class creation), so shared leaf types (ReportingPeriod, PushNotificationConfig,
# ExtensionObject, ...) stay ``__pydantic_complete__ is False`` with a ``MockValSer`` serializer
# until something validates them as a *top-level* target. Pydantic only auto-heals a model
# used as a top-level target — a model used only as a *nested field* keeps its mock serializer,
# and our wrap serializers hand off to it during dump -> "'MockValSer' object cannot be
# converted to 'SchemaSerializer'". Order-dependent: a model passes iff an earlier test/import
# happened to validate its leaf types first.
#
# Fix: honor pydantic's contract by completing the transitive CLOSURE of model types reachable
# from the schemas we serialize, once, now that every referenced type is importable. This is
# deterministic and targeted — it walks only types our models reference, not the thousands of
# unused adcp submodels.
import inspect as _inspect  # noqa: E402
import typing as _typing  # noqa: E402

from pydantic import BaseModel as _BaseModel  # noqa: E402


def _iter_nested_models(_ann):
    """Yield every BaseModel subclass reachable through an annotation (unions, list/dict, ...)."""
    _args = _typing.get_args(_ann)
    if _args:
        for _a in _args:
            yield from _iter_nested_models(_a)
    elif _inspect.isclass(_ann) and issubclass(_ann, _BaseModel):
        yield _ann


_seen: set = set()
_worklist = [
    _o
    for _o in globals().values()
    if _inspect.isclass(_o) and issubclass(_o, _BaseModel) and hasattr(_o, "__pydantic_generic_metadata__")
]
while _worklist:
    _model = _worklist.pop()
    # ``__pydantic_generic_metadata__`` guard skips library generic aliases whose metaclass
    # raises on attribute access (they are not concrete models and never need rebuild).
    if _model in _seen or not hasattr(_model, "__pydantic_generic_metadata__"):
        continue
    _seen.add(_model)
    if getattr(_model, "__pydantic_complete__", True) is False:
        _model.model_rebuild()
    for _f in _model.model_fields.values():
        _worklist.extend(_iter_nested_models(_f.annotation))
del _inspect, _typing, _BaseModel, _iter_nested_models, _seen, _worklist, _model, _f
