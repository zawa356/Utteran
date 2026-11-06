import { usePreview } from "../composables";
import type { WizardChoices, WizardEntry } from "../domain/setup";

/**
 * Opening the wizard from anywhere: it is shown on the Setup page (or as the whole window while
 * nothing is configured), so "add" and "repair" also move to that page. "Re-detect" is a different
 * action (it only refreshes the environment view) and never opens the wizard.
 */
export function useSetupNav() {
  const { layout, setup, setupState } = usePreview();
  function openSetup(entry: WizardEntry, preset: Partial<WizardChoices> = {}) {
    const resolved: WizardEntry = setupState.value.configured ? entry : "first_run";
    setup.open(resolved, preset);
    layout.navigate("setup");
  }
  return { openSetup };
}
