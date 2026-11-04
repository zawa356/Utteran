<script setup lang="ts">
import { computed } from "vue";
import type { ApiError } from "../api/errors";
import { useI18n } from "../composables/core";
import { errorMessage } from "../i18n";

const props = defineProps<{ error: ApiError; title?: string; retryLabel?: string; testid?: string }>();
const emit = defineEmits<{ retry: [] }>();
const { language, t } = useI18n();
const message = computed(() => errorMessage(language.value, props.error));
const code = computed(() => props.error.code ?? (props.error.status !== null ? `HTTP ${props.error.status}` : props.error.kind));
</script>

<template>
  <v-alert type="error" :title="title" role="alert" :data-testid="testid ?? 'error-alert'">
    <div>{{ message }}</div>
    <div class="text-caption">{{ t("common.details", { code }) }}</div>
    <template v-if="error.retryable" #append>
      <v-btn variant="text" size="small" :data-testid="(testid ?? 'error-alert') + '-retry'" @click="emit('retry')">
        {{ retryLabel ?? t("common.retry") }}
      </v-btn>
    </template>
  </v-alert>
</template>
