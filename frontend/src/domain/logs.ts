import type { LegacyApi } from "../api/legacy";
import { failed, idleOperation, type Operation, started, succeeded } from "./operation";
import { Store } from "./store";

/**
 * "Open log folder": the path is always the `log_dir` the server returned in GET /api/settings,
 * passed unchanged to the existing POST /api/open-folder. The UI never builds a path itself and
 * never reads or uploads log contents.
 */
export class LogFolderController {
  readonly store = new Store<{ open: Operation }>({ open: idleOperation });

  constructor(private readonly api: LegacyApi) {}

  async open(serverLogDir: string | null): Promise<boolean> {
    const state = this.store.get();
    if (serverLogDir === null || serverLogDir === "" || state.open.phase === "pending") return false;
    this.store.patch({ open: started(state.open) });
    try {
      await this.api.openFolder(serverLogDir);
      this.store.patch({ open: succeeded(this.store.get().open) });
      return true;
    } catch (error) {
      this.store.patch({ open: failed(this.store.get().open, error) });
      return false;
    }
  }
}
