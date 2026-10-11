import { useCallback, useSyncExternalStore } from "react";
import {
  initialLocale,
  translate,
  type Locale,
  type Values,
} from "./messages.ts";

const storageKey = "studio-language";
const listeners = new Set<() => void>();
let saved: string | null = null;
try {
  saved = localStorage.getItem(storageKey);
} catch {
  /* Storage can be unavailable in a development browser. */
}
let locale: Locale = initialLocale(
  saved,
  typeof navigator === "undefined" ? "en" : navigator.language,
);
if (typeof document !== "undefined") document.documentElement.lang = locale;

export function setLocale(next: Locale) {
  if (next !== "en" && next !== "zh-CN") return;
  locale = next;
  try {
    localStorage.setItem(storageKey, next);
  } catch {
    /* Switching still works for the current session. */
  }
  if (typeof document !== "undefined") document.documentElement.lang = next;
  for (const listener of listeners) listener();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function getLocale() {
  return locale;
}
export function useI18n() {
  const language = useSyncExternalStore(subscribe, getLocale);
  const t = useCallback(
    (key: string, values?: Values) => translate(language, key, values),
    [language],
  );
  return { t, locale: language, setLocale };
}
