# V2 frontend third-party sources

The V2 Vue layer intentionally keeps its dependency set small.

| Dependency/source | Version | License | Purpose |
| --- | --- | --- | --- |
| Vue | 3.5.29 | MIT | NiceGUI-compatible Vue SFC runtime |
| Vite | 7.3.2 | MIT | SFC build pipeline |
| Tailwind CSS | 4.2.1 | MIT | Vue component layout and visual utilities |
| motion-v | 2.3.0 | MIT | Vue motion engine; version aligned with the Inspira UI baseline reviewed for V2 |
| Inspira UI InteractiveGridPattern | reviewed 2026-10-22 | MIT | Source inspiration for `VpnInteractiveGrid.vue`; adapted locally rather than importing the entire library |

Inspira UI upstream: `unovue/inspira-ui`.
The adapted component retains an attribution comment and remains presentation-only.
