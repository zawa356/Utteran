/**
 * In-memory stand-in for `/api/v1/worksets` with the wire shapes and rules of
 * `src/utteran_gui/worksets_store.py` (CAS 412, identical PUT = no new revision, Idempotency-Key
 * replay, 20-workset limit, last-workset refusal, absolute active pointer). Synthetic values only;
 * the real store is covered by `tests/test_eb_worksets.py` and the Playwright E2E.
 */
import { canonical } from "../domain/v1/settings";
import { json, type Reply } from "./fakeFetch";

type Json = Record<string, unknown>;

export interface FakeWorkset {
  id: string;
  name: string;
  revision: number;
  state: Json;
  updatedAt: string;
}

export interface FakeWorksets {
  exists: boolean;
  items: FakeWorkset[];
  active: { id: string; mode: string; revision: number } | null;
  /** Next reply override for "METHOD /path" (consumed once). The request is applied first when `apply`. */
  inject: Map<string, { reply: Reply; apply?: boolean }>;
  /** Paths reported missing by `input-status`. */
  missing: Set<string>;
  /** Every request line, in order. */
  lines: string[];
  /** Applied writes (excluding replays and refusals). */
  applied: string[];
  handle(url: string, init: RequestInit): Promise<Response>;
  /** Another window: replace a workset's state directly (bumps the revision). */
  external(id: string, state: Json, name?: string): void;
}

export function emptyWire(): Json {
  return { schema_version: 1, preset_ref: null, policy_ref: null, common: { language: null, formats: null, output_dir: "", output_source: null }, inputs: [], primary_index: null, glossaries: [] };
}

