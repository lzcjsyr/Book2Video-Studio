import { useLayoutEffect, useRef, useState } from "react";

/** Text broken into its lines and sentences, as the manuscript heading counts them. */
export function textCounts(text: string) {
  const lines = text.split("\n").filter((line) => line.trim()).length;
  // A sentence ends at 。！？!? (with any closing quotes or brackets) or at a line break.
  const sentences = text
    .replace(/([。！？!?]+[」』”’）)]*)/g, "$1\n")
    .split("\n")
    .filter((part) => part.trim()).length;
  return { lines, sentences };
}

/**
 * A textarea with a number beside each non-empty line, like a code editor's gutter. A copy of the
 * text laid out exactly like the textarea tells where each line starts, so wrapped lines keep one
 * number and the numbers follow scrolling and resizing.
 */
export default function NumberedText({
  value,
  onChange,
  className,
  ariaLabel,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  className: string;
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const [tops, setTops] = useState<number[]>([]);
  const [scroll, setScroll] = useState(0);
  const lines = value.split("\n");

  useLayoutEffect(() => {
    const text = area.current, copy = mirror.current;
    if (!text || !copy) return;
    const measure = () => {
      const style = getComputedStyle(text);
      for (const key of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft"] as const)
        copy.style[key] = style[key];
      // The textarea's own width without its border or scrollbar, where its text wraps.
      copy.style.width = `${text.clientWidth}px`;
      const border = parseFloat(style.borderTopWidth) || 0;
      setTops([...copy.children].map((line) => (line as HTMLElement).offsetTop + border));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(text);
    return () => observer.disconnect();
  }, [value]);

  let number = 0;
  return (
    <div className="numbered-text">
      <textarea
        ref={area}
        className={className}
        aria-label={ariaLabel}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onScroll={(e) => setScroll(e.currentTarget.scrollTop)}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="numbered-gutter" aria-hidden="true">
        <div style={{ transform: `translateY(${-scroll}px)` }}>
          {lines.map((line, i) =>
            line.trim() && tops[i] !== undefined ? (
              <span key={i} style={{ top: tops[i] }}>{++number}</span>
            ) : null,
          )}
        </div>
      </div>
      <div ref={mirror} className="numbered-mirror" aria-hidden="true">
        {lines.map((line, i) => <div key={i}>{line || "​"}</div>)}
      </div>
    </div>
  );
}
