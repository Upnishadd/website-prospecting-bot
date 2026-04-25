from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from models import ExportRow


def export_to_csv(rows: list[ExportRow], export_dir: Path, niche: str, location: str) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_niche = _safe_filename_part(niche)
    safe_location = _safe_filename_part(location)
    path = export_dir / f"prospects_{safe_niche}_{safe_location}_{timestamp}.csv"

    frame = pd.DataFrame([row.model_dump() for row in rows])
    frame.to_csv(path, index=False)
    return path


def _safe_filename_part(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return normalized or "unknown"
