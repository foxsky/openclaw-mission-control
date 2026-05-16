declare module "openclaw/plugin-sdk" {
  export type OpenClawPluginApi = {
    pluginConfig?: Record<string, unknown>;
    runtime: any;
    logger?: {
      info?: (message: string) => void;
      warn?: (message: string) => void;
      error?: (message: string) => void;
      debug?: (message: string) => void;
    };
    on: (hookName: string, handler: (...args: any[]) => any, opts?: any) => void;
  };

  export function emptyPluginConfigSchema(): any;
}
