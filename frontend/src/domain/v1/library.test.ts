import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createV1ResourcesApi } from "../../api/v1Resources";
import { json } from "../../test/fakeFetch";
import { fakeV1Server, presetWire, V1_PRESET } from "../../test/v1Server";
import { apiModeFrom, readApiMode } from "./apiMode";
import { V1LibraryController } from "./library";
import { commonOverrides, effectiveSettings, policyBody, presetBody, presetDraftIssues } from "./settings";

function library(options: Parameters<typeof fakeV1Server>[0] = {}) {
  const server = fakeV1Server(options);
  const api = createV1ResourcesApi(createHttpClient({ fetch: server.fetch }));
  return { server, lib: new V1LibraryController(api), api };
}

/** The only part of `Document` readApiMode uses: the tags the server rendered. */
function doc(html: string): Pick<Document, "querySelectorAll"> {
  const tags = [...html.matchAll(/<meta name="utteran-api-mode" content="([^"]*)">/g)].map((m) => ({ getAttribute: (name: string) => (name === "content" ? m[1]! : null) }));
  return { querySelectorAll: (() => tags) as unknown as Document["querySelectorAll"] };
}

describe("API mode comes only from the server page", () => {
  it("exactly one meta with exactly v1-dev enables v1; everything else stays legacy", () => {
    expect(readApiMode(doc('<meta name="utteran-api-mode" content="v1-dev">'))).toBe("v1-dev");
    expect(readApiMode(doc('<meta name="utteran-api-mode" content="legacy">'))).toBe("legacy");
    expect(readApiMode(doc(""))).toBe("legacy");
    expect(readApiMode(doc('<meta name="utteran-api-mode" content="__UTTERAN_API_MODE__">'))).toBe("legacy");
    expect(readApiMode(doc('<meta name="utteran-api-mode" content="V1-DEV">'))).toBe("legacy");
    expect(readApiMode(doc('<meta name="utteran-api-mode" content=" v1-dev">'))).toBe("legacy");
    expect(readApiMode(doc('<meta name="utteran-api-mode" content="legacy"><meta name="utteran-api-mode" content="v1-dev">'))).toBe("legacy");
    expect(apiModeFrom(null)).toBe("legacy");
  });
});

describe("B5 helpers", () => {
  const preset = { id: V1_PRESET, revision: 1, name: "p", settings: presetWire().settings as Record<string, unknown> };
  it("only real differences become registered leaf overrides; effective settings replace whole leaves", () => {
    expect(commonOverrides(preset, { language: null, formats: null })).toEqual([]);
    expect(commonOverrides(preset, { language: "ja", formats: ["srt", "json", "md"] })).toEqual([]);
    const overrides = commonOverrides(preset, { language: "en", formats: ["vtt"] });
    expect(overrides).toEqual([
      { path: "/asr/language", value: "en" },
      { path: "/output/formats", value: ["vtt"] },
    ]);
    const effective = effectiveSettings(preset.settings, overrides, []) as { asr: { language: string }; output: { formats: string[] } };
    expect(effective.asr.language).toBe("en");
    expect(effective.output.formats).toEqual(["vtt"]);
    expect((preset.settings.asr as { language: string }).language).toBe("ja"); // the Preset is untouched
  });

  it("Preset/Policy bodies follow the contract and the instructions", () => {
    const body = presetBody({ name: " 会議 ", model: "large-v3-turbo", language: "en", formats: ["srt"] }) as { name: string; settings: Record<string, Record<string, unknown>> };
    expect(body.name).toBe("会議");
    expect(body.settings.asr).toMatchObject({ backend: "faster-whisper", model: "large-v3-turbo", language: "en", initial_prompt: null, glossary: [] });
    expect(body.settings.diarization!.enabled).toBe(false);
    expect(policyBody("cpu")).toEqual({ schema_version: 1, name: "cpu", mode: "auto", fixed_candidate_id: null, allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null, fallback: "none", execution_deadline_seconds: null });
    expect(presetDraftIssues({ name: "", model: "bad model", language: "xx", formats: [] })).toEqual(["name", "model", "language", "formats"]);
  });
});

