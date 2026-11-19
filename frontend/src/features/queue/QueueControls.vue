<script setup lang="ts">
import { computed } from "vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { MessageKey } from "../../i18n";
import type { QueueViewOptions } from "./queueRows";
const props = defineProps<{ rootId: string; jobId?: string; display: QueueViewOptions }>();
const v1 = useCore().v1!;
const board = v1.board;
const s = useStore(board.store);
const gate = useStore(v1.queueGate.store);
const { t } = useCodeText();
const holdReason = computed(() => { void [s.value, gate.value]; return board.holdReason(props.rootId, props.jobId); });
const held = computed(() => s.value.queueView?.items.find(i => i.resourceKind === "job" && i.resourceId === props.jobId)?.held === true);
const moves = computed(() => {
  void [s.value, gate.value];
  return ([-1, 1] as const).map(direction => ({ direction, reason: board.moveReason(props.rootId, props.jobId, direction, props.display) }));
});
const cancelReason = computed(() => {
  void [s.value, gate.value];
  if (board.actionOpen()) return "busy";
  if (s.value.phase !== "done") return "unread";
  const group = s.value.queue.find(g => g.rootId === props.rootId);
  if (!props.jobId) return "branch";
  if (!group || !board.canCancel(group, props.jobId)) return "waiting";
  return null;
});
</script>
<template>
  <div class="u-stack" data-testid="queue-controls" :data-job="jobId">
    <div>
      <v-btn variant="outlined" size="small" :disabled="holdReason !== null" data-testid="queue-hold" @click="board.setHeld(rootId, jobId!, !held)">{{ t(held ? 'queue.unhold' : 'queue.hold') }}</v-btn>
      <p class="text-caption">{{ t("queue.holdSemantics") }}</p>
      <p v-if="holdReason" class="text-caption" data-testid="queue-hold-reason">{{ t(`queue.disabled.${holdReason}` as MessageKey) }}</p>
    </div>
    <div v-for="move in moves" :key="move.direction">
      <v-btn variant="outlined" size="small" :disabled="move.reason !== null" :data-testid="move.direction === -1 ? 'queue-up' : 'queue-down'" @click="board.move(rootId, jobId!, move.direction, display)">{{ t(move.direction === -1 ? 'queue.up' : 'queue.down') }}</v-btn>
      <p v-if="move.reason" class="text-caption" :data-testid="move.direction === -1 ? 'queue-up-reason' : 'queue-down-reason'">{{ t(`queue.disabled.${move.reason}` as MessageKey) }}</p>
    </div>
    <div>
      <v-btn variant="outlined" color="error" size="small" :disabled="cancelReason !== null" data-testid="queue-cancel" @click="board.cancel(rootId, jobId)">{{ t("v1.board.cancel") }}</v-btn>
      <p class="text-caption">{{ t("queue.cancelSemantics") }}</p>
      <p v-if="cancelReason" class="text-caption">{{ t(`queue.disabled.${cancelReason}` as MessageKey) }}</p>
    </div>
  </div>
</template>
