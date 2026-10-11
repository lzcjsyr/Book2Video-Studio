import { useCallback, useState } from "react";

// Remembers whether a panel is open on this computer. Storage can be unavailable
// (private windows, development browsers), so the panel still works without it.
export function useCollapsed(key: string, initial = false) {
  const storageKey = `studio-collapsed-${key}`;
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      return saved === null ? initial : saved === "1";
    } catch {
      return initial;
    }
  });
  const toggle = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {
        /* The choice still applies for this session. */
      }
      return next;
    });
  }, [storageKey]);
  return [collapsed, toggle] as const;
}
