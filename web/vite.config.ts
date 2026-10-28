import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API's CORS allows http://localhost:5173 by default, so keep the dev server on that port.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
