<script setup lang="ts">
import { computed } from "vue";
import type { ApiError } from "../api/errors";
import { useI18n } from "../composables/core";
import { errorMessage } from "../i18n";

const props = defineProps<{ error: ApiError; title?: string; retryLabel?: string; onRetry?: () => unknown; testid?: string }>();

const { language, t } = useI18n();
const message = computed(() => errorMessage(language.value, props.error));
const code = computed(() => props.error.code ?? (props.error.status !== null ? `HTTP ${props.error.status}` : props.error.kind));
</script>

<template>
  <v-alert type="error" :title="title" role="alert" :data-testid="testid ?? 'error-alert'">
    <div>{{ message }}</div>
    <div class="text-caption">{{ t("common.details", { code }) }}</div>
    <div v-if="error.retryable && onRetry" class="mt-2">
      <v-btn variant="text" size="small" :data-testid="(testid ?? 'error-alert') + '-retry'" @click="onRetry()">
        {{ retryLabel ?? t("common.retry") }}
      </v-btn>
    </div>
  </v-alert>
</template>
