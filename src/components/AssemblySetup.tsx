import { FolderOpen, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { Settings } from "../types";

type Values = Settings[string];
type Patch = Record<string, unknown>;

const exportSizes = ["1280x720", "1920x1080", "2560x1440", "3840x2160"];
const transitions = ["crossfade", "fade_black", "fade_white", "slide_left", "slide_right", "wipe_left", "wipe_right", "zoom_in", "zoom_out"];
const transitionNames: Record<string, string> = {
  crossfade: "Crossfade", fade_black: "Fade through black", fade_white: "Fade through white",
  slide_left: "Slide left", slide_right: "Slide right", wipe_left: "Wipe left", wipe_right: "Wipe right",
  zoom_in: "Zoom in", zoom_out: "Zoom out",
};
const themes = [
  { id: "ink", label: "Ink", hint: "Dark panels" },
  { id: "paper", label: "Paper", hint: "Light panels" },
  { id: "blur", label: "Blurred video", hint: "The picture, softened" },
];
const topContents = [
  { id: "title_subtitle", label: "Title & subtitle" },
  { id: "title", label: "Title" },
  { id: "none", label: "Empty" },
];
const bottomContents = [
  { id: "quote", label: "Quote" },
  { id: "ornament", label: "Ornament" },
  { id: "text", label: "Text" },
  { id: "none", label: "Empty" },
];
const accents = ["#C45A47", "#C9A24A", "#3F8F86", "#4A72B0", "#8A4F7D", "#E9E4DA"];
const number = (value: unknown, fallback: number) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

/** A slider that saves when it is let go, not on every step of the drag. */
function Range({ label, value, min, max, step, format, disabled, onCommit, hint }: {
  label: string; value: number; min: number; max: number; step: number;
  format: (value: number) => string; disabled: boolean; onCommit: (value: number) => void; hint?: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => { if (draft !== value) onCommit(draft); };
  return <label className="field range-field">
    <span>{label}<output>{format(draft)}</output></span>
    <input type="range" min={min} max={max} step={step} value={draft} disabled={disabled}
      onChange={(e) => setDraft(Number(e.target.value))}
      onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
    {hint && <small className="field-hint">{hint}</small>}
  </label>;
}

/** A text box that saves when it loses focus (or on Enter for a single line). */
function TextSetting({ label, value, placeholder, disabled, multiline, onCommit }: {
  label: string; value: string; placeholder: string; disabled: boolean; multiline?: boolean; onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => { if (draft.trim() !== value.trim()) onCommit(draft.trim()); };
  return <label className="field">
    <span>{label}</span>
    {multiline
      ? <textarea rows={3} value={draft} placeholder={placeholder} disabled={disabled}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
      : <input value={draft} placeholder={placeholder} disabled={disabled}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />}
  </label>;
}

function Segmented({ label, name, options, value, disabled, onChange }: {
  label: string; name: string; options: { id: string; label: string }[]; value: string; disabled: boolean; onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  return <fieldset className="segmented-field" disabled={disabled}>
    <legend>{label}</legend>
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => <label key={o.id} className={o.id === value ? "selected" : ""}>
        <input type="radio" name={name} value={o.id} checked={o.id === value} onChange={() => onChange(o.id)} />
        {t(o.label)}
      </label>)}
    </div>
  </fieldset>;
}

/** Assembly's choices — music, the video, and the 3:4 copy's panels — saved directly from its page. */
export default function AssemblySetup({ step5, panels, music, userMusic, copy, disabled, musicUrl, onChange, onAddMusic }: {
  step5: Values;
  panels: Values;
  music: string[];
  userMusic: string[];
  /** What the panels show when their text is left empty. */
  copy: { title: string; subtitle: string; quote: string };
  disabled: boolean;
  musicUrl: (name: string) => string;
  onChange: (section: "step5" | "three_by_four", patch: Patch) => Promise<boolean>;
  onAddMusic: () => void;
}) {
  const { t } = useI18n();
  const set5 = (patch: Patch) => onChange("step5", patch);
  const setPanel = (patch: Patch) => onChange("three_by_four", patch);

  const track = String(step5.bgm_filename ?? "");
  const included = music.filter((name) => !userMusic.includes(name));
  const [playing, setPlaying] = useState(false);
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    // A new track (or leaving the page) stops the preview.
    player.current?.pause();
    setPlaying(false);
  }, [track]);
  function togglePreview() {
    const audio = player.current;
    if (!audio || !track) return;
    if (playing) { audio.pause(); return; }
    audio.src = musicUrl(track);
    audio.play().catch(() => setPlaying(false));
  }

  const size = String(step5.video_size ?? "1280x720").toLowerCase().replace("×", "x");
  const transition = step5.enable_transitions ? String(step5.transition_style ?? "") : "";
  const zoom = number(step5.image_zoom_in_ratio, 1);

  const enabled = panels.enabled !== false;
  const theme = String(panels.theme ?? "ink");
  const top = String(panels.top_content ?? "title_subtitle");
  const bottom = String(panels.bottom_content ?? "quote");
  const share = number(panels.top_share, 0.5);
  const accent = String(panels.accent_color ?? "#C45A47");
  // The colour picker reports every step of a drag; only the colour it rests on is saved.
  const [accentDraft, setAccentDraft] = useState(accent);
  useEffect(() => setAccentDraft(accent), [accent]);
  useEffect(() => {
    if (accentDraft.toLowerCase() === accent.toLowerCase()) return;
    const timer = setTimeout(() => setPanel({ accent_color: accentDraft }), 450);
    return () => clearTimeout(timer);
  }, [accentDraft]);
  const panelLocked = disabled || !enabled;

  return <section className="assembly-setup" aria-label={t("Assembly setup")}>
    <div className="cover-setup-heading">
      <h2>{t("Assembly setup")}</h2>
      <p className="small-muted">{t("Selections save automatically. Music and video changes apply when you Generate; 3:4 panel changes only need Build 3:4 copy.")}</p>
    </div>

    <div className="setup-group" role="group" aria-labelledby="assembly-music">
      <h3 id="assembly-music">{t("Background music")}</h3>
      <label className="field">
        <span>{t("Track")}</span>
        <div className="input-action">
          <select disabled={disabled} value={track} onChange={(e) => set5({ bgm_filename: e.target.value || null })}>
            <option value="">{t("No music")}</option>
            {track && !music.includes(track) && <option value={track}>{t("Unavailable: {id}", { id: track })}</option>}
            <optgroup label={t("Included")}>
              {included.map((name) => <option key={name} value={name}>{name.replace(/\.[^.]+$/, "")}</option>)}
            </optgroup>
            {userMusic.length > 0 && <optgroup label={t("Added by you")}>
              {userMusic.map((name) => <option key={name} value={name}>{name.replace(/\.[^.]+$/, "")}</option>)}
            </optgroup>}
          </select>
          <button type="button" className="icon-button" disabled={!track || !music.includes(track)}
            aria-label={t(playing ? "Stop preview" : "Preview track")} title={t(playing ? "Stop preview" : "Preview track")}
            onClick={togglePreview}>
            {playing ? <Pause size={15} /> : <Play size={15} />}
          </button>
        </div>
        <audio ref={player} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
      </label>
      <button type="button" className="button small" disabled={disabled} onClick={onAddMusic}>
        <FolderOpen size={13} /> {t("Add music…")}
      </button>
      <Range label={t("Music volume")} value={number(step5.bgm_default_volume, 0.1)} min={0} max={0.6} step={0.01}
        format={(v) => `${Math.round(v * 100)}%`} disabled={disabled || !track}
        onCommit={(v) => set5({ bgm_default_volume: v })} />
      <label className="checkbox">
        <input type="checkbox" disabled={disabled || !track} checked={Boolean(step5.audio_ducking_enabled)}
          onChange={(e) => set5({ audio_ducking_enabled: e.target.checked })} />
        {t("Lower the music under narration")}
      </label>
      {Boolean(step5.audio_ducking_enabled) && track &&
        <Range label={t("Lowered by")} value={number(step5.audio_ducking_strength, 0.5)} min={0.1} max={0.9} step={0.05}
          format={(v) => `${Math.round(v * 100)}%`} disabled={disabled}
          onCommit={(v) => set5({ audio_ducking_strength: v })} />}
      <label className="checkbox">
        <input type="checkbox" disabled={disabled || !track} checked={Boolean(step5.bgm_normalize_loudness)}
          onChange={(e) => set5({ bgm_normalize_loudness: e.target.checked })} />
        {t("Even out the music's loudness")}
      </label>
    </div>

    <div className="setup-group" role="group" aria-labelledby="assembly-video">
      <h3 id="assembly-video">{t("Video")}</h3>
      <label className="field">
        <span>{t("Export size (16:9)")}</span>
        <select disabled={disabled} value={size} onChange={(e) => set5({ video_size: e.target.value })}>
          {!exportSizes.includes(size) && <option value={size}>{size.replace("x", " × ")}</option>}
          {exportSizes.map((s) => <option key={s} value={s}>{s.replace("x", " × ")}</option>)}
        </select>
      </label>
      <label className="checkbox">
        <input type="checkbox" disabled={disabled} checked={Boolean(step5.enable_subtitles)}
          onChange={(e) => set5({ enable_subtitles: e.target.checked })} />
        {t("Burn subtitles into the video")}
      </label>
      <Range label={t("Narration speed")} value={number(step5.narration_speed_factor, 1)} min={0.8} max={1.5} step={0.05}
        format={(v) => `${v.toFixed(2)}×`} disabled={disabled}
        onCommit={(v) => set5({ narration_speed_factor: v })} />
      <Range label={t("Slow zoom on still images")} value={zoom} min={1} max={1.2} step={0.01}
        format={(v) => (v <= 1 ? t("Off") : `${v.toFixed(2)}×`)} disabled={disabled}
        onCommit={(v) => set5({ image_zoom_in_ratio: v })} />
      <label className="field">
        <span>{t("Transitions")}</span>
        <select disabled={disabled} value={transition}
          onChange={(e) => set5(e.target.value ? { enable_transitions: true, transition_style: e.target.value } : { enable_transitions: false })}>
          <option value="">{t("Cut (no transition)")}</option>
          {transitions.map((id) => <option key={id} value={id}>{t(transitionNames[id])}</option>)}
        </select>
      </label>
    </div>

    <div className={"setup-group" + (enabled ? "" : " inactive")} role="group" aria-labelledby="assembly-panels">
      <h3 id="assembly-panels">{t("3:4 panels")}</h3>
      {!enabled && <p className="small-muted">{t("Turn on the 3:4 copy at the top of the page to design its panels.")}</p>}
      <fieldset className="theme-choices" disabled={panelLocked}>
        <legend>{t("Style")}</legend>
        {themes.map((choice) => <label key={choice.id} className={`theme-choice${choice.id === theme ? " selected" : ""}`}>
          <input type="radio" name="panel-theme" value={choice.id} checked={choice.id === theme} onChange={() => setPanel({ theme: choice.id })} />
          <span className={`theme-swatch ${choice.id}`} aria-hidden="true"><i /></span>
          <strong>{t(choice.label)}</strong>
          <small>{t(choice.hint)}</small>
        </label>)}
      </fieldset>
      <Range label={t("Video position")} value={share} min={0.15} max={0.85} step={0.05}
        format={(v) => (Math.abs(v - 0.5) < 0.001 ? t("Centred") : v < 0.5 ? t("Higher") : t("Lower"))}
        disabled={panelLocked} onCommit={(v) => setPanel({ top_share: Number(v.toFixed(2)) })}
        hint={t("Move the video up to give the bottom panel more room, or down for a larger title.")} />
      <Segmented label={t("Top panel")} name="panel-top" options={topContents} value={top} disabled={panelLocked}
        onChange={(id) => setPanel({ top_content: id })} />
      {top !== "none" && <TextSetting label={t("Title")} value={String(panels.title_text ?? "")} disabled={panelLocked}
        placeholder={copy.title || t("The chosen cover title")} onCommit={(v) => setPanel({ title_text: v })} />}
      {top === "title_subtitle" && <TextSetting label={t("Subtitle")} value={String(panels.subtitle_text ?? "")} disabled={panelLocked}
        placeholder={copy.subtitle || t("The chosen cover subtitle")} onCommit={(v) => setPanel({ subtitle_text: v })} />}
      {top !== "none" && <p className="field-hint panel-hint">{t("Leave empty to use the cover text chosen in Writing.")}</p>}
      <Segmented label={t("Bottom panel")} name="panel-bottom" options={bottomContents} value={bottom} disabled={panelLocked}
        onChange={(id) => setPanel({ bottom_content: id })} />
      {bottom === "quote" && <p className="field-hint panel-hint">
        {copy.quote ? t("Shows the opening quote: {quote}", { quote: copy.quote }) : t("No opening quote yet, so the ornament shows. Choose a quote in Writing.")}
      </p>}
      {bottom === "text" && <TextSetting label={t("Bottom text")} value={String(panels.bottom_text ?? "")} disabled={panelLocked} multiline
        placeholder={t("For example: Follow for more books")} onCommit={(v) => setPanel({ bottom_text: v })} />}
      <fieldset className="accent-field" disabled={panelLocked}>
        <legend>{t("Accent colour")}</legend>
        <div className="accent-choices">
          {accents.map((color) => <button key={color} type="button" className={`accent-swatch${color.toLowerCase() === accentDraft.toLowerCase() ? " selected" : ""}`}
            style={{ background: color }} aria-label={color} title={color} onClick={() => setAccentDraft(color)} />)}
          <label className="accent-custom" title={t("Custom colour")}>
            <input type="color" value={/^#[0-9a-f]{6}$/i.test(accentDraft) ? accentDraft : "#c45a47"}
              aria-label={t("Custom colour")} onChange={(e) => setAccentDraft(e.target.value)} />
          </label>
        </div>
      </fieldset>
    </div>
  </section>;
}
