<script setup lang="ts">
import { mdiCheck, mdiMenu, mdiOpenInNew } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import { usePreview } from "../composables";
import { type AppDialog, MENU_BAR_MIN_WIDTH } from "../domain/layout";
import { ABOUT } from "../domain/about";
import type { PreviewKey } from "../i18n";

/**
 * Application menu shared by Normal and Expert (iteration 3, owner memo #12). Two presentations are
 * compared in the review strip: a Windows-like menu bar (ファイル / 表示 / ツール / ヘルプ) and one
 * menu button. Narrow windows always use the button so the app bar never gets crowded.
 * Every entry is a mock: logs, diagnostics and "about" open sample dialogs; nothing is read, saved
 * or sent. The only real link is the verified official repository.
 */
const { demo, layout, layoutState, t } = usePreview();
const display = useDisplay();


type Action = "open" | "openFolder" | "modeNormal" | "modeExpert" | "resetPanes" | AppDialog | "repo";
interface Item {
  action: Action;
  title: string;
  checked?: boolean;
  href?: string;
}
interface Group {
  id: "file" | "view" | "tools" | "help";
  title: string;
  items: Item[];
}

const expert = computed(() => layoutState.value.mode === "expert");
const asBar = computed(() => layoutState.value.menuStyle === "bar" && display.width.value >= MENU_BAR_MIN_WIDTH);
const groups = computed<Group[]>(() => {
  const k = (key: PreviewKey) => t(key);
  const file: Item[] = expert.value
    ? [
        { action: "open", title: k("menu.file.addFile") },
        { action: "openFolder", title: k("menu.file.addFolder") },
      ]
    : [{ action: "open", title: k("menu.file.choose") }];
  const view: Item[] = [
    { action: "modeNormal", title: k("menu.view.normal"), checked: !expert.value },
    { action: "modeExpert", title: k("menu.view.expert"), checked: expert.value },
  ];
  if (expert.value) view.push({ action: "resetPanes", title: k("menu.view.resetPanes") });
  return [
    { id: "file", title: k("menu.file"), items: file },
    { id: "view", title: k("menu.view"), items: view },
    {
      id: "tools",
      title: k("menu.tools"),
      items: [
        { action: "systemLog", title: k("menu.tools.systemLog") },
        { action: "jobLog", title: k("menu.tools.jobLog") },
        { action: "diagnostics", title: k("menu.tools.diagnostics") },
      ],
    },
    {
      id: "help",
      title: k("menu.help"),
      items: [
        { action: "about", title: k("menu.help.about") },
        { action: "repo", title: k("menu.help.repo"), href: ABOUT.repository },
      ],
    },
  ];
});

function run(action: Action) {
  switch (action) {
    case "open":
      layout.navigate("transcribe");
      if (expert.value) demo.addFiles(1);
      else demo.chooseNormalFile();
      return;
    case "openFolder":
      layout.navigate("transcribe");
      demo.addFiles(3);
      return;
    case "modeNormal":
      layout.setMode("normal");
      return;
    case "modeExpert":
      layout.setMode("expert");
      return;
    case "resetPanes":
      layout.navigate("transcribe");
      layout.resetPanes();
      return;
    case "repo":
      return; // a plain link (opens in the browser); no handler
    default:
      layout.openDialog(action);
  }
}
</script>

<template>
  <div class="p-appmenu" data-testid="app-menu" :data-style="asBar ? 'bar' : 'button'">
    <template v-if="asBar">
      <v-menu v-for="g in groups" :key="g.id" location="bottom start">
        <template #activator="{ props: menu }">
          <v-btn v-bind="menu" variant="text" :data-testid="`menu-${g.id}`">{{ g.title }}</v-btn>
        </template>
        <v-list :aria-label="g.title" min-width="240">
          <v-list-item
            v-for="item in g.items"
            :key="item.action"
            :title="item.title"
            :href="item.href"
            :target="item.href ? '_blank' : undefined"
            :rel="item.href ? 'noopener noreferrer' : undefined"
            :data-testid="`menu-item-${item.action}`"
            @click="run(item.action)"
          >
            <template v-if="item.checked !== undefined" #prepend><v-icon :icon="mdiCheck" :style="{ visibility: item.checked ? 'visible' : 'hidden' }" aria-hidden="true" /></template>
            <template v-if="item.href" #append><v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></template>
          </v-list-item>
        </v-list>
      </v-menu>
    </template>
    <v-menu v-else location="bottom start">
      <template #activator="{ props: menu }">
        <v-btn v-bind="menu" variant="text" :prepend-icon="mdiMenu" data-testid="menu-button">{{ t("menu.label") }}</v-btn>
      </template>
      <v-list :aria-label="t('menu.label')" min-width="260" max-height="70vh">
        <template v-for="(g, gi) in groups" :key="g.id">
          <v-divider v-if="gi > 0" />
          <v-list-subheader>{{ g.title }}</v-list-subheader>
          <v-list-item
            v-for="item in g.items"
            :key="item.action"
            :title="item.title"
            :href="item.href"
            :target="item.href ? '_blank' : undefined"
            :rel="item.href ? 'noopener noreferrer' : undefined"
            :data-testid="`menu-item-${item.action}`"
            @click="run(item.action)"
          >
            <template v-if="item.checked !== undefined" #prepend><v-icon :icon="mdiCheck" :style="{ visibility: item.checked ? 'visible' : 'hidden' }" aria-hidden="true" /></template>
            <template v-if="item.href" #append><v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></template>
          </v-list-item>
        </template>
      </v-list>
    </v-menu>
  </div>
</template>
