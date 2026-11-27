/**
 * DX-01 Typed JSON Props Contract for Deterministic Remotion Effect Spans.
 * Pure data schema — zero executable expressions, zero remote URLs, zero niche hardcoding.
 */

export interface ArtPalette {
  bg_hex?: string;
  surface_hex?: string;
  accent_hex?: string;
  fg_hex?: string;
  muted_hex?: string;
  danger_hex?: string;
  positive_hex?: string;
  canvas_hex?: string;
  stroke_hex?: string;
}

export interface WordCue {
  text: string;
  start_sec: number;
  end_sec: number;
  emphasis?: boolean;
  serif_italic?: boolean;
}

export interface TallyItem {
  label: string;
  value_text: string;
  reveal_sec: number;
  accent?: boolean;
  strike_after_sec?: number;
}

export interface BarItem {
  label: string;
  value: number;
  display_text?: string;
  color_role?: "accent" | "positive" | "danger" | "muted" | "fg";
  reveal_sec?: number;
}

export interface PointNorm {
  x: number;
  y: number;
}

export interface Dx01EffectEvent {
  event_id: string;
  effect_id: string;
  start_sec: number;
  end_sec: number;
  enter_sec?: number;
  exit_sec?: number;
  z_order?: number;
  seed?: number;
  x_norm?: number;
  y_norm?: number;
  w_norm?: number;
  h_norm?: number;
  align?: "left" | "center" | "right";
  opacity?: number;
  palette?: ArtPalette;
  // Typography
  title?: string;
  emphasis_words?: string[];
  subtitle?: string;
  kicker?: string;
  lines?: string[];
  word_cues?: WordCue[];
  font_size_scale?: number;
  font_style?: "mixed_editorial" | "clean_sans" | "serif_editorial";
  text_shadow?: boolean;
  underline_accent?: boolean;
  // Geometry / Path
  path_kind?: "underline" | "bracket" | "curve_arrow" | "polyline" | "circle_ring" | "rounded_focus" | "morph_badge";
  from_point?: PointNorm;
  to_point?: PointNorm;
  control_point?: PointNorm;
  points?: PointNorm[];
  stroke_width?: number;
  label?: string;
  morph_from?: "circle" | "pill" | "diamond" | "chevron";
  morph_to?: "circle" | "pill" | "diamond" | "chevron";
  // Numerical / Data
  from_value?: number;
  to_value?: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  count_sec?: number;
  qualifier?: string;
  items?: TallyItem[];
  bars?: BarItem[];
  max_value?: number;
  // Evidence
  focus_box_norm?: [number, number, number, number];
  vignette_opacity?: number;
  divider_pause_at?: number;
  before_label?: string;
  after_label?: string;
  // Atmosphere
  angle_deg?: number;
  intensity?: number;
  particle_count?: number;
  grain_opacity?: number;
}

export interface Dx01SpanCompositionProps extends Record<string, unknown> {
  span_id: string;
  width: number;
  height: number;
  fps: number;
  duration_in_frames: number;
  span_start_sec: number;
  transparent: boolean;
  background_hex?: string;
  palette: ArtPalette;
  events: Dx01EffectEvent[];
  seed?: number;
}

export const DEFAULT_PALETTE: Required<ArtPalette> = {
  bg_hex: "#14181C",
  surface_hex: "#1F262D",
  accent_hex: "#E5B95C",
  fg_hex: "#FAF8F5",
  muted_hex: "#CBD2D9",
  danger_hex: "#D95D5D",
  positive_hex: "#52B788",
  canvas_hex: "#0D1014",
  stroke_hex: "#101214",
};

export const LOCAL_SANS_STACK =
  '"Segoe UI", "Helvetica Neue", Arial, "Liberation Sans", sans-serif';
export const LOCAL_SERIF_STACK =
  'Georgia, "Times New Roman", "Liberation Serif", serif';
