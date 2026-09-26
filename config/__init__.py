import importlib.util
import os
import sys

# Load Config class from root config.py
_root_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py")
if os.path.isfile(_root_config_path):
    _spec = importlib.util.spec_from_file_location("root_config_module", _root_config_path)
    if _spec and _spec.loader:
        _root_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_root_mod)
        Config = getattr(_root_mod, "Config", None)
    else:
        Config = None
else:
    Config = None

from .settings import settings

__all__ = ["settings", "Config"]

