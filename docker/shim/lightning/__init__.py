"""
Shim for the PyPI `lightning` metapackage, which is currently quarantined.

Pyannote.audio (and possibly other deps) does `import lightning.pytorch` and
expects the same API surface as `pytorch_lightning`. Rather than installing the
real `lightning` metapackage, we install this shim and `pytorch_lightning`,
then alias the submodules at import time via `sys.modules`.

If/when PyPI un-quarantines `lightning`, this shim can be removed and the
Dockerfile reverted to a normal `pip install -r requirements.txt`.
See docs/BLOCKERS.md B-003.
"""
from __future__ import annotations

import importlib
import sys

import pytorch_lightning as _pl

__version__ = getattr(_pl, "__version__", "2.5.0")

# Alias the most common pytorch_lightning subtree under `lightning.pytorch`.
# Modules not pre-aliased here will still work via the lazy submodule loader
# below — pyannote.audio mostly uses Trainer / LightningModule / callbacks /
# loggers, which are covered.
_PRE_ALIASED_SUBMODULES = (
    "callbacks",
    "core",
    "loggers",
    "loops",
    "plugins",
    "profilers",
    "strategies",
    "trainer",
    "tuner",
    "utilities",
    "utilities.memory",
    "utilities.model_summary",
    "utilities.types",
    "accelerators",
)

sys.modules[f"{__name__}.pytorch"] = _pl
for _sub in _PRE_ALIASED_SUBMODULES:
    try:
        _m = importlib.import_module(f"pytorch_lightning.{_sub}")
        sys.modules[f"{__name__}.pytorch.{_sub}"] = _m
    except ModuleNotFoundError:
        # Some submodules vary across pytorch-lightning minor versions; skip silently.
        pass


def __getattr__(name: str):
    """Lazy fallback for attribute access on `lightning.<name>`.

    This is a last-resort trampoline. Most consumers go through `lightning.pytorch`
    which is already aliased above.
    """
    if name == "pytorch":
        return _pl
    raise AttributeError(f"module 'lightning' has no attribute {name!r}")
