# NiceVPN UI V2 frontend layer

This directory is the build-time Vue visual layer for NiceGUI.

## Boundary

- Python/NiceGUI owns VPN business state, navigation, forms, tables, dialogs and callbacks.
- Vue owns presentation-only effects and complex browser animation.
- Vue components must not read server files, secrets, VPN configuration or call business services directly.
- Quasar remains the functional component system; this directory is not a second application frontend.

## Stack

- Vue 3
- Vite
- TypeScript
- Tailwind CSS
- motion-v
- selectively adapted Inspira UI presentation components

## Build model

Vue compilation is a development/CI concern, not a production-host concern.

- `.github/workflows/frontend-dist.yml` uses Node 22 to typecheck and build the Vue SFCs.
- The generated `app/ui/vue/dist/` is committed back to `main` as the formal runtime asset.
- Normal `start.sh`, systemd, and Docker runtime all consume that committed dist directly.
- Production hosts and production Docker builds do not require Node/npm.

The Python wrapper prefers `app/ui/vue/dist/VpnSpotlightSurface.js`. If the compiled file is absent, it falls back to the original NiceGUI-compatible SFC for development resilience.

## Standalone build

From the repository root:

```bash
./build-ui.sh
```

This only builds the Vue assets and writes them to `app/ui/vue/dist/`. It does not start Python.

A custom destination is supported for debugging or container workflows:

```bash
./build-ui.sh /tmp/nicevpn-ui-dist
```

## Local startup

```bash
./start.sh
```

`start.sh` uses the committed precompiled dist by default and then starts Python.

To explicitly rebuild the Vue assets for local development:

```bash
./start.sh --build-ui
```

`build-ui.sh` prefers local npm and falls back to Docker only when npm is unavailable.
