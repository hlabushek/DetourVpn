import { defineConfig } from "vite";
export default defineConfig({
    base: "/mini-app/",
    server: { proxy: { "/api/": "http://127.0.0.1:18767" } },
});
