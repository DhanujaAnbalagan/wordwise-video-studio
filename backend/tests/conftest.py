import sys, os
# Ensure the backend directory is on the import path for tests
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import pydantic_core
import pydantic_core.core_schema as _cs

if not hasattr(pydantic_core, "validate_core_schema"):
    pydantic_core.validate_core_schema = lambda s: s

_orig_union = _cs.union_schema
_cs.union_schema = lambda *a, **kw: _orig_union(*a, **{k: v for k, v in kw.items() if k != "strict"})

