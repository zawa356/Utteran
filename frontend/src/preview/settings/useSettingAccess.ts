import { computed, type ComputedRef, inject, type InjectionKey, provide } from "vue";
import { usePreview } from "../composables";
import { effectiveSettings, selectionField } from "../domain/demo";
import {
  categoryKeys,
  type FieldDef,
  issueCategory,
  type ProcessingSettings,
  type SettingIssue,
  type SettingKey,
  type SettingsCategoryId,
  validateSettings,
} from "../domain/settingsSchema";
import { isPreviewKey, type PreviewKey } from "../i18n";

/**
 * Read/write access to the processing settings for one editing target, shared by every row of an
 * editor through provide/inject:
 *  - "common": the common defaults (Expert Inspector);
 *  - "selection": individual settings of the files selected in Expert step 1;
 *  - "normal": the one file Normal mode shows (edits apply to that file only).
 */
export type FieldMode = "common" | "selection" | "normal";

export interface SettingAccess {
  mode: FieldMode;
  /** Effective settings of every target (one for common/normal, one per selected file). */
  targets: ComputedRef<ProcessingSettings[]>;
  /** Ids of the files edited (empty for the common defaults). */
  targetIds: ComputedRef<string[]>;
  value<K extends SettingKey>(key: K): ProcessingSettings[K];
  mixed(key: SettingKey): boolean;
  editable(key: SettingKey): boolean;
  set<K extends SettingKey>(key: K, value: ProcessingSettings[K]): void;
  setPatch(patch: Partial<ProcessingSettings>): void;
  /** "shown" when it applies to every target, "partial" when to some, "hidden" when to none. */
  visibility(def: FieldDef): "shown" | "partial" | "hidden";
  disabled(def: FieldDef): boolean;
  issues: ComputedRef<SettingIssue[]>;
  issueText(key: SettingKey, index?: number): string | undefined;
  categoryIssues(category: SettingsCategoryId): number;
  categoryOverrides(category: SettingsCategoryId): number;
  optionLabel(key: SettingKey, value: string): string;
}

const ACCESS: InjectionKey<SettingAccess> = Symbol("utteran-ui3-setting-access");

export function provideSettingAccess(mode: () => FieldMode, inputId: () => string | null = () => null): SettingAccess {
  const { demo, state, t } = usePreview();
  const targets = computed<ProcessingSettings[]>(() => {
    const s = state.value;
    if (mode() === "common") return [s.common];
    if (mode() === "normal") {
      const id = inputId();
      return [id && s.inputs.some((i) => i.id === id) ? effectiveSettings(s, id) : s.common];
    }
    return s.selected.length > 0 ? s.selected.map((id) => effectiveSettings(s, id)) : [s.common];
  });
  const issues = computed(() => targets.value.flatMap((x) => validateSettings(x)));
  const targetIds = computed<string[]>(() => {
    if (mode() === "common") return [];
    if (mode() === "normal") {
      const id = inputId();
      return id && state.value.inputs.some((i) => i.id === id) ? [id] : [];
    }
    return [...state.value.selected];
  });

  const access: SettingAccess = {
    get mode() {
      return mode();
    },
    targets,
    targetIds,
    value(key) {
      if (mode() === "selection") return selectionField(state.value, key).value;
      return targets.value[0]![key];
    },
    mixed(key) {
      return mode() === "selection" && selectionField(state.value, key).mixed;
    },
    editable(key) {
      return mode() !== "selection" || selectionField(state.value, key).coverage === "all";
    },
    set(key, value) {
      access.setPatch({ [key]: value } as Partial<ProcessingSettings>);
    },
    setPatch(patch) {
      if (mode() === "normal") demo.setNormalSetting(inputId(), patch);
      else if (mode() === "common") demo.setCommonPatch(patch);
      else demo.setOverridePatch(patch);
    },
    visibility(def) {
      if (!def.visible) return "shown";
      const n = targets.value.filter((x) => def.visible!(x)).length;
      return n === targets.value.length ? "shown" : n > 0 ? "partial" : "hidden";
    },
    disabled(def) {
      return Boolean(def.disabled) && targets.value.every((x) => def.disabled!(x));
    },
    issues,
    issueText(key, index) {
      const issue = issues.value.find((i) => i.key === key && (index === undefined || i.index === index));
      return issue ? t(`issue.${issue.code}` as PreviewKey, issue.params) : undefined;
    },
    categoryIssues(category) {
      return issues.value.filter((i) => issueCategory(i) === category).length;
    },
    categoryOverrides(category) {
      if (mode() !== "selection") return 0;
      const keys = new Set(categoryKeys(category));
      return [...keys].filter((k) => k !== "numSpeakers" && k !== "minSpeakers" && k !== "maxSpeakers" && selectionField(state.value, k).coverage !== "none").length;
    },
    optionLabel(key, value) {
      const own = `opt.${key}.${value}`;
      if (isPreviewKey(own)) return t(own);
      const model = `model.${value}`;
      if (key === "model" && isPreviewKey(model)) return t(model);
      return value;
    },
  };
  provide(ACCESS, access);
  return access;
}

export function useSettingAccess(): SettingAccess {
  const access = inject(ACCESS);
  if (!access) throw new Error("SettingAccess was not provided");
  return access;
}
