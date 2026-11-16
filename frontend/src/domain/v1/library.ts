import type { ApiError } from "../../api/errors";
import { MutationIntent } from "../../api/idempotency";
import type { CollectionKind, JsonObject, OutputFormatV1, PolicyV1, PresetV1, V1ResourcesApi } from "../../api/v1Resources";
import { classifyStartFailure } from "../admission";
import { toApiError } from "../operation";
import { Store } from "../store";
import { type PresetDraft, presetBody, policyBody, type ScreenDifferences } from "./settings";

/**
 * S5-A: saved B5 Presets/Policies (id + revision) and the screen's differences from the Preset.
 *
 * - Lists are read on start and on explicit reload, every page (`truncated` when there are more
 *   than the client reads — the view says so instead of cutting silently).
 * - Choosing an entry reads `GET /{collection}/{id}?revision=N` and freezes that exact revision.
 *   A newer revision seen later is only *reported*; the selection is never swapped silently.
 * - Nothing is created or updated implicitly: start-up, adding inputs and starting a job never
 *   write. Creating is an explicit action with its own Idempotency-Key per intent; an unknown
 *   outcome keeps the same key + body for an explicit re-check (no automatic resend).
 */

export interface ListState<T> {
  phase: "idle" | "pending" | "done" | "error";
  items: readonly T[];
  truncated: boolean;
  error: ApiError | null;
}

export interface Selection<T> {
  phase: "idle" | "pending" | "done" | "error";
  /** The frozen revision (null until read). */
  item: T | null;
  /** The list shows a newer revision of the selected id (not adopted automatically). */
  newerRevision: number | null;
  /** The selected id is no longer listed (deleted or unreadable). */
  missing: boolean;
  error: ApiError | null;
}

export interface CreateState {
  phase: "idle" | "pending" | "rejected" | "unknown";
  error: ApiError | null;
  /** The intent of the last attempt (kept while unknown, for an explicit re-check). */
  intent: MutationIntent<JsonObject> | null;
}

export interface LibraryState {
  presets: ListState<PresetV1>;
  policies: ListState<PolicyV1>;
  preset: Selection<PresetV1>;
  policy: Selection<PolicyV1>;
  screen: ScreenDifferences;
  createPreset: CreateState;
  createPolicy: CreateState;
}

const emptyList = { phase: "idle", items: [], truncated: false, error: null } as const;
const emptySelection = { phase: "idle", item: null, newerRevision: null, missing: false, error: null } as const;
const idleCreate: CreateState = { phase: "idle", error: null, intent: null };

type Kind = "preset" | "policy";
const collection = (kind: Kind): CollectionKind => (kind === "preset" ? "presets" : "execution-policies");
const listKey = (kind: Kind) => (kind === "preset" ? "presets" : "policies") as "presets" | "policies";
const createKey = (kind: Kind) => (kind === "preset" ? "createPreset" : "createPolicy") as "createPreset" | "createPolicy";

export class V1LibraryController {
  readonly store = new Store<LibraryState>({
    presets: emptyList,
    policies: emptyList,
    preset: emptySelection,
    policy: emptySelection,
    screen: { language: null, formats: null },
    createPreset: idleCreate,
    createPolicy: idleCreate,
  });
  private generation = { preset: 0, policy: 0 };

  constructor(private readonly api: V1ResourcesApi) {}

  /** Read both lists (GET only). Selections keep their frozen revision. */
  async load(): Promise<void> {
    await Promise.all([this.loadList("preset"), this.loadList("policy")]);
  }

  async loadList(kind: Kind): Promise<void> {
    const key = listKey(kind);
    this.store.patch({ [key]: { ...this.store.get()[key], phase: "pending", error: null } } as Partial<LibraryState>);
    try {
      const all = await this.api.listAll(collection(kind));
      this.store.patch({ [key]: { phase: "done", items: all.items, truncated: all.truncated, error: null } } as Partial<LibraryState>);
      this.reconcile(kind);
    } catch (error) {
      this.store.patch({ [key]: { ...this.store.get()[key], phase: "error", error: toApiError(error) } } as Partial<LibraryState>);
    }
  }

