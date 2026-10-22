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

Production and local startup share the Docker stages in `deploy/docker/Dockerfile`:

- `frontend-builder`: installs Node dependencies, typechecks and builds the Vue SFCs.
- `frontend-assets`: scratch export target containing only `app/ui/vue/dist/` contents.
- `runtime`: Python 3.14 image with compiled frontend assets copied in; Node/npm are absent.

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

`start.sh` reuses `build-ui.sh` and then starts `python3 main.py`.

To skip rebuilding when a valid dist already exists:

```bash
NICEVPN_BUILD_UI=0 ./start.sh
```

For frontend-only validation:

```bash
DOCKER_BUILDKIT=1 docker build \
  -f deploy/docker/Dockerfile \
  --target frontend-assets \
  --output type=local,dest=/tmp/nicevpn-ui-dist \
  .
```
