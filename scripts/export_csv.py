from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.config import load_settings
from src.export_csv import export_current_data


if __name__ == "__main__":
    settings = load_settings()
    paths = export_current_data(settings)
    for path in paths:
        print(path)
