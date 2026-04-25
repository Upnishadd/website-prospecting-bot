from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from models import ExportRow


def export_to_csv(rows: list[ExportRow], export_dir: Path, niche: str, location: str) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_niche = niche.lower().replace(" ", "_")
    safe_location = location.lower().replace(" ", "_")
    path = export_dir / f"prospects_{safe_niche}_{safe_location}_{timestamp}.csv"

    frame = pd.DataFrame([row.model_dump() for row in rows])
    frame.to_csv(path, index=False)
    return path
