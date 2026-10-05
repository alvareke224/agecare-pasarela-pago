import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo /api se redirige a la API FastAPI local (sin CORS).
// En Azure Static Web Apps se define VITE_API_BASE_URL con la URL pública de la API.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
