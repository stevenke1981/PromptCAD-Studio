from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.dxf_analysis import DxfAnalysisError, DxfFeatureExtractor


def main() -> int:
    """Read one JSON request on stdin and write exactly one JSON response."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        request = json.loads(sys.stdin.read())
        path = Path(request["path"])
        max_bytes = int(request["max_bytes"])
        if not path.is_absolute() or path.suffix.lower() != ".dxf":
            raise ValueError("DXF worker requires an absolute temporary .dxf path")
        if not path.is_file() or path.stat().st_size > max_bytes:
            raise ValueError("DXF temporary file is missing or exceeds the byte limit")
        data = path.read_bytes()
        extractor = DxfFeatureExtractor(
            max_bytes=max_bytes,
            max_entities=int(request["max_entities"]),
            max_segments=int(request["max_segments"]),
            max_holes=int(request["max_holes"]),
        )
        result = extractor.analyze_path(
            path,
            thickness_mm=float(request["thickness_mm"]),
            unit_override=request.get("unit_override", "auto"),
            source_data=data,
        )
        output = {"ok": True, "result": result.model_dump(mode="json")}
    except (KeyError, TypeError, ValueError, DxfAnalysisError) as exc:
        output = {"ok": False, "error": str(exc)}
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