  /** Freeze the listed revision of `id` (an explicit choice). */
  async select(kind: Kind, id: string | null): Promise<void> {
    const generation = ++this.generation[kind];
    if (id === null) {
      this.store.patch({ [kind]: emptySelection } as Partial<LibraryState>);
      return;
    }
    const listed = (this.store.get()[listKey(kind)].items as readonly (PresetV1 | PolicyV1)[]).find((i) => i.id === id);
    if (!listed) return;
    await this.freeze(kind, id, listed.revision, generation);
  }

  /** Adopt the newer revision the list shows — only when the person asks. */
  async adoptNewer(kind: Kind): Promise<void> {
    const sel = this.store.get()[kind];
    if (!sel.item || sel.newerRevision === null) return;
    await this.freeze(kind, sel.item.id, sel.newerRevision, ++this.generation[kind]);
  }

  setLanguage(language: string | null): void {
    this.store.patch({ screen: { ...this.store.get().screen, language } });
  }

  setFormats(formats: readonly OutputFormatV1[] | null): void {
    this.store.patch({ screen: { ...this.store.get().screen, formats: formats === null ? null : [...formats] } });
  }

  /** Explicit "save this Preset" (after the confirmation step). New intent = new key. */
  async createPreset(draft: PresetDraft): Promise<boolean> {
    return this.create("preset", new MutationIntent(presetBody(draft)));
  }

  async createPolicy(name: string): Promise<boolean> {
    return this.create("policy", new MutationIntent(policyBody(name)));
  }

  /** Explicit re-check of an unknown create: the same key and the same body, once. */
  async recheckCreate(kind: Kind): Promise<boolean> {
    const state = this.store.get()[createKey(kind)];
    if (state.phase !== "unknown" || state.intent === null) return false;
    return this.create(kind, state.intent);
  }

  dismissCreate(kind: Kind): void {
    if (this.store.get()[createKey(kind)].phase === "pending") return;
    this.store.patch({ [createKey(kind)]: idleCreate } as Partial<LibraryState>);
  }

  private async create(kind: Kind, intent: MutationIntent<JsonObject>): Promise<boolean> {
    const key = createKey(kind);
    if (this.store.get()[key].phase === "pending") return false;
    this.store.patch({ [key]: { phase: "pending", error: null, intent } } as Partial<LibraryState>);
    try {
      const created = (await this.api.create(collection(kind), intent)) as PresetV1 | PolicyV1;
      this.store.patch({ [key]: idleCreate } as Partial<LibraryState>);
      await this.loadList(kind);
      // The created revision is chosen explicitly (it is what the person just confirmed).
      await this.freeze(kind, created.id, created.revision, ++this.generation[kind]);
      return true;
    } catch (error) {
      const apiError = toApiError(error);
      this.store.patch({ [key]: { phase: classifyStartFailure(apiError), error: apiError, intent } } as Partial<LibraryState>);
      return false;
    }
  }

  private async freeze(kind: Kind, id: string, revision: number, generation: number): Promise<void> {
    this.store.patch({ [kind]: { ...emptySelection, phase: "pending" } } as Partial<LibraryState>);
    try {
      const item = await this.api.getRevision(collection(kind), id, revision);
      if (generation !== this.generation[kind]) return;
      this.store.patch({ [kind]: { ...emptySelection, phase: "done", item } } as Partial<LibraryState>);
      this.reconcile(kind);
    } catch (error) {
      if (generation !== this.generation[kind]) return;
      this.store.patch({ [kind]: { ...emptySelection, phase: "error", error: toApiError(error) } } as Partial<LibraryState>);
    }
  }

  /** Report (never apply) list changes relative to the frozen selection. */
  private reconcile(kind: Kind): void {
    const s = this.store.get();
    const sel = s[kind];
    const list = s[listKey(kind)];
    if (!sel.item || list.phase !== "done") return;
    const listed = (list.items as readonly (PresetV1 | PolicyV1)[]).find((i) => i.id === sel.item!.id);
    const missing = !listed && !list.truncated;
    const newerRevision = listed && listed.revision > sel.item.revision ? listed.revision : null;
    if (missing !== sel.missing || newerRevision !== sel.newerRevision) this.store.patch({ [kind]: { ...sel, missing, newerRevision } } as Partial<LibraryState>);
  }
}
