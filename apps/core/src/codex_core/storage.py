from pathlib import Path

from .main import db_path


def codex_data_dir() -> Path:
    return db_path().parent
