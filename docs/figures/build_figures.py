#!/usr/bin/env python3
"""Build the architecture scenes used by the README and ARCHITECTURE docs.

The scenes are deterministic: every element gets a stable id, seed, and
version so a rebuild produces the same JSON. Render the PNGs with the
Excalidraw CLI after running this script. See docs/DRAWINGS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

FIGURE_DIR = Path(__file__).resolve().parent

PALETTE = {
    "input": {"stroke": "#1971c2", "fill": "#d0ebff"},
    "runtime": {"stroke": "#9c36b5", "fill": "#f3d9fa"},
    "analysis": {"stroke": "#f08c00", "fill": "#fff3bf"},
    "risk": {"stroke": "#e03131", "fill": "#ffe3e3"},
    "model": {"stroke": "#0c8599", "fill": "#c5f6fa"},
    "neutral": {"stroke": "#495057", "fill": "#f1f3f5"},
}

INK = "#1e1e1e"
PAPER = "#ffffff"
BODY_FONT = 22
NOTE_FONT = 18
TITLE_FONT = 32
LINE_HEIGHT = 1.25


class Scene:
    """A tiny deterministic Excalidraw scene builder."""

    def __init__(self) -> None:
        self._elements: list[dict[str, object]] = []
        self._seed = 0

    def _next_seed(self) -> int:
        self._seed += 1
        return self._seed

    def box(
        self,
        element_id: str,
        x: int,
        y: int,
        width: int,
        height: int,
        label: str,
        role: str,
        font_size: int = BODY_FONT,
    ) -> None:
        """Add a colour-coded box with text bound to it."""
        palette = PALETTE[role]
        text_id = f"{element_id}-label"
        lines = label.count("\n") + 1
        text_height = round(lines * font_size * LINE_HEIGHT)

        self._elements.append(
            {
                "id": element_id,
                "type": "rectangle",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "angle": 0,
                "strokeColor": palette["stroke"],
                "backgroundColor": palette["fill"],
                "fillStyle": "hachure",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 2,
                "opacity": 100,
                "groupIds": [],
                "frameId": None,
                "roundness": {"type": 2},
                "seed": self._next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [{"type": "text", "id": text_id}],
                "updated": 1,
                "link": None,
                "locked": False,
            }
        )
        self._elements.append(
            {
                "id": text_id,
                "type": "text",
                "x": x + 12,
                "y": y + (height - text_height) // 2,
                "width": width - 24,
                "height": text_height,
                "angle": 0,
                "strokeColor": INK,
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 2,
                "opacity": 100,
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "seed": self._next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "updated": 1,
                "link": None,
                "locked": False,
                "text": label,
                "fontSize": font_size,
                "fontFamily": 1,
                "textAlign": "center",
                "verticalAlign": "middle",
                "containerId": element_id,
                "originalText": label,
                "lineHeight": LINE_HEIGHT,
            }
        )

    def arrow(
        self,
        element_id: str,
        start: tuple[int, int],
        end: tuple[int, int],
        label: str,
        label_at: tuple[int, int],
        role: str = "neutral",
    ) -> None:
        """Add a labelled arrow and a nearby text label."""
        palette = PALETTE[role]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        self._elements.append(
            {
                "id": element_id,
                "type": "arrow",
                "x": start[0],
                "y": start[1],
                "width": abs(dx),
                "height": abs(dy),
                "angle": 0,
                "strokeColor": palette["stroke"],
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 2,
                "opacity": 100,
                "groupIds": [],
                "frameId": None,
                "roundness": {"type": 2},
                "seed": self._next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "updated": 1,
                "link": None,
                "locked": False,
                "points": [[0, 0], [dx, dy]],
                "startArrowhead": None,
                "endArrowhead": "arrow",
            }
        )
        label_width = max(60, len(label) * NOTE_FONT // 2)
        self._elements.append(
            {
                "id": f"{element_id}-label",
                "type": "text",
                "x": int(label_at[0] - label_width / 2),
                "y": int(label_at[1] - NOTE_FONT),
                "width": label_width,
                "height": round(NOTE_FONT * LINE_HEIGHT),
                "angle": 0,
                "strokeColor": palette["stroke"],
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 2,
                "opacity": 100,
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "seed": self._next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "updated": 1,
                "link": None,
                "locked": False,
                "text": label,
                "fontSize": NOTE_FONT,
                "fontFamily": 1,
                "textAlign": "center",
                "verticalAlign": "top",
                "containerId": None,
                "originalText": label,
                "lineHeight": LINE_HEIGHT,
            }
        )

    def text(
        self,
        element_id: str,
        x: int,
        y: int,
        width: int,
        label: str,
        font_size: int,
    ) -> None:
        """Add a free text element, used for titles and notes."""
        lines = label.count("\n") + 1
        self._elements.append(
            {
                "id": element_id,
                "type": "text",
                "x": x,
                "y": y,
                "width": width,
                "height": round(lines * font_size * LINE_HEIGHT),
                "angle": 0,
                "strokeColor": INK,
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "roughness": 2,
                "opacity": 100,
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "seed": self._next_seed(),
                "version": 1,
                "versionNonce": 1,
                "isDeleted": False,
                "boundElements": [],
                "updated": 1,
                "link": None,
                "locked": False,
                "text": label,
                "fontSize": font_size,
                "fontFamily": 1,
                "textAlign": "left",
                "verticalAlign": "top",
                "containerId": None,
                "originalText": label,
                "lineHeight": LINE_HEIGHT,
            }
        )

    def write(self, path: Path) -> None:
        """Write the scene as Excalidraw JSON."""
        scene = {
            "type": "excalidraw",
            "version": 2,
            "source": "https://excalidraw.com",
            "elements": self._elements,
            "appState": {"viewBackgroundColor": PAPER},
            "files": {},
        }
        path.write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")


def request_path() -> Scene:
    """Checkout fans out to four services over HTTP and gRPC."""
    scene = Scene()
    scene.text(
        "title",
        80,
        40,
        1360,
        "Checkout fans out over HTTP and gRPC",
        TITLE_FONT,
    )
    scene.box("k6", 80, 220, 260, 90, "k6 load Job", "input")
    scene.box("checkout", 480, 350, 300, 110, "checkout-service\n:8003", "runtime")
    scene.box("cart", 1000, 140, 280, 90, "cart-service\n:8002", "neutral")
    scene.box("order", 1000, 270, 280, 90, "order-service\n:8004", "neutral")
    scene.box(
        "inventory",
        1000,
        400,
        300,
        110,
        "inventory-service\n:8001 + gRPC :50051",
        "neutral",
        font_size=20,
    )
    scene.box(
        "payment",
        1000,
        550,
        300,
        110,
        "payment-gateway\n:8005 + gRPC :50052",
        "model",
        font_size=20,
    )

    scene.arrow("k6-checkout", (340, 265), (480, 370), "load", (370, 248))
    scene.arrow("checkout-cart", (780, 395), (1000, 185), "HTTP", (850, 260))
    scene.arrow("checkout-order", (780, 405), (1000, 315), "HTTP", (850, 335))
    scene.arrow("checkout-inventory", (780, 420), (1000, 455), "gRPC", (860, 405))
    scene.arrow("checkout-payment", (780, 435), (1000, 605), "gRPC", (840, 535))

    scene.text(
        "note",
        80,
        700,
        1360,
        "catalog-service serves product reads on :8000. "
        "synthetic-monitor probes all six services.",
        NOTE_FONT,
    )
    return scene


def telemetry_path() -> Scene:
    """One collector feeds three stores, and burn-rate alerts become postmortems."""
    scene = Scene()
    scene.text(
        "title",
        80,
        40,
        1360,
        "One collector feeds three stores",
        TITLE_FONT,
    )
    scene.text(
        "subtitle",
        80,
        92,
        1360,
        "Traces, logs, and metrics share one OTLP gateway; Grafana queries all three.",
        NOTE_FONT,
    )

    scene.box("services", 80, 180, 300, 100, "6 ecom services", "input")
    scene.box(
        "synthetic",
        80,
        330,
        300,
        90,
        "synthetic-monitor",
        "input",
    )
    scene.box(
        "agents",
        80,
        470,
        300,
        90,
        "node log agents",
        "runtime",
    )
    scene.box(
        "collector",
        560,
        230,
        320,
        120,
        "OTel Collector\n:4317 gRPC / :4318 HTTP",
        "runtime",
    )
    scene.box("prometheus", 560, 500, 320, 100, "Prometheus\nSLO rules", "analysis")
    scene.box("grafana", 560, 700, 320, 100, "Grafana\n:3000", "analysis")
    scene.box("tempo", 1040, 150, 380, 85, "Tempo\ntraces", "analysis")
    scene.box("loki", 1040, 270, 380, 85, "Loki\nlogs", "analysis")

    scene.arrow("services-collector", (380, 230), (560, 265), "OTLP", (455, 210))
    scene.arrow("synthetic-collector", (380, 375), (560, 300), "OTLP", (480, 320))
    scene.arrow("agents-collector", (380, 515), (560, 325), "logs", (430, 455))
    scene.arrow(
        "collector-prometheus",
        (720, 350),
        (720, 500),
        "remote write",
        (790, 415),
    )
    scene.arrow("collector-tempo", (880, 255), (1040, 192), "traces", (920, 190))
    scene.arrow("collector-loki", (880, 300), (1040, 312), "logs", (920, 278))
    scene.arrow("tempo-grafana", (1040, 235), (880, 730), "traces", (930, 430))
    scene.arrow("loki-grafana", (1040, 355), (880, 760), "logs", (980, 620))
    scene.arrow(
        "prometheus-grafana",
        (720, 600),
        (720, 700),
        "queries",
        (775, 640),
    )

    scene.text(
        "note",
        80,
        910,
        1360,
        "payment-gateway also exposes one direct Prometheus scrape; every other signal uses the collector.",
        NOTE_FONT,
    )
    return scene


def incident_path() -> Scene:
    """Burn-rate alerts become a postmortem through one webhook."""
    scene = Scene()
    scene.text(
        "title",
        80,
        40,
        1360,
        "Burn-rate alerts become postmortems",
        TITLE_FONT,
    )
    scene.box("prometheus", 80, 170, 300, 110, "Prometheus\nSLO rules", "analysis")
    scene.box("alertmanager", 480, 170, 320, 110, "Alertmanager", "risk")
    scene.box("incident", 900, 170, 360, 110, "incident-engine\n:8088", "runtime")
    scene.box("postmortem", 900, 400, 360, 100, "postmortem.md\n/postmortems", "neutral")

    scene.arrow(
        "prometheus-alertmanager",
        (380, 225),
        (480, 225),
        "alerts",
        (430, 196),
    )
    scene.arrow(
        "alertmanager-incident",
        (800, 225),
        (900, 225),
        "webhook",
        (850, 196),
    )
    scene.arrow(
        "incident-postmortem",
        (1080, 280),
        (1080, 390),
        "writes",
        (1140, 325),
    )

    scene.text(
        "note",
        80,
        460,
        760,
        "incident-engine queries Tempo, Loki, and Prometheus for the timeline.",
        NOTE_FONT,
    )
    return scene


def main() -> None:
    """Write both scenes next to this script."""
    request_path().write(FIGURE_DIR / "request-path.excalidraw")
    telemetry_path().write(FIGURE_DIR / "telemetry-path.excalidraw")
    incident_path().write(FIGURE_DIR / "incident-path.excalidraw")


if __name__ == "__main__":
    main()
