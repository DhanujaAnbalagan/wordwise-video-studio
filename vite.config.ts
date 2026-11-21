// This meta-config package bundles: TanStack Start + devtools, viteReact, tailwindcss,
// tsConfigPaths, nitro (build-only, cloudflare target), VITE_* env injection, @ path alias,
// React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// Do NOT add these plugins manually or the app will break with duplicate plugins.
// Never set ssr.external or resolve.external: the Worker has no runtime module resolution.
// Never add compression: hosting already compresses at the edge.
// Pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

export default defineConfig({
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
});
