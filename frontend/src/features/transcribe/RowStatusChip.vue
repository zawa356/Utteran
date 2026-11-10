<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "../../composables/core";
import type { RowStatus } from "../../domain/inputCollection";
import type { MessageKey } from "../../i18n";

/** Admission state of one input (what the start request got back) — never "running" or "done". */
const props = defineProps<{ status: RowStatus; testid?: string }>();
const { t } = useI18n();
const color = computed(() => ({ not_submitted: undefined, queued: "info", pending: "info", accepted: "primary", rejected: "error", unknown: "warning" })[props.status]);
</script>

<template>
  <v-chip size="small" :color="color" variant="tonal" :data-testid="testid ?? 'tx-row-status'" :data-status="status">{{ t(`tx.row.status.${status}` as MessageKey) }}</v-chip>
</template>
