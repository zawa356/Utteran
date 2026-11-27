<script setup lang="ts">
import { mdiCheck, mdiMenu } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { TOPNAV_FOLD_WIDTH } from "../../domain/shell";

/**
 * The application menu of the Design Freeze (iteration 3 memo #12, #96), v1 shape only. It offers
 * ONLY actions the product really has — adding files through the host picker, the view mode, pane
 * reset, the setup and settings pages. The prototype's sample log/diagnostics/about dialogs and the
 * repair tools are not shown here (they have no product implementation yet; see design-recovery.md).
 * A menu bar when wide, one menu button when narrow (the app bar never scrolls sideways).
 */
const core = useCore();
const { shell } = core;
const state = useStore(shell.store);
const inputs = useStore(core.inputs.store);
const { t } = useI18n();
const display = useDisplay();
const asBar = computed(() => display.width.value >= TOPNAV_FOLD_WIDTH);
const expert = computed(() => state.value.mode === "expert");

type Action = "addFile" | "modeNormal" | "modeExpert" | "resetPanes" | "setup" | "settings";
interface Item { action: Action; title: string; checked?: boolean; disabled?: boolean }
interface Group { id: "file" | "view" | "tools"; title: string; items: Item[] }

const groups = computed<Group[]>(() => {
  const view: Item[] = [
    { action: "modeNormal", title: t("ui3.menu.viewNormal"), checked: !expert.value },
    { action: "modeExpert", title: t("ui3.menu.viewExpert"), checked: expert.value },
  ];
  if (expert.value) view.push({ action: "resetPanes", title: t("ui3.menu.resetPanes") });
  return [
    { id: "file", title: t("ui3.menu.file"), items: [{ action: "addFile", title: t("ui3.menu.addFile"), disabled: inputs.value.picking }] },
    { id: "view", title: t("ui3.menu.view"), items: view },
    { id: "tools", title: t("ui3.menu.tools"), items: [
      { action: "setup", title: t("ui3.menu.setup") },
      { action: "settings", title: t("ui3.menu.settings") },
    ] },
  ];
});

function run(action: Action) {
  switch (action) {
    case "addFile":
      shell.navigate("transcribe");
      void core.inputs.pick(core.host);
      return;
    case "modeNormal":
      shell.setMode("normal");
      return;
    case "modeExpert":
      shell.setMode("expert");
      return;
    case "resetPanes":
      shell.navigate("transcribe");
      shell.resetPanes();
      return;
    case "setup":
      shell.navigate("setup");
      return;
    case "settings":
      shell.navigate("settings");
  }
}
</script>

<template>
  <div class="u-appmenu" data-testid="app-menu" :data-style="asBar ? 'bar' : 'button'">
    <template v-if="asBar">
      <v-menu v-for="g in groups" :key="g.id" location="bottom start">
        <template #activator="{ props: menu }">
          <v-btn v-bind="menu" variant="text" :data-testid="`menu-${g.id}`">{{ g.title }}</v-btn>
        </template>
        <v-list :aria-label="g.title" min-width="240">
          <v-list-item v-for="item in g.items" :key="item.action" :title="item.title" :disabled="item.disabled" :role="item.checked !== undefined ? 'menuitemradio' : undefined" :aria-checked="item.checked" :data-testid="`menu-item-${item.action}`" @click="run(item.action)">
            <template v-if="item.checked !== undefined" #prepend><v-icon :icon="mdiCheck" :style="{ visibility: item.checked ? 'visible' : 'hidden' }" aria-hidden="true" /></template>
          </v-list-item>
        </v-list>
      </v-menu>
    </template>
    <v-menu v-else location="bottom start">
      <template #activator="{ props: menu }">
        <v-btn v-bind="menu" variant="text" :icon="mdiMenu" density="comfortable" :aria-label="t('ui3.menu.label')" data-testid="menu-button" />
      </template>
      <v-list :aria-label="t('ui3.menu.label')" min-width="260" max-height="70vh">
        <template v-for="(g, gi) in groups" :key="g.id">
          <v-divider v-if="gi > 0" />
          <v-list-subheader>{{ g.title }}</v-list-subheader>
          <v-list-item v-for="item in g.items" :key="item.action" :title="item.title" :disabled="item.disabled" :role="item.checked !== undefined ? 'menuitemradio' : undefined" :aria-checked="item.checked" :data-testid="`menu-item-${item.action}`" @click="run(item.action)">
            <template v-if="item.checked !== undefined" #prepend><v-icon :icon="mdiCheck" :style="{ visibility: item.checked ? 'visible' : 'hidden' }" aria-hidden="true" /></template>
          </v-list-item>
        </template>
      </v-list>
    </v-menu>
  </div>
</template>

<style scoped>
.u-appmenu {
  display: flex;
  align-items: center;
  flex: none;
}
.u-appmenu :deep(.v-btn) {
  letter-spacing: normal;
}
</style>
