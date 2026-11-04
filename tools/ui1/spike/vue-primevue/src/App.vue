<script setup lang="ts">
import Button from "primevue/button";
import Select from "primevue/select";
import ToggleSwitch from "primevue/toggleswitch";
import Message from "primevue/message";
import Tag from "primevue/tag";
import { computed } from "vue";
import { LABELS, LANGUAGES, MODELS, SECTIONS, effectiveSettings, isOverridden } from "../../core/core";
import { useCore } from "./useCore";

const { state, controller: c } = useCore();
const selected = computed(() => state.value.files.find((f) => f.id === state.value.selectedFileId)!);
const effective = computed(() => effectiveSettings(state.value, selected.value.id));
const overridden = computed(() => isOverridden(state.value, selected.value.id, "language"));
const jobs = computed(() => Object.values(state.value.jobs));
function toggleOverride(on: boolean) {
  c.setOverride(selected.value.id, "language", on ? effective.value.language : undefined);
}
c.loadEnvironment("cpu");
</script>

<template>
  <div class="shell" data-testid="ready">
    <nav class="side" aria-label="Primary">
      <div class="row">
        <Button data-testid="mode-normal" label="Normal" size="small" :severity="state.mode === 'normal' ? undefined : 'secondary'" @click="c.setMode('normal')" />
        <Button data-testid="mode-expert" label="Expert" size="small" :severity="state.mode === 'expert' ? undefined : 'secondary'" @click="c.setMode('expert')" />
      </div>
      <Button v-for="s in SECTIONS" :key="s" :label="LABELS[s]" text :aria-current="s === 'workspace' ? 'page' : undefined" />
    </nav>
    <main class="work">
      <section v-if="state.mode === 'normal'" class="normal">
        <h1>Transcribe</h1>
        <p>Input: {{ selected.name }}</p>
        <p>Recommended: <b>{{ effective.model }}</b> / {{ effective.language }}</p>
        <details><summary>Details</summary><span data-testid="normal-route">{{ state.environment.data?.backend }} · {{ state.environment.data?.device }} · {{ state.environment.data?.reason }}</span></details>
        <Button data-testid="normal-start" label="Start" :loading="state.submit.phase === 'pending'" @click="c.submit()" />
      </section>
      <div v-else class="panes">
        <section class="pane" aria-label="Inputs">
          <h2>Inputs</h2>
          <div v-for="f in state.files" :key="f.id" class="file" role="option" :aria-selected="f.id === state.selectedFileId" :data-testid="`file-row-${f.id}`" tabindex="0" @click="c.selectFile(f.id)" @keydown.enter="c.selectFile(f.id)">
            <span>{{ f.name }}</span><Tag :data-testid="`effective-language-${f.id}`" :value="effectiveSettings(state, f.id).language" />
          </div>
        </section>
        <section class="pane" aria-label="Inspector">
          <h2>Shared defaults</h2>
          <div class="row"><label>Language</label><Select data-testid="default-language" :options="LANGUAGES" :model-value="state.defaults.language" @update:model-value="(v: string) => c.setDefault('language', v)" /></div>
          <div class="row"><label>Model</label><Select :options="MODELS" :model-value="state.defaults.model" @update:model-value="(v: string) => c.setDefault('model', v)" /></div>
          <h2>Selected: {{ selected.name }}</h2>
          <div class="row"><label for="ov">Override language</label><ToggleSwitch input-id="ov" data-testid="override-language" :model-value="overridden" @update:model-value="toggleOverride" /></div>
          <div class="row"><label>Language</label><Select data-testid="inspector-language" :options="LANGUAGES" :disabled="!overridden" :model-value="effective.language" @update:model-value="(v: string) => c.setOverride(selected.id, 'language', v)" /></div>
          <h2>Pipeline</h2>
          <p>Input → ASR ({{ effective.model }}) → Diarization ({{ effective.diarization ? "on" : "off" }}) → Output</p>
          <h2>Environment</h2>
          <div class="row">
            <Button data-testid="profile-cuda" label="cuda" size="small" severity="secondary" @click="c.loadEnvironment('cuda')" />
            <Button data-testid="profile-cpu" label="cpu" size="small" severity="secondary" @click="c.loadEnvironment('cpu')" />
            <span data-testid="profile-current">{{ state.environment.data?.profile }}</span>
            <span v-if="state.environment.phase === 'pending'" data-testid="profile-pending">loading…</span>
          </div>
        </section>
        <section class="pane queue" aria-label="Queue">
          <h2>Queue</h2>
          <div class="row">
            <Button data-testid="submit" label="Submit" :loading="state.submit.phase === 'pending'" :disabled="state.submit.phase === 'pending'" @click="c.submit()" />
            <span data-testid="submit-status">{{ state.submit.phase }}</span>
          </div>
          <Message v-if="state.submit.phase === 'error'" severity="error" data-testid="submit-error">
            {{ state.submit.error?.kind }}: {{ state.submit.error?.code }}
            <Button v-if="state.submit.error?.retryable" data-testid="retry" label="Retry" size="small" text @click="c.retry()" />
          </Message>
          <table><tbody><tr v-for="j in jobs" :key="j.id" :data-testid="`job-row-${j.id}`"><td>{{ j.id }}</td><td :data-testid="`job-status-${j.id}`">{{ j.status }}</td><td>r{{ j.revision }}</td></tr></tbody></table>
          <p data-testid="event-stats">applied={{ state.stats.applied }} ignored={{ state.stats.ignored }} resyncs={{ state.stats.resyncs }}</p>
        </section>
      </div>
    </main>
  </div>
</template>
