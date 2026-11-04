import { createHttpClient, type FetchLike } from "../api/http";
import { createLegacyApi, type LegacyApi } from "../api/legacy";
import type { HostBridge } from "../host";
import { EnvironmentController } from "./environment";
import { SettingsController } from "./settings";
import { ShellController } from "./shell";

/** Everything the views use, created once and outside Vue (mode switches keep it). */
export interface AppCore {
  host: HostBridge;
  legacy: LegacyApi;
  shell: ShellController;
  environment: EnvironmentController;
  settings: SettingsController;
}

export function createCore(deps: { fetch: FetchLike; host: HostBridge }): AppCore {
  const legacy = createLegacyApi(createHttpClient({ fetch: deps.fetch }));
  return {
    host: deps.host,
    legacy,
    shell: new ShellController(),
    environment: new EnvironmentController(legacy),
    settings: new SettingsController(legacy),
  };
}
