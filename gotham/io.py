"""Atomic JSON persistence for townships, configs, and result files."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import gotham
from gotham.ontology import Township

logger = logging.getLogger(__name__)


def save_json(obj: object, path: Path) -> None:
    """Write JSON atomically with sorted keys and indent=2."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, indent=2, default=_default)
        fh.write("\n")
    os.replace(tmp, path)


def _default(o: object) -> Any:
    if isinstance(o, tuple):
        return list(o)
    if hasattr(o, "value"):
        return o.value
    raise TypeError(f"not JSON serialisable: {type(o)!r}")


def load_json(path: Path) -> object:
    with Path(path).open(encoding="utf-8") as fh:
        return json.load(fh)


def save_township(t: Township, path: Path) -> None:
    save_json(t.to_dict(), Path(path))


def load_township(path: Path) -> Township:
    data = load_json(Path(path))
    assert isinstance(data, dict)
    return Township.from_dict(data)


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def data_version_of(t: Township) -> str:
    """First 8 hex chars of the SHA-256 of the canonical township JSON."""
    digest = hashlib.sha256(canonical_json(t.to_dict()).encode("utf-8")).hexdigest()
    return digest[:8]


def model_version() -> str:
    """Package version plus short git HEAD (or 'nogit')."""
    head = "nogit"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        )
        if out.returncode == 0 and out.stdout.strip():
            head = out.stdout.strip()[:8]
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        logger.debug("git HEAD unavailable")
    return f"{gotham.__version__}+{head}"


def results_dir(root: Path, data_version: str, model_version: str) -> Path:
    """Versioned results directory, created if missing."""
    d = Path(root) / f"{data_version}_{model_version}"
    d.mkdir(parents=True, exist_ok=True)
    return d
