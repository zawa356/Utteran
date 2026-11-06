<script setup lang="ts">
import { mdiFileDocumentOutline, mdiFolderOutline } from "@mdi/js";
import { usePreview } from "../composables";
import type { DemoJob } from "../domain/demo";
import { useJobText } from "./useJobText";

/**
 * The output folder and file names a job was given at submission (#33/#35). They never change
 * afterwards. Nothing exists on disk in the prototype.
 */
defineProps<{ job: DemoJob }>();
const { t } = usePreview();
const { dir } = useJobText();
</script>

<template>
  <dl class="p-dl text-body-medium" data-testid="job-outputs">
    <dt>{{ t("output.folder") }}</dt>
    <dd data-testid="job-output-dir"><v-icon :icon="mdiFolderOutline" size="small" aria-hidden="true" /> <span class="p-break">{{ dir(job.outputDir) }}</span></dd>
    <dt>{{ t("output.files") }}</dt>
    <dd>
      <ul class="p-outputs" :data-testid="`job-output-names-${job.id}`">
        <li v-for="name in job.outputs" :key="name"><v-icon :icon="mdiFileDocumentOutline" size="small" aria-hidden="true" /> <span class="p-ellipsis" :title="name">{{ name }}</span></li>
      </ul>
    </dd>
  </dl>
</template>
