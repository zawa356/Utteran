/**
 * In-memory stand-in for the B5/B7 v1 routes, with the wire shapes of
 * `src/utteran_gui/settings_api.py` / `jobs_api.py` (synthetic values only). It enforces the
 * rules the UI relies on — Idempotency-Key replay (same key + same body → same answer; same key +
 * other body → 409), If-Match CAS on the queue/job, single-use resolution tokens — so tests see
 * duplicates if the client ever resends with a new key. Real-server behaviour is covered by the
 * Python tests and the Playwright E2E against the real FastAPI app.
 */
import template from "../domain/v1/presetTemplate.json";
import { canonical, clone } from "../domain/v1/settings";
import { type Call, json, type Reply, scriptedFetch } from "./fakeFetch";

type Json = Record<string, unknown>;
export const V1_PRESET = "pre_" + "1".repeat(32);
export const V1_POLICY = "pol_" + "2".repeat(32);
const NOW = "2026-11-12T00:00:00Z";

export function presetWire(id = V1_PRESET, revision = 1, name = "合成preset", settings: Json = clone(template) as Json): Json {
  return { schema_version: 1, id, revision, name, settings };
}
export function policyWire(id = V1_POLICY, revision = 1, name = "合成policy"): Json {
  return { schema_version: 1, id, revision, name, mode: "auto", fixed_candidate_id: null, allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null, fallback: "none", execution_deadline_seconds: null };
}
export const SPEC = {
  candidate_id: "fw-cpu.cpu.generation-1",
  recipe_id: "fw-cpu",
  profile_id: "cpu",
  profile_generation: "generation-1",
  model_id: "large-v3-turbo",
  model_revision: "revision-1",
  backend: "faster-whisper",
  requested_route: { encoder: "selected-device", decoder: "selected-device" },
  device: "cpu",
  compute_type: "int8",
  inventory_revision: 3,
  policy_ref: { id: V1_POLICY, revision: 1 },
  reason_codes: ["supported"],
  confidence: "medium",
  evidence_kinds: ["smoke"],
  warnings: [],
  fallback_candidate_ids: [],
  worker_support: "supported",
};

export interface FakeV1 {
  fetch: ReturnType<typeof scriptedFetch>;
  presets: Json[];
  policies: Json[];
  jobs: Json[];
  queue: { revision: number; dispatch: "running" | "paused" };
  /** Next reply override for "METHOD /path" (consumed once). The request is still *applied* when `apply` is true. */
  inject: Map<string, { reply: Reply; apply?: boolean }>;
  /** Server-side rejections for the next resolution (e.g. B4 not ready). */
  resolveError: { status: number; code: string; issues: { path: string; code: string }[] } | null;
  /** Writes the server applied (excluding replays). */
  applied: string[];
  calls(): string[];
  writes(): { line: string; key: string | null; body: string | undefined; ifMatch: string | null }[];
  setJobStatus(status: string): void;
  /** Synthetic durable jobs created directly in the store (oldest first, like B7 rowid order). */
  addJobs(count: number): string[];
  /** Override one `GET /api/v1/jobs` page by its offset (0 = first page); consumed once. */
  listPage: Map<number, Reply>;
  /** Layout v7 history visibility per job (`false` = the store has none: filters answer 422). */
  visibility: Map<string, { hidden: boolean; revision: number }> | false;
  /** `GET /jobs/{id}/artifacts` bodies; absent = state "none". */
  artifacts: Map<string, Json>;
  /** Set a job's status by id (bumps its revision). */
  setStatus(jobId: string, status: string): void;
  /** B7 `PUT /queue/order` result: waiting job ids in dispatch order (null = submission order). */
  queueOrder: string[] | null;
  /** Job ids whose queue item is held. */
  held: Set<string>;
}

/** B7 `list_jobs`: 100 per page, oldest first, one immutable snapshot per listing. */
export const V1_LIST_PAGE = 100;

