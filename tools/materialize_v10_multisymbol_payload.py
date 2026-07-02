"""Materialize the V10 multi-symbol implementation files."""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parts_dir = Path(__file__).with_name("v10_multisymbol_payload_parts")
    part_paths = sorted(parts_dir.glob("part_*.txt"))
    if not part_paths:
        raise FileNotFoundError(f"No payload parts found in {parts_dir}")
    encoded = "".join(
        path.read_text(encoding="utf-8").strip()
        for path in part_paths
    )
    files = json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf-8"))
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {relative}")


if __name__ == "__main__":
    main()
