<script setup lang="ts">
import { computed } from "vue";
import { LABELS, LANGUAGES, MODELS, SECTIONS, effectiveSettings, isOverridden } from "../../core/core";
import { useCore } from "./useCore";

const { state, controller: c } = useCore();
const selected = computed(() => state.value.files.find((f) => f.id === state.value.selectedFileId)!);
const effective = computed(() => effectiveSettings(state.value, selected.value.id));
const overridden = computed(() => isOverridden(state.value, selected.value.id, "language"));
const jobs = computed(() => Object.values(state.value.jobs));
function toggleOverride(on: boolean | null) {
  c.setOverride(selected.value.id, "language", on ? effective.value.language : undefined);
}
c.loadEnvironment("cpu");
</script>

<template>
  <v-app>
    <div class="shell" data-testid="ready">
      <nav class="side" aria-label="Primary">
        <v-btn-toggle :model-value="state.mode" density="compact" mandatory @update:model-value="c.setMode">
          <v-btn data-testid="mode-normal" value="normal" size="small">Normal</v-btn>
          <v-btn data-testid="mode-expert" value="expert" size="small">Expert</v-btn>
        </v-btn-toggle>
        <v-list density="compact" nav>
          <v-list-item v-for="s in SECTIONS" :key="s" :title="LABELS[s]" :active="s === 'workspace'" />
        </v-list>
      </nav>
      <main class="work">
        <section v-if="state.mode === 'normal'" class="normal">
          <h1>Transcribe</h1>
          <p>Input: {{ selected.name }}</p>
          <p>Recommended: <b>{{ effective.model }}</b> / {{ effective.language }}</p>
          <details><summary>Details</summary><span data-testid="normal-route">{{ state.environment.data?.backend }} · {{ state.environment.data?.device }} · {{ state.environment.data?.reason }}</span></details>
          <v-btn data-testid="normal-start" color="primary" :loading="state.submit.phase === 'pending'" @click="c.submit()">Start</v-btn>
        </section>
        <div v-else class="panes">
          <section class="pane" aria-label="Inputs">
            <h2>Inputs</h2>
            <div v-for="f in state.files" :key="f.id" class="file" role="option" :aria-selected="f.id === state.selectedFileId" :data-testid="`file-row-${f.id}`" tabindex="0" @click="c.selectFile(f.id)" @keydown.enter="c.selectFile(f.id)">
              <span>{{ f.name }}</span><v-chip size="small" :data-testid="`effective-language-${f.id}`">{{ effectiveSettings(state, f.id).language }}</v-chip>
            </div>
          </section>
          <section class="pane" aria-label="Inspector">
            <h2>Shared defaults</h2>
            <v-select data-testid="default-language" label="Language" density="compact" :items="LANGUAGES" :model-value="state.defaults.language" @update:model-value="(v: string) => c.setDefault('language', v)" />
            <v-select label="Model" density="compact" :items="MODELS" :model-value="state.defaults.model" @update:model-value="(v: string) => c.setDefault('model', v)" />
            <h2>Selected: {{ selected.name }}</h2>
            <v-switch data-testid="override-language" label="Override language" density="compact" hide-details :model-value="overridden" @update:model-value="toggleOverride" />
            <v-select data-testid="inspector-language" label="Language" density="compact" :items="LANGUAGES" :disabled="!overridden" :model-value="effective.language" @update:model-value="(v: string) => c.setOverride(selected.id, 'language', v)" />
            <h2>Pipeline</h2>
            <p>Input → ASR ({{ effective.model }}) → Diarization ({{ effective.diarization ? "on" : "off" }}) → Output</p>
            <h2>Environment</h2>
            <div class="row">
              <v-btn data-testid="profile-cuda" size="small" variant="tonal" @click="c.loadEnvironment('cuda')">cuda</v-btn>
              <v-btn data-testid="profile-cpu" size="small" variant="tonal" @click="c.loadEnvironment('cpu')">cpu</v-btn>
              <span data-testid="profile-current">{{ state.environment.data?.profile }}</span>
              <span v-if="state.environment.phase === 'pending'" data-testid="profile-pending">loading…</span>
            </div>
          </section>
          <section class="pane queue" aria-label="Queue">
            <h2>Queue</h2>
            <div class="row">
              <v-btn data-testid="submit" color="primary" :loading="state.submit.phase === 'pending'" :disabled="state.submit.phase === 'pending'" @click="c.submit()">Submit</v-btn>
              <span data-testid="submit-status">{{ state.submit.phase }}</span>
            </div>
            <v-alert v-if="state.submit.phase === 'error'" type="error" density="compact" data-testid="submit-error">
              {{ state.submit.error?.kind }}: {{ state.submit.error?.code }}
              <v-btn v-if="state.submit.error?.retryable" data-testid="retry" size="small" variant="text" @click="c.retry()">Retry</v-btn>
            </v-alert>
            <v-table density="compact"><tbody><tr v-for="j in jobs" :key="j.id" :data-testid="`job-row-${j.id}`"><td>{{ j.id }}</td><td :data-testid="`job-status-${j.id}`">{{ j.status }}</td><td>r{{ j.revision }}</td></tr></tbody></v-table>
            <p data-testid="event-stats">applied={{ state.stats.applied }} ignored={{ state.stats.ignored }} resyncs={{ state.stats.resyncs }}</p>
          </section>
        </div>
      </main>
    </div>
  </v-app>
</template>
