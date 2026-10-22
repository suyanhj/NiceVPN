import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";
import checker from "vite-plugin-checker";
import cssInjectedByJsPlugin from "vite-plugin-css-injected-by-js";

export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    checker({ vueTsc: true }),
    cssInjectedByJsPlugin(),
  ],
  build: {
    cssCodeSplit: false,
    outDir: "../dist",
    emptyOutDir: true,
    lib: {
      entry: {
        VpnSpotlightSurface: "./src/VpnSpotlightSurface.vue",
        VpnInteractiveGrid: "./src/VpnInteractiveGrid.vue",
      },
      formats: ["es"],
      fileName: (_format, entryName) => `${entryName}.js`,
      name: "nicevpn-ui-v2",
    },
    minify: true,
    rollupOptions: {
      external: ["vue"],
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "[name]-[hash].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
