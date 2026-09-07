import sys
from pathlib import Path

# Allow `from common.schemas import ...` to resolve to services/common,
# and `from app...` to resolve to this service's own app/ package.
_SERVICES_DIR = Path(__file__).resolve().parent.parent
_THIS_SERVICE_DIR = Path(__file__).resolve().parent

for path in (_SERVICES_DIR, _THIS_SERVICE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