describe("Preset/Policy library", () => {
  it("load is read-only; select freezes the exact revision; a newer revision is reported, not adopted", async () => {
    const { server, lib } = library();
    await lib.load();
    expect(server.writes()).toEqual([]);
    await lib.select("preset", V1_PRESET);
    expect(server.calls()).toContain(`GET /api/v1/presets/${V1_PRESET}`);
    expect(server.fetch.calls.at(-1)!.url).toBe(`/api/v1/presets/${V1_PRESET}?revision=1`);
    expect(lib.store.get().preset.item?.revision).toBe(1);
    server.presets.push(presetWire(V1_PRESET, 2, "合成preset v2"));
    server.presets.splice(0, 1);
    await lib.load();
    expect(lib.store.get().preset).toMatchObject({ item: { revision: 1 }, newerRevision: 2, missing: false });
    await lib.adoptNewer("preset");
    expect(lib.store.get().preset).toMatchObject({ item: { revision: 2 }, newerRevision: null });
    server.presets.length = 0;
    await lib.load();
    expect(lib.store.get().preset.missing).toBe(true);
  });

  it("creating is explicit; an unknown create keeps its key and body for the explicit re-check only", async () => {
    const { server, lib } = library({ presets: [], policies: [] });
    await lib.load();
    server.inject.set("POST /api/v1/presets", { reply: new TypeError("lost"), apply: true });
    expect(await lib.createPreset({ name: "会議", model: "large-v3-turbo", language: "ja", formats: ["srt"] })).toBe(false);
    expect(lib.store.get().createPreset.phase).toBe("unknown");
    expect(await lib.recheckCreate("preset")).toBe(true);
    const posts = server.writes().filter((w) => w.line === "POST /api/v1/presets");
    expect(posts).toHaveLength(2);
    expect(posts[1]!.key).toBe(posts[0]!.key);
    expect(posts[1]!.body).toBe(posts[0]!.body);
    expect(server.presets).toHaveLength(1); // the replay did not create a second one
    expect(lib.store.get().preset.item?.name).toBe("会議");
    // A second explicit create is a new intent (new key) — e.g. the same name again is allowed by B5.
    expect(await lib.createPreset({ name: "会議", model: "large-v3-turbo", language: "ja", formats: ["srt"] })).toBe(true);
    const keys = server.writes().filter((w) => w.line === "POST /api/v1/presets").map((w) => w.key);
    expect(new Set(keys).size).toBe(2);
    expect(server.presets).toHaveLength(2);
  });

  it("a 422 create is a refusal with issue pointers/codes only", async () => {
    const { server, lib } = library({ presets: [] });
    server.inject.set("POST /api/v1/presets", {
      reply: json(422, { schema_version: 1, error: { code: "settings_invalid", message_key: "x", request_id: "r", retryable: false, issues: [{ path: "/settings/asr/model", code: "unknown_model", message_key: "x" }, { path: "not a pointer", code: "x" }] } }),
    });
    await lib.createPreset({ name: "x", model: "large-v3-turbo", language: "ja", formats: ["srt"] });
    expect(lib.store.get().createPreset).toMatchObject({ phase: "rejected", error: { code: "settings_invalid", issues: [{ path: "/settings/asr/model", code: "unknown_model" }] } });
  });
});

describe("v1 parsers", () => {
  it("a GET whose ETag disagrees with the body revision is malformed; a revision read must return that revision", async () => {
    const { server, api } = library();
    server.inject.set("GET /api/v1/queue", { reply: json(200, { schema_version: 1, revision: 3, dispatch: "paused", active_item_id: null, items: [] }, { ETag: '"4"' }) });
    await expect(api.getQueue()).rejects.toMatchObject({ kind: "malformed" });
    server.inject.set(`GET /api/v1/presets/${V1_PRESET}`, { reply: json(200, presetWire(V1_PRESET, 2)) });
    await expect(api.getRevision("presets", V1_PRESET, 1)).rejects.toMatchObject({ kind: "malformed" });
  });

  it("collections are read page by page and flagged as truncated beyond the cap", async () => {
    let page = 0;
    const fetch = async () => json(200, { schema_version: 1, revision: 1, items: [presetWire("pre_" + String(page++ % 10).repeat(32))], next_cursor: "c.next" });
    const api = createV1ResourcesApi(createHttpClient({ fetch }));
    const all = await api.listAll("presets");
    expect(all.truncated).toBe(true);
    expect(all.items).toHaveLength(10);
  });
});
