# Architecture figures

The architecture diagrams are hand-drawn Excalidraw scenes in `docs/figures`.
Every scene has a rendered PNG beside it, and both files ship together.

## Files

| File | Claim |
|---|---|
| `request-path.excalidraw` / `.png` | Checkout fans out to four services over HTTP and gRPC. |
| `telemetry-path.excalidraw` / `.png` | One OTLP collector feeds Tempo, Loki, and Prometheus, and Grafana queries all three. |
| `incident-path.excalidraw` / `.png` | Burn-rate alerts reach Alertmanager, then the incident engine writes a postmortem. |

`build_figures.py` generates the scenes. The generator is the source of truth:
edit it and rebuild instead of editing scene JSON or PNG files by hand.

## Regenerate

1. Enter the development shell.
2. Put the Excalidraw CLI on `PATH`. On macOS, also set
   `EXCALIDRAW_BROWSER_PATH` to a Chrome or Chromium binary.
3. Run `make figures`.
4. Inspect every PNG at page width before committing.

```bash
export EXCALIDRAW_BROWSER_PATH="/path/to/Google Chrome"
make figures
```

If `excalidraw` is not on `PATH`, point `EXCALIDRAW` at the binary instead:

```bash
make figures EXCALIDRAW=/nix/store/.../bin/excalidraw
```

## Style

The palette comes from the house drawing rules. Strokes carry the meaning and
fills stay pale.

| Role | Stroke | Fill | Use |
|---|---|---|---|
| Input | `#1971c2` | `#d0ebff` | Load generators and telemetry sources |
| Runtime | `#9c36b5` | `#f3d9fa` | Long-running application and pipeline code |
| Analysis | `#f08c00` | `#fff3bf` | Stores, recording rules, and query surfaces |
| Risk | `#e03131` | `#ffe3e3` | Alerting |
| Model | `#0c8599` | `#c5f6fa` | Simulated or mock services |
| Neutral | `#495057` | `#f1f3f5` | Infrastructure with no special role |

Every scene uses roughness 2, hachure fills, 2 unit strokes, the Virgil hand
font, and a white background. Labels stay at 6 words or fewer. Arrows carry a
label near the line. Body text is 20 to 22 units, notes are 18, and titles are
32.

## Check before committing

- The figure makes one claim.
- Every label fits inside its box.
- Every arrow has a label near the line.
- No arrow crosses text.
- The smallest text is readable at page width.
- The scene and the PNG are both present.
