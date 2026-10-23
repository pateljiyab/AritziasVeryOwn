import sys
from pathlib import Path

# No package install: make `import stylist...` work straight from api/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
