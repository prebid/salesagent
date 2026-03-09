"""Schema package — re-exports all names for backward compatibility.

``from src.core.schemas import Creative`` continues to work unchanged.
Creative-domain classes live in ``src.core.schemas.creative``; everything
else lives in ``src.core.schemas._base``.
"""

# Re-export everything from _base (the original schemas.py minus creative classes)
from src.core.schemas._base import *  # noqa: F401, F403

# --- Forward-reference resolution ---
# _base.py has forward string references to types defined in creative.py:
#   - PackageRequest.creatives: list["Creative"]
#   - GetMediaBuysPackage.creative_approvals: list["CreativeApproval"]
# Now that both modules are imported, rebuild models so Pydantic resolves them.
from src.core.schemas._base import GetMediaBuysPackage as _GetMediaBuysPackage
from src.core.schemas._base import PackageRequest as _PackageRequest

# Re-export everything from creative module
from src.core.schemas.creative import *  # noqa: F401, F403
from src.core.schemas.creative import Creative as _Creative
from src.core.schemas.creative import CreativeApproval as _CreativeApproval

_PackageRequest.model_rebuild(_types_namespace={"Creative": _Creative})
_GetMediaBuysPackage.model_rebuild(_types_namespace={"CreativeApproval": _CreativeApproval})
