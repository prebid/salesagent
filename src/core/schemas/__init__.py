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
