"""
reset_data.py - puts the demo data back to its starting state.

The server reads and writes the files in data/live/.
The untouched originals are kept in data/original/.
Run this before every fresh demo:   python reset_data.py
"""
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
ORIGINAL = DATA_DIR / "original"
LIVE = DATA_DIR / "live"


def reset() -> None:
    if LIVE.exists():
        shutil.rmtree(LIVE)
    shutil.copytree(ORIGINAL, LIVE)


if __name__ == "__main__":
    reset()
    print("Demo data reset: data/live/ now matches data/original/")
