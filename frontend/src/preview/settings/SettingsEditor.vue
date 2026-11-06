<script setup lang="ts">
import { mdiAlertCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { CATEGORIES, type FieldDef, fieldsOf, FOLDED_SECTIONS } from "../domain/settingsSchema";
import type { PreviewKey } from "../i18n";
import OutputDirField from "../output/OutputDirField.vue";
import SettingRow from "./SettingRow.vue";
import { type FieldMode, provideSettingAccess } from "./useSettingAccess";

/**
 * The Expert processing-settings editor (iteration 4, owner memo #19): five categories instead of
 * one long form. Switching category never loses an input — values live in the demo store.
 * Inside a category, fields are grouped in short sections; detailed ones start folded. Only the
 * fields that apply to the chosen backend are shown (the rest stay stored but unused).
 */
const props = defineProps<{ mode: Exclude<FieldMode, "normal"> }>();
const { demo, layout, state, t } = usePreview();
const access = provideSettingAccess(() => props.mode);
const category = computed(() => state.value.inspectorCategory);

const tabs = computed(() =>
  CATEGORIES.map((id) => ({ id, title: t(`cat.${id}` as PreviewKey), issues: access.categoryIssues(id), overrides: access.categoryOverrides(id) })),
);

interface Section {
  id: string;
  folded: boolean;
  fields: FieldDef[];
}
const sections = computed<Section[]>(() => {
  const out: Section[] = [];
  for (const def of fieldsOf(category.value)) {
    if (access.visibility(def) !== "shown") continue;
    let sec = out.find((s) => s.id === def.section);
    if (!sec) out.push((sec = { id: def.section, folded: FOLDED_SECTIONS.has(def.section), fields: [] }));
    sec.fields.push(def);
  }
  return out;
});
const partial = computed(() => fieldsOf(category.value).some((d) => access.visibility(d) === "partial"));
const backend = computed(() => (access.mixed("backend") ? null : access.value("backend")));
const engineNote = computed(() => {
  if (category.value !== "advanced") return null;
  if (sections.value.some((s) => s.id === "whisperCpp" || s.id === "genai")) return null;
  if (backend.value === null) return null;
  return backend.value === "auto" ? t("adv.autoEngine") : t("adv.noEngineFields", { engine: access.optionLabel("backend", backend.value) });
});
const showHeadings = computed(() => category.value === "advanced" || sections.value.filter((s) => !s.folded).length > 1);
</script>

<template>
  <div class="p-editor" :data-testid="`settings-editor-${mode}`">
    <!-- A wrapping row of buttons rather than scrolling tabs: every category stays visible in a narrow pane. -->
    <div class="p-editor__cats" role="group" :aria-label="t('cat.label')" data-testid="category-tabs">
      <v-btn
        v-for="c in tabs"
        :key="c.id"
        variant="text"
        :color="c.id === category ? 'primary' : undefined"
        :class="{ 'p-editor__cat--active': c.id === category }"
        :aria-pressed="c.id === category"
        :data-testid="`category-${c.id}`"
        @click="demo.setInspectorCategory(c.id)"
      >
        {{ c.title }}
        <span v-if="c.overrides > 0" class="p-tabcount" :title="t('cat.overrides', { n: c.overrides })" :data-testid="`category-${c.id}-overrides`">{{ c.overrides }}</span>
        <v-icon v-if="c.issues > 0" :icon="mdiAlertCircleOutline" color="error" size="small" class="ml-1" :aria-label="t('cat.hasIssues')" :data-testid="`category-${c.id}-issues`" />
      </v-btn>
    </div>

    <div class="p-editor__body u-stack" role="region" :aria-label="t(`cat.${category}` as PreviewKey)" :data-testid="`category-panel-${category}`">
      <p v-if="engineNote" class="text-body-medium" data-testid="engine-note">{{ engineNote }}</p>
      <!-- #35: the output folder belongs to the file (FileInput.output_dir), not to the processing settings. -->
      <template v-if="category === 'output'">
        <OutputDirField v-if="mode === 'selection'" :input-ids="state.selected" />
        <div v-else class="u-stack" data-testid="output-dir-common">
          <div class="text-body-medium"><span class="font-weight-medium">{{ t("settings.output") }}</span> — {{ state.defaultOutputDir ?? t("output.sameAsInput") }}</div>
          <v-btn variant="text" color="primary" class="align-self-start" data-testid="output-dir-open-settings" @click="layout.navigate('settings')">{{ t("output.changeInSettings") }}</v-btn>
        </div>
      </template>
      <template v-for="sec in sections" :key="sec.id">
        <v-expansion-panels v-if="sec.folded" variant="accordion" flat class="p-editor__fold">
          <v-expansion-panel :data-testid="`section-${sec.id}`">
            <v-expansion-panel-title>{{ t(`fsec.${sec.id}` as PreviewKey) }}</v-expansion-panel-title>
            <v-expansion-panel-text>
              <div class="u-stack">
                <SettingRow v-for="def in sec.fields" :key="def.key" :def="def" />
              </div>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        <section v-else class="u-stack" :data-testid="`section-${sec.id}`">
          <h3 v-if="showHeadings" class="p-editor__heading">{{ t(`fsec.${sec.id}` as PreviewKey) }}</h3>
          <SettingRow v-for="def in sec.fields" :key="def.key" :def="def" />
        </section>
      </template>
      <p v-if="partial" class="text-body-small" data-testid="partial-hidden">{{ t("inspector.partialHidden") }}</p>
    </div>
  </div>
</template>
