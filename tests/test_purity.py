"""The pure core must stay pure: no Home Assistant imports, and no household data.

If either guard fails, the fix is in the code, not the test.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "custom_components" / "heating_engine" / "heating_core"
REPO = Path(__file__).resolve().parent.parent

FORBIDDEN_IMPORT_ROOTS = {"homeassistant", "voluptuous", "custom_components"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_heating_core_imports_nothing_from_home_assistant():
    offenders = {}
    for path in CORE.glob("*.py"):
        bad = _imports(path) & FORBIDDEN_IMPORT_ROOTS
        if bad:
            offenders[path.name] = sorted(bad)
    assert not offenders, f"heating_core must not import these: {offenders}"


def test_heating_core_only_uses_the_standard_library():
    allowed = {"dataclasses", "datetime", "typing", "collections", "enum", "math", "zoneinfo", "__future__"}
    for path in CORE.glob("*.py"):
        extra = {r for r in _imports(path) if r not in allowed and not r.startswith(".")}
        assert not extra, f"{path.name} imports outside the standard-library allow list: {sorted(extra)}"


IP_SHAPE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TOKEN_SHAPE = re.compile(r"eyJ[A-Za-z0-9_-]{20,}")


def test_no_addresses_or_tokens_anywhere_in_the_repo():
    hits = []
    for path in REPO.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".json", ".md", ".yaml", ".yml", ".ps1", ".toml", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for rx in (IP_SHAPE, TOKEN_SHAPE):
            for m in rx.finditer(text):
                hits.append(f"{path.relative_to(REPO)}: {m.group(0)}")
    assert not hits, hits


ENGINE = REPO / "custom_components" / "heating_engine" / "engine.py"


def test_a_write_is_recorded_before_the_climate_call_is_awaited():
    """The device's report arrives while the blocking call is still awaiting.
    If the tracker learns about the write only afterwards, the engine files its
    own echo as an unexpected change. Order inside _write must stay: record, then call."""
    source = ENGINE.read_text(encoding="utf-8")
    body = source.split("async def _write(", 1)[1].split("\n    def ", 1)[0]
    recorded = body.index("self.tracker.record_write(")
    called = body.index("await self.hass.services.async_call(")
    assert recorded < called, "record_write must come before the climate service call"
