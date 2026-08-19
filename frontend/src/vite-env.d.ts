/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_BACKEND_PORT?: string;
  readonly VITE_FRONTEND_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
