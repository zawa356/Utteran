import { mdiCogOutline, mdiFormatListBulleted, mdiHistory, mdiMicrophoneOutline, mdiPackageVariantClosed, mdiWrenchOutline } from "@mdi/js";
import type { SectionId } from "../../domain/shell";

/** One icon per page, shared by the Normal app-bar navigation and the Expert sidebar. */
export const SECTION_ICONS: Record<SectionId, string> = {
  transcribe: mdiMicrophoneOutline,
  queue: mdiFormatListBulleted,
  results: mdiHistory,
  models: mdiPackageVariantClosed,
  setup: mdiWrenchOutline,
  settings: mdiCogOutline,
};
