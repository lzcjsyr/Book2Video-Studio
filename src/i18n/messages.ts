import { zh } from "../locales/zh-CN.ts";
export type Locale = "en" | "zh-CN";
export type Values = Record<string, string | number>;

const patterns = Object.keys(zh)
  .filter((key) => /\{\w+\}/.test(key))
  .map((key) => {
    const names: string[] = [];
    const pattern = key
      .split(/(\{\w+\})/)
      .map((part) => {
        if (/^\{\w+\}$/.test(part)) {
          names.push(part.slice(1, -1));
          const name = part.slice(1, -1);
          if (["count", "index"].includes(name)) return "(\\d+)";
          if (["step", "seconds"].includes(name)) return "(\\d+(?:\\.\\d+)?)";
          if (name === "duration") return "([\\d:]+)";
          return "(.+?)";
        }
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      })
      .join("");
    return { key, names, regex: new RegExp(`^${pattern}$`) };
  });

export function translate(
  locale: Locale,
  key: string,
  values: Values = {},
): string {
  let template = locale === "zh-CN" ? zh[key] || key : key;
  // Local service notices and revision labels remain language-independent on disk.
  if (locale === "zh-CN" && !zh[key] && !Object.keys(values).length) {
    const prefix = key.match(
      /^(Error|ValueError|RuntimeError|Conflict|TypeError):\s*(.+)$/s,
    );
    if (prefix) return translate(locale, prefix[2]);
    for (const { key: patternKey, names, regex } of patterns) {
      const match = key.match(regex);
      if (match)
        return translate(
          locale,
          patternKey,
          Object.fromEntries(names.map((name, i) => [name, match[i + 1]])),
        );
    }
  }
  return template.replace(/\{(\w+)\}/g, (original, name) => {
    const value = values[name];
    return value === undefined
      ? original
      : typeof value === "string"
        ? translate(locale, value)
        : String(value);
  });
}

export function initialLocale(saved: string | null, preferred: string): Locale {
  if (saved === "en" || saved === "zh-CN") return saved;
  return /^zh(?:-|$)/i.test(preferred) ? "zh-CN" : "en";
}
