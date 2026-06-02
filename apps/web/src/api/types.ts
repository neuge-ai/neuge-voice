export type RequiredSecretKey = {
  id: string;
  is_configured: boolean;
};

export type ProviderSchema = {
  id: string;
  name: string;
  required_keys: RequiredSecretKey[];
  capabilities?: string[];
};

export type ProvidersSchema = {
  stt: ProviderSchema[];
  tts: ProviderSchema[];
  llm: ProviderSchema[];
};

export type ActiveConfig = {
  stt: string;
  tts: string;
  llm: string;
};

export type ConfigDiagnostic = {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  provider?: string | null;
  key?: string | null;
};

export type ConfigStatusResponse = {
  system_ready: boolean;
  missing_capabilities: string[];
  active_config: ActiveConfig;
  default_config: ActiveConfig;
  providers_schema: ProvidersSchema;
  config_path: string;
  diagnostics?: ConfigDiagnostic[];
};

export type AppSettings = Record<string, string>;

export type McpServerEntry = {
  name: string;
  command?: string;
  args?: string[];
};

export type McpToolsResponse = {
  servers: McpServerEntry[];
};

export type DynamicSecret = {
  id: string;
  label: string;
  categories: string[];
  is_configured: boolean;
};
