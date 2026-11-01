#!/usr/bin/env bash
# Check that the architecture figures exist, follow the scene format, and are
# the only diagrams used by the top-level docs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

python3 - <<'PY'
import json
from pathlib import Path

FIGURES = ("request-path", "telemetry-path", "incident-path")
REQUIRED_FIELDS = {
    "id",
    "type",
    "x",
    "y",
    "width",
    "height",
    "angle",
    "strokeColor",
    "backgroundColor",
    "fillStyle",
    "strokeWidth",
    "strokeStyle",
    "roughness",
    "opacity",
    "groupIds",
    "frameId",
    "roundness",
    "seed",
    "version",
    "versionNonce",
    "isDeleted",
    "boundElements",
    "updated",
    "link",
    "locked",
}

for name in FIGURES:
    scene_path = Path("docs/figures") / f"{name}.excalidraw"
    png_path = Path("docs/figures") / f"{name}.png"
    assert scene_path.is_file(), f"missing {scene_path}"
    assert png_path.is_file(), f"missing {png_path}"
    assert png_path.stat().st_size > 10_000, f"{png_path} is too small to read"

    scene = json.loads(scene_path.read_text())
    assert scene["type"] == "excalidraw"
    assert scene["appState"]["viewBackgroundColor"] == "#ffffff"
    assert scene["files"] == {}

    elements = scene["elements"]
    assert elements, f"{scene_path} has no elements"
    ids = [element["id"] for element in elements]
    assert len(ids) == len(set(ids)), f"{scene_path} has duplicate element ids"

    for element in elements:
        missing = REQUIRED_FIELDS - set(element)
        assert not missing, f"{element['id']} is missing {sorted(missing)}"
        if element["type"] == "rectangle":
            assert element["fillStyle"] == "hachure", element["id"]
        if element["type"] in {"rectangle", "arrow"}:
            assert element["roughness"] == 2, element["id"]
            assert element["strokeWidth"] == 2, element["id"]
            assert element["strokeStyle"] == "solid", element["id"]
        if element["type"] == "text":
            assert element["fontFamily"] == 1, element["id"]
            assert element["fontSize"] >= 16, element["id"]

    labels = {
        element["id"]
        for element in elements
        if element["type"] == "text" and element.get("containerId") is None
    }
    for element in elements:
        if element["type"] == "text" and element.get("containerId") is not None:
            assert element["containerId"] in ids, element["id"]
        if element["type"] == "arrow":
            assert f"{element['id']}-label" in labels, f"{element['id']} has no label"

readme = Path("README.md").read_text()
for figure in FIGURES:
    assert f"docs/figures/{figure}.png" in readme, f"README does not embed {figure}"
for path in ("README.md", "docs/ARCHITECTURE.md"):
    assert "```mermaid" not in Path(path).read_text(), f"{path} still contains Mermaid"

print("documentation contracts passed")
PY
