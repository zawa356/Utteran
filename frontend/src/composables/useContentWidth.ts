import { onScopeDispose, ref, watch, type Ref } from "vue";

/** Measures the grid itself, after sidebar, scrollbars and surrounding padding. */
export function useContentWidth(element: Readonly<Ref<HTMLElement | null>>, enabled: boolean) {
  const width = ref(0);
  if (!enabled) return width;

  let frame = 0;
  const observer = new ResizeObserver(([entry]) => {
    if (!entry) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => { width.value = entry.contentRect.width; });
  });
  watch(element, (node, previous) => {
    if (previous) observer.unobserve(previous);
    cancelAnimationFrame(frame);
    if (node) observer.observe(node);
  }, { flush: "post" });
  onScopeDispose(() => {
    observer.disconnect();
    cancelAnimationFrame(frame);
  });
  return width;
}
