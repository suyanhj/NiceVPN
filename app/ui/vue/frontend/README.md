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

## Local development

The currently approved homepage still uses the pre-Vite NiceGUI SFC, so `python main.py` remains sufficient to preview V2.

To validate the formal frontend build layer:

```bash
cd app/ui/vue/frontend
npm install --no-audit --no-fund --package-lock=false
npm run check
```

The build output is written to `app/ui/vue/dist/`. It is intentionally ignored until the runtime wrapper is switched to compiled components in a later task.
