import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    dedupe: ["react", "react-dom", "lucide-react", "recharts"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: true,
    fs: {
      allow: [path.resolve(__dirname, "..")],
    },
    proxy: {
      "/api": {
        target: "http://localhost:8020",
        changeOrigin: true,
        configure: (proxy) => {
          // 确保所有 HTTP 方法（包括 DELETE、PUT、PATCH）都被正确代理
          proxy.on("error", (err) => {
            console.error("Vite proxy error:", err.message);
          });
        },
      },
      "/ws": {
        target: "ws://localhost:8020",
        ws: true,
      },
    },
  },
})