export function fakeV1Server(options: { presets?: Json[]; policies?: Json[]; queuePaused?: boolean } = {}): FakeV1 {
  const state: FakeV1 = {
    fetch: null as never,
    presets: options.presets ?? [presetWire()],
    policies: options.policies ?? [policyWire()],
    jobs: [],
    queue: { revision: 1, dispatch: options.queuePaused === false ? "running" : "paused" },
    inject: new Map(),
    listPage: new Map(),
    addJobs: (count) => {
      const ids: string[] = [];
      for (let i = 0; i < count; i += 1) {
        const created = job({ preset_ref: { id: V1_PRESET, revision: 1 }, policy_ref: { id: V1_POLICY, revision: 1 }, files: [{ client_file_id: `f-${String(i).padStart(24, "0")}` }] });
        state.jobs.push(created);
        ids.push(created.id as string);
      }
      return ids;
    },
    resolveError: null,
    visibility: new Map(),
    artifacts: new Map(),
    queueOrder: null,
    held: new Set(),
    setStatus(jobId, status) {
      const found = state.jobs.find((j) => j.id === jobId);
      if (found) {
        found.status = status;
        found.revision = (found.revision as number) + 1;
      }
    },
    applied: [],
    calls: () => state.fetch.calls.map(line),
    writes: () =>
      state.fetch.calls
        .filter((c) => c.init.method !== "GET")
        .map((c) => {
          const h = (c.init.headers ?? {}) as Record<string, string>;
          return { line: line(c), key: h["Idempotency-Key"] ?? null, body: c.init.body as string | undefined, ifMatch: h["If-Match"] ?? null };
        }),
    setJobStatus(status) {
      const job = state.jobs.at(-1);
      if (job) {
        job.status = status;
        job.revision = (job.revision as number) + 1;
      }
    },
  };
  const replays = new Map<string, { digest: string; status: number; body: Json }>();
  const snapshots = new Map<string, { revision: number; items: Json[] }>();
  const tokens = new Map<string, { session: string; used: boolean }>();
  let counter = 0;
  const hex = (n: number) => (++counter).toString(16).padStart(n, "0");

  function line(c: Call): string {
    return `${c.init.method} ${new URL(c.url, "http://x").pathname}`;
  }
  const error = (status: number, code: string, issues: unknown[] = []) => json(status, { schema_version: 1, error: { code, message_key: code, request_id: "r", retryable: false, issues } });
  function job(session: Json, index = 0): Json {
    const file = (session.files as Json[])[index]!;
    return {
      schema_version: 1,
      id: `job_${hex(32)}`,
      revision: 1,
      status: "queued",
      held: false,
      blockers: [],
      block_reason: null,
      recovery_required: null,
      reason_code: "submitted",
      snapshot: { created_at: NOW, client_file_id: file.client_file_id, preset_ref: session.preset_ref, policy_ref: session.policy_ref, resume_mode: "resume", input_identity: `in_${"a".repeat(32)}`, spec: SPEC },
      attempts: [],
      retry_of: null,
      rerun_of: null,
      result: null,
      error: null,
      created_at: NOW,
      updated_at: NOW,
    };
  }
  // Like B7: one active item (running/cancelling, no position), then waiting items by position.
  const queueView = () => {
    const itemId = (j: Json) => `qi_${(j.id as string).slice(4)}`;
    const active = state.jobs.find((j) => j.status === "running" || j.status === "cancelling") ?? null;
    const waiting = state.jobs.filter((j) => j.status === "queued" || j.status === "held" || j.status === "blocked");
    const order = state.queueOrder;
    if (order) waiting.sort((a, b) => (order.indexOf(a.id as string) + 1 || 1e9) - (order.indexOf(b.id as string) + 1 || 1e9));
    const items = [
      ...(active ? [{ id: itemId(active), resource_id: active.id, resource_kind: "job", position: null, held: false, blockers: [] }] : []),
      ...waiting.map((j, i) => ({ id: itemId(j), resource_id: j.id, resource_kind: "job", position: i + 1, held: state.held.has(j.id as string), blockers: [] })),
    ];
    return { schema_version: 1, revision: state.queue.revision, dispatch: state.queue.dispatch, active_item_id: active ? itemId(active) : null, items };
  };

  function handle(c: Call): Reply {
    const url = new URL(c.url, "http://x");
    const method = c.init.method ?? "GET";
    const path = url.pathname;
    const headers = (c.init.headers ?? {}) as Record<string, string>;
    const body = c.init.body === undefined ? undefined : (JSON.parse(c.init.body as string) as Json);
    if (method === "GET") {
      if (path === "/api/v1/presets") return json(200, { schema_version: 1, revision: state.presets.length || 1, items: state.presets, next_cursor: null });
      if (path === "/api/v1/execution-policies") return json(200, { schema_version: 1, revision: state.policies.length || 1, items: state.policies, next_cursor: null });
      const m = /^\/api\/v1\/(presets|execution-policies)\/([a-z0-9_]+)$/.exec(path);
      if (m) {
        const list = m[1] === "presets" ? state.presets : state.policies;
        const found = list.find((r) => r.id === m[2] && String(r.revision) === url.searchParams.get("revision"));
        return found ? json(200, found, { ETag: `"${found.revision as number}"` }) : error(404, "resource_not_found");
      }
      if (path === "/api/v1/queue") return json(200, queueView(), { ETag: `"${state.queue.revision}"` });
      if (path === "/api/v1/jobs") {
        const cursor = url.searchParams.get("cursor");
        const filter = url.searchParams.get("visibility");
        if (state.visibility === false ? filter !== null && filter !== "all" : filter !== null && !["visible", "hidden", "all"].includes(filter)) return error(422, "request_invalid");
        const hiddenIds = state.visibility === false ? new Set<string>() : new Set([...state.visibility].filter(([, v]) => v.hidden).map(([id]) => id));
        const selected = state.visibility === false || filter === "all" ? state.jobs : state.jobs.filter((j) => hiddenIds.has(j.id as string) === (filter === "hidden"));
        let snapshotId: string;
        let offset = 0;
        if (cursor === null) {
          snapshotId = hex(32);
          snapshots.set(snapshotId, { revision: Math.max(state.jobs.length, 1), items: selected.map((j) => clone(j)) });
        } else {
          const m = /^j2\.([0-9a-f]{32})\.([0-9]+)\.100\.f{64}$/.exec(cursor);
          if (!m) return error(400, "cursor_invalid");
          snapshotId = m[1]!;
          offset = Number(m[2]);
        }
        const injected = state.listPage.get(offset);
        if (injected) {
          state.listPage.delete(offset);
          return injected;
        }
        if (!snapshots.has(snapshotId)) return error(409, "cursor_expired");
        const snap = snapshots.get(snapshotId)!;
        const end = offset + V1_LIST_PAGE;
        const next = end < snap.items.length ? `j2.${snapshotId}.${end}.100.${"f".repeat(64)}` : null;
        return json(200, { schema_version: 1, revision: snap.revision, items: snap.items.slice(offset, end), next_cursor: next }, { ETag: `"${snap.revision}"` });
      }
      const vis = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})\/visibility$/.exec(path);
      if (vis) {
        if (state.visibility === false) return error(409, "history_visibility_unavailable");
        if (!state.jobs.some((x) => x.id === vis[1])) return error(404, "resource_not_found");
        const v = state.visibility.get(vis[1]!) ?? { hidden: false, revision: 0 };
        return json(200, { schema_version: 1, job_id: vis[1], hidden: v.hidden, revision: v.revision, updated_at: v.revision === 0 ? null : NOW }, { ETag: `"${v.revision}"` });
      }
      const art = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})\/artifacts$/.exec(path);
      if (art) {
        if (!state.jobs.some((x) => x.id === art[1])) return error(404, "resource_not_found");
        return json(200, state.artifacts.get(art[1]!) ?? { schema_version: 1, job_id: art[1], state: "none", items: [], unexpected_count: 0 });
      }
      const j = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})$/.exec(path);
      if (j) {
        const found = state.jobs.find((x) => x.id === j[1]);
        return found ? json(200, found, { ETag: `"${found.revision as number}"` }) : error(404, "resource_not_found");
      }
      throw new Error(`unexpected GET ${path}`);
    }
    if (!path.startsWith("/api/v1/")) throw new Error(`unexpected ${method} ${path}`);
    const put = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})\/visibility$/.exec(path);
    if (put && method === "PUT") {
      // Layout v7: own revision, idempotent for the stored state, finished jobs only, no event.
      if (state.visibility === false) return error(409, "history_visibility_unavailable");
      const target = state.jobs.find((x) => x.id === put[1]);
      if (!target) return error(404, "resource_not_found");
      const v = state.visibility.get(put[1]!) ?? { hidden: false, revision: 0 };
      const hidden = (body as Json).hidden as boolean;
      const view = (x: { hidden: boolean; revision: number }) => json(200, { schema_version: 1, job_id: put[1], hidden: x.hidden, revision: x.revision, updated_at: x.revision === 0 ? null : NOW }, { ETag: `"${x.revision}"` });
      if (v.hidden === hidden) return view(v);
      if (headers["If-Match"] !== `"${v.revision}"`) return error(412, "revision_conflict");
      if (hidden && !["succeeded", "failed", "cancelled", "interrupted"].includes(target.status as string)) return error(409, "invalid_transition");
      const next = { hidden, revision: v.revision + 1 };
      state.visibility.set(put[1]!, next);
      state.applied.push(`PUT ${path}`);
      return view(next);
    }
    const key = headers["Idempotency-Key"];
    if (!key || !/^[A-Za-z0-9_.-]{16,128}$/.test(key)) return error(400, "idempotency_key_invalid");
    const scope = `${method} ${path} ${key}`;
    const digest = canonical(body);
    const replay = replays.get(scope);
    if (replay) return replay.digest === digest ? json(replay.status, replay.body) : error(409, "idempotency_conflict");
    const remember = (status: number, out: Json) => {
      replays.set(scope, { digest, status, body: out });
      state.applied.push(`${method} ${path}`);
      return json(status, out);
    };
    if (path === "/api/v1/presets" || path === "/api/v1/execution-policies") {
      const list = path === "/api/v1/presets" ? state.presets : state.policies;
      const prefix = path === "/api/v1/presets" ? "pre_" : "pol_";
      const created = { ...body!, id: prefix + hex(32), revision: 1 };
      list.push(created);
      return remember(201, created);
    }
    if (path === "/api/v1/validations") {
      const preset = state.presets.find((p) => p.id === (body!.preset_ref as Json).id && p.revision === (body!.preset_ref as Json).revision);
      if (!preset) return error(422, "settings_invalid", [{ path: "/preset_ref", code: "reference_unresolved", message_key: "x" }]);
      const apply = (target: Json, overrides: { path: string; value: unknown }[]) => {
        for (const o of overrides) {
          const parts = o.path.split("/").slice(1);
          let node = target;
          for (const p of parts.slice(0, -1)) node = node[p] as Json;
          node[parts.at(-1)!] = o.value;
        }
        return target;
      };
      const effective = apply(clone(preset.settings) as Json, body!.common_overrides as { path: string; value: unknown }[]);
      // B5 leaf inheritance: Preset <- common overrides <- this file's overrides.
      const files = (body!.files as Json[]).map((f) => ({
        client_file_id: f.client_file_id,
        valid: true,
        effective_settings: apply(clone(effective) as Json, (f.overrides ?? []) as { path: string; value: unknown }[]),
        provenance: [],
        issues: [],
        warnings: [],
        worker_support: "unverified",
      }));
      return remember(200, { schema_version: 1, valid: true, preset_ref: body!.preset_ref, policy_ref: body!.policy_ref, files });
    }
    if (path === "/api/v1/execution-resolutions") {
      if (state.resolveError) {
        const e = state.resolveError;
        state.resolveError = null;
        return error(e.status, e.code, e.issues.map((i) => ({ ...i, message_key: "x" })));
      }
      const token = `v1.${hex(32)}.${"9".repeat(13)}.${"f".repeat(64)}`;
      tokens.set(token, { session: canonical(body), used: false });
      const files = (body!.files as Json[]).map((f) => ({ client_file_id: f.client_file_id, input_identity: `in_${"a".repeat(32)}`, spec: SPEC }));
      return remember(201, { schema_version: 1, id: `res_${hex(32)}`, token, expires_at: "2099-01-01T00:00:00Z", files });
    }
    if (path === "/api/v1/jobs") {
      const record = tokens.get(body!.resolution_token as string);
      if (!record) return error(409, "resolution_invalid");
      if (record.used) return error(409, "resolution_consumed");
      if (record.session !== canonical(body!.editing_session)) return error(409, "resolution_stale");
      record.used = true;
      // B7: one job per file, all or none in one transaction.
      const session = body!.editing_session as Json;
      const created = (session.files as Json[]).map((_, index) => job(session, index));
      state.jobs.push(...created);
      state.queue.revision += 1;
      return remember(202, { schema_version: 1, jobs: created, queue_revision: state.queue.revision });
    }
    if (path === "/api/v1/queue/control") {
      if (headers["If-Match"] !== `"${state.queue.revision}"`) return error(412, "revision_conflict");
      state.queue.dispatch = body!.dispatch as "running" | "paused";
      state.queue.revision += 1;
      return remember(200, queueView());
    }
    const cancel = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})\/cancel$/.exec(path);
    if (cancel) {
      const found = state.jobs.find((x) => x.id === cancel[1]);
      if (!found) return error(404, "resource_not_found");
      if (headers["If-Match"] !== `"${found.revision as number}"`) return error(412, "revision_conflict");
      if ((body as Json).mode !== "cooperative") return error(409, "cancel_mode_unsupported");
      found.status = found.status === "running" ? "cancelling" : "cancelled";
      found.revision = (found.revision as number) + 1;
      return remember(202, found);
    }
    const retry = /^\/api\/v1\/jobs\/(job_[0-9a-f]{32})\/retry$/.exec(path);
    if (retry) {
      // B7: a new job with the same snapshot, linked by retry_of; the source is untouched.
      const source = state.jobs.find((x) => x.id === retry[1]);
      if (!source) return error(404, "resource_not_found");
      if (headers["If-Match"] !== `"${source.revision as number}"`) return error(412, "revision_conflict");
      if (!["failed", "cancelled", "interrupted"].includes(source.status as string)) return error(409, "invalid_transition");
      const created = { ...(clone(source) as Json), id: `job_${hex(32)}`, revision: 1, status: "queued", reason_code: "submitted", retry_of: source.id, rerun_of: null, result: null, error: null, attempts: [] };
      state.jobs.push(created);
      state.queue.revision += 1;
      return remember(202, created);
    }
    throw new Error(`unexpected ${method} ${path}`);
  }

  state.fetch = scriptedFetch((c) => {
    const id = `${c.init.method} ${new URL(c.url, "http://x").pathname}`;
    const injected = state.inject.get(id);
    if (injected) {
      state.inject.delete(id);
      if (injected.apply) {
        const result = handle(c);
        if (result instanceof Response) void result.text();
      }
      return injected.reply;
    }
    return handle(c);
  });
  return state;
}
