import { createHttpClient, type FetchLike } from "../api/http";
import { createLegacyApi, type LegacyApi } from "../api/legacy";
import type { HostBridge } from "../host";
import { EnvironmentController } from "./environment";
import { LogFolderController } from "./logs";
import { SettingsController } from "./settings";
import { ShellController } from "./shell";
import { TokenController } from "./token";
import { WizardStatusController } from "./wizard";

/** Everything the views use, created once and outside Vue (mode switches keep it). */
export interface AppCore {
  host: HostBridge;
  legacy: LegacyApi;
  shell: ShellController;
  environment: EnvironmentController;
  settings: SettingsController;
  token: TokenController;
  logs: LogFolderController;
  wizard: WizardStatusController;
  /** Start-up reads: settings first, then the environment for the saved default profile. */
  start(): Promise<void>;
}

export function createCore(deps: { fetch: FetchLike; host: HostBridge }): AppCore {
  const legacy = createLegacyApi(createHttpClient({ fetch: deps.fetch }));
  const environment = new EnvironmentController(legacy);
  const settings = new SettingsController(legacy);
  // A saved default profile is shown on the environment panel only if the panel was following
  // the default (a profile the person picked there is kept). Saving and the environment check
  // are separate operations with separate states; no device re-detection is started.
  settings.onSaved((before, after) => {
    const env = environment.store.get();
    if (before.defaultProfile === after.defaultProfile || !env.followsDefault || env.load.phase === "idle") return;
    void environment.selectDefault(after.defaultProfile);
  });
  return {
    host: deps.host,
    legacy,
    shell: new ShellController(),
    environment,
    settings,
    token: new TokenController(legacy),
    logs: new LogFolderController(legacy),
    wizard: new WizardStatusController(legacy),
    async start() {
      await settings.load();
      if (environment.store.get().load.phase === "idle") {
        await environment.selectDefault(settings.store.get().saved?.defaultProfile ?? null);
      }
    },
  };
}
