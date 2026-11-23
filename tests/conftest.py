"""Make the pure engine importable without Home Assistant.

The engine lives at custom_components/heating_engine/heating_core so that it ships
inside the integration folder. Importing it via the package path would execute
custom_components/heating_engine/__init__.py, which imports Home Assistant. Putting
the integration folder itself on sys.path lets tests import `heating_core` directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

INTEGRATION_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "heating_engine"
if str(INTEGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_DIR))
