/// <reference types="vite/client" />

declare module "cytoscape-dagre";

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