export function fakeWorksets(options: { exists?: boolean } = {}): FakeWorksets {
  let counter = 0;
  let clock = Date.parse("2026-11-24T00:00:00Z");
  const now = () => new Date((clock += 1000)).toISOString();
  const newId = () => "ws_" + (++counter).toString(16).padStart(32, "0");
  const replays = new Map<string, { body: string; id: string }>();
  const summary = (w: FakeWorkset) => ({ id: w.id, name: w.name, revision: w.revision, created_at: w.updatedAt, updated_at: w.updatedAt, state_bytes: canonical(w.state).length, input_count: (w.state.inputs as unknown[]).length });
  const doc = (w: FakeWorkset, status = 200) => json(status, { schema_version: 1, ...summary(w), state: w.state }, { ETag: `"${w.revision}"` });
  const error = (status: number, code: string) => json(status, { schema_version: 1, error: { code, message_key: `worksets.${code}`, request_id: "r", retryable: status === 503, issues: [] } });
  const limits = { max_worksets: 20, max_inputs: 100, max_state_bytes: 8388608, max_store_state_bytes: 67108864, recovery_generations: 2 };
  const listing = () =>
    json(200, {
      schema_version: 1,
      exists: state.exists,
      items: state.items.map(summary),
      active: state.active ? { workset_id: state.active.id, view: { mode: state.active.mode }, revision: state.active.revision } : null,
      limits,
    });
  const state: FakeWorksets = {
    exists: options.exists ?? false,
    items: [],
    active: null,
    inject: new Map(),
    missing: new Set(),
    lines: [],
    applied: [],
    external(id, next, name) {
      const w = state.items.find((i) => i.id === id)!;
      w.state = next;
      w.name = name ?? w.name;
      w.revision += 1;
      w.updatedAt = now();
    },
    async handle(url, init) {
      const path = new URL(url, "http://x").pathname;
      const method = init.method ?? "GET";
      const line = `${method} ${path}`;
      state.lines.push(line);
      const injected = state.inject.get(line);
      if (injected) state.inject.delete(line);
      if (injected && !injected.apply) return reply(injected.reply);
      const answer = route(method, path, init);
      if (injected) return reply(injected.reply);
      return answer;
    },
  };
  async function reply(r: Reply): Promise<Response> {
    if (r instanceof Error) throw r;
    if (typeof r === "function") return r();
    return r;
  }
  function headers(init: RequestInit): Record<string, string> {
    return (init.headers ?? {}) as Record<string, string>;
  }
  function body(init: RequestInit): Json {
    return JSON.parse((init.body as string) ?? "{}") as Json;
  }
  function route(method: string, path: string, init: RequestInit): Response {
    if (path === "/api/v1/worksets" && method === "GET") return listing();
    if (path === "/api/v1/worksets/bootstrap" && method === "POST") {
      state.exists = true;
      if (state.items.length === 0) {
        const w = { id: newId(), name: String(body(init).name), revision: 1, state: emptyWire(), updatedAt: now() };
        state.items.push(w);
        state.active = { id: w.id, mode: "normal", revision: 1 };
        state.applied.push(line("bootstrap"));
      }
      return listing();
    }
    if (path === "/api/v1/worksets" && method === "POST") {
      const key = headers(init)["Idempotency-Key"];
      if (!key) return error(400, "idempotency_key_invalid");
      const text = canonical(body(init));
      const seen = replays.get(key);
      if (seen) return seen.body === text ? doc(state.items.find((i) => i.id === seen.id)!, 201) : error(409, "idempotency_conflict");
      if (state.items.length >= 20) return error(409, "workset_limit");
      const b = body(init);
      const w = { id: newId(), name: String(b.name), revision: 1, state: (b.state as Json | undefined) ?? emptyWire(), updatedAt: now() };
      state.items.push(w);
      replays.set(key, { body: text, id: w.id });
      state.applied.push(line("create"));
      return doc(w, 201);
    }
    if (path === "/api/v1/worksets/active" && method === "PUT") {
      const b = body(init);
      const id = String(b.workset_id);
      if (!state.items.some((i) => i.id === id)) return error(404, "workset_not_found");
      const mode = String((b.view as Json).mode);
      if (!state.active || state.active.id !== id || state.active.mode !== mode) state.active = { id, mode, revision: (state.active?.revision ?? 0) + 1 };
      return json(200, { schema_version: 1, workset_id: id, view: { mode }, revision: state.active.revision });
    }
    if (path === "/api/v1/worksets/input-status" && method === "POST") {
      const paths = body(init).paths as string[];
      return json(200, { schema_version: 1, items: paths.map((p) => ({ state: state.missing.has(p) ? "missing" : "file" })) });
    }
    const match = /^\/api\/v1\/worksets\/(ws_[0-9a-f]{32})(\/recovery)?$/.exec(path);
    const w = match ? state.items.find((i) => i.id === match[1]) : undefined;
    if (!match || !w) return error(404, "workset_not_found");
    if (match[2]) return json(200, { schema_version: 1, workset_id: w.id, items: [] });
    if (method === "GET") return doc(w);
    const expected = Number(/^"(\d+)"$/.exec(headers(init)["If-Match"] ?? "")?.[1] ?? NaN);
    if (Number.isNaN(expected)) return error(428, "if_match_required");
    if (expected !== w.revision) return error(412, "revision_conflict");
    if (method === "PUT") {
      const b = body(init);
      if (canonical(b.state) !== canonical(w.state) || b.name !== w.name) {
        w.state = b.state as Json;
        w.name = String(b.name);
        w.revision += 1;
        w.updatedAt = now();
        state.applied.push(line("put"));
      }
      return doc(w);
    }
    if (method === "DELETE") {
      if (state.items.length <= 1) return error(409, "workset_last");
      state.items = state.items.filter((i) => i !== w);
      if (state.active?.id === w.id) state.active = { ...state.active, id: state.items[0]!.id, revision: state.active.revision + 1 };
      state.applied.push(line("delete"));
      return listing();
    }
    return error(405, "method_not_allowed");
  }
  function line(kind: string): string {
    return kind;
  }
  return state;
}
