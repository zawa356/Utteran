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
  /**
   * E-B: the chosen id + revision, kept even while it cannot be read (pending, error, deleted).
   * A workset saves this, never a guessed "latest"; only the person replaces it.
   */
  ref: { readonly id: string; readonly revision: number } | null;
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
const emptySelection = { phase: "idle", ref: null, item: null, newerRevision: null, missing: false, error: null } as const;
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
  private listGeneration = { preset: 0, policy: 0 };
  private generation = { preset: 0, policy: 0 };

  constructor(private readonly api: V1ResourcesApi) {}

  /** Read both lists (GET only). Selections keep their frozen revision. */
  async load(): Promise<void> {
    await Promise.all([this.loadList("preset"), this.loadList("policy")]);
  }

  async loadList(kind: Kind): Promise<void> {
    const key = listKey(kind);
    const generation = ++this.listGeneration[kind];
    this.store.patch({ [key]: { ...this.store.get()[key], phase: "pending", error: null } } as Partial<LibraryState>);
    try {
      const all = await this.api.listAll(collection(kind));
      if (generation !== this.listGeneration[kind]) return;
      this.store.patch({ [key]: { phase: "done", items: all.items, truncated: all.truncated, error: null } } as Partial<LibraryState>);
      this.reconcile(kind);
    } catch (error) {
      if (generation !== this.listGeneration[kind]) return;
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

  /**
   * E-B: re-freeze saved references exactly (id + revision), whether or not they are still
   * listed: B5 keeps past revisions readable after a tombstone, and a deleted or unreadable one
   * stays selected as missing/error until the person chooses another. Screen differences are
   * restored in the same step.
   */
  async restore(preset: { id: string; revision: number } | null, policy: { id: string; revision: number } | null, screen: ScreenDifferences): Promise<void> {
    this.store.patch({ screen: { language: screen.language, formats: screen.formats === null ? null : [...screen.formats] } });
    const one = async (kind: Kind, ref: { id: string; revision: number } | null) => {
      const generation = ++this.generation[kind];
      if (ref === null) {
        this.store.patch({ [kind]: emptySelection } as Partial<LibraryState>);
        return;
      }
      await this.freeze(kind, ref.id, ref.revision, generation);
    };
    await Promise.all([one("preset", preset), one("policy", policy)]);
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

  /** Duplicating a saved Preset creates a new resource with the full settings. */
  async duplicatePreset(name: string, settings: JsonObject): Promise<boolean> {
    return this.create("preset", new MutationIntent({ schema_version: 1, name: name.trim(), settings }));
  }

  getGlossarySupport() {
    return this.api.getGlossarySupport();
  }

  async getLatestPreset(id: string): Promise<PresetV1> {
    try {
      const latest = await this.api.getLatestPreset(id);
      const selected = this.store.get().preset;
      if (selected.item?.id === id) {
        this.store.patch({ preset: { ...selected, missing: false, newerRevision: latest.revision > selected.item.revision ? latest.revision : null } });
      }
      return latest;
    } catch (error) {
      const selected = this.store.get().preset;
      if (selected.item?.id === id && toApiError(error).status === 404) {
        this.store.patch({ preset: { ...selected, missing: true, newerRevision: null } });
      }
      throw error;
    }
  }

  async revisePreset(base: PresetV1, name: string, settings: JsonObject): Promise<void> {
    const updated = await this.api.revisePreset(base.id, base.revision, { schema_version: 1, name: name.trim(), settings });
    const selected = this.store.get().preset;
    if (selected.item?.id === base.id && selected.item.revision < updated.revision) {
      this.store.patch({ preset: { ...selected, newerRevision: Math.max(selected.newerRevision ?? 0, updated.revision) } });
    }
    await this.loadList("preset");
  }

  async deletePreset(base: PresetV1): Promise<void> {
    await this.api.deletePreset(base.id, base.revision);
    const selected = this.store.get().preset;
    if (selected.item?.id === base.id) this.store.patch({ preset: { ...selected, missing: true, newerRevision: null } });
    await this.loadList("preset");
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
    const current = this.store.get()[key];
    if (current.phase === "pending" || (current.phase === "unknown" && current.intent !== intent)) return false;
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
    const ref = Object.freeze({ id, revision });
    this.store.patch({ [kind]: { ...emptySelection, phase: "pending", ref } } as Partial<LibraryState>);
    try {
      const item = await this.api.getRevision(collection(kind), id, revision);
      if (generation !== this.generation[kind]) return;
      this.store.patch({ [kind]: { ...emptySelection, phase: "done", ref, item } } as Partial<LibraryState>);
      this.reconcile(kind);
    } catch (error) {
      if (generation !== this.generation[kind]) return;
      const apiError = toApiError(error);
      // A revision that cannot be read (deleted/tombstone or never existed) stays referenced.
      this.store.patch({ [kind]: { ...emptySelection, phase: "error", ref, missing: apiError.status === 404, error: apiError } } as Partial<LibraryState>);
    }
  }

  /** Report (never apply) list changes relative to the frozen selection. */
  private reconcile(kind: Kind): void {
    const s = this.store.get();
    const sel = s[kind];
    const list = s[listKey(kind)];
    if (!sel.item || list.phase !== "done") return;
    const listed = (list.items as readonly (PresetV1 | PolicyV1)[]).find((i) => i.id === sel.item!.id);
    const missing = !listed && (!list.truncated || sel.missing);
    const newerRevision = listed && listed.revision > sel.item.revision ? listed.revision : list.truncated && !listed ? sel.newerRevision : null;
    if (missing !== sel.missing || newerRevision !== sel.newerRevision) this.store.patch({ [kind]: { ...sel, missing, newerRevision } } as Partial<LibraryState>);
  }
}
