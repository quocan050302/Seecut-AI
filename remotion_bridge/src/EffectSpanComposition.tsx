import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import {
  Dx01EffectEvent,
  Dx01SpanCompositionProps,
  LOCAL_SANS_STACK,
} from "./types";
import {
  BitsSeededParticlesAndGrain,
  BitsStaggeredText,
  resolveArtPalette,
} from "./effects/CuratedBitsPrimitives";
import {
  RemocnAnimatedFlowBar,
  RemocnBeforeAfterDivider,
  RemocnKineticCounter,
  RemocnLightSweep,
  RemocnMaskedRevealText,
} from "./effects/CuratedRemocnPrimitives";
import {
  Dx01CalloutArrow,
  Dx01FocusRingAndSpotlight,
  Dx01KaraokeCue,
  Dx01SequentialTally,
  Dx01ShapeMorph,
  Dx01SvgPathDraw,
} from "./effects/VectorAndTypographyEffects";

const renderSingleEffect = (
  ev: Dx01EffectEvent,
  absTimeSec: number,
  width: number,
  height: number,
  fps: number,
  globalPalette: ReturnType<typeof resolveArtPalette>,
): React.ReactNode => {
  if (absTimeSec < ev.start_sec - 1e-4 || absTimeSec >= ev.end_sec - 1e-4) {
    return null;
  }
  const pal = resolveArtPalette({ ...globalPalette, ...(ev.palette || {}) });
  const localFrame = Math.max(0, Math.round((absTimeSec - ev.start_sec) * fps));
  const durationFrames = Math.max(2, Math.round((ev.end_sec - ev.start_sec) * fps));
  const enterFrames = Math.max(2, Math.round((ev.enter_sec ?? 0.28) * fps));
  const exitFrames = Math.max(2, Math.round((ev.exit_sec ?? 0.22) * fps));
  const sc = width / 1080;
  const fontScale = ev.font_size_scale ?? 1.0;

  const boxLeft = Math.round((ev.x_norm ?? 0.06) * width);
  const boxTop = Math.round((ev.y_norm ?? 0.08) * height);
  const boxW = Math.round((ev.w_norm ?? 0.88) * width);
  const evOpacity = ev.opacity ?? 1.0;

  switch (ev.effect_id) {
    case "dx01.typo.kinetic_headline":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 30,
          }}
        >
          <BitsStaggeredText
            text={ev.title || ""}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            emphasisWords={ev.emphasis_words || []}
            fontSizePx={Math.round(64 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            strokeColor={pal.stroke_hex}
            align={ev.align ?? "center"}
            mixedSerifItalic={ev.font_style !== "clean_sans"}
            textShadow={ev.text_shadow !== false}
          />
        </div>
      );

    case "dx01.typo.masked_reveal":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 30,
          }}
        >
          <RemocnMaskedRevealText
            title={ev.title || ""}
            kicker={ev.kicker}
            subtitle={ev.subtitle}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            fontSizePx={Math.round(58 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            mutedColor={pal.muted_hex}
            underlineAccent={ev.underline_accent !== false}
            align={ev.align ?? "center"}
          />
        </div>
      );

    case "dx01.typo.karaoke_cue":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 45,
          }}
        >
          <Dx01KaraokeCue
            wordCues={ev.word_cues || []}
            absTimeSec={absTimeSec}
            localFrame={localFrame}
            durationFrames={durationFrames}
            fontSizePx={Math.round(52 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
          />
        </div>
      );

    case "dx01.typo.floating_statement":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 28,
          }}
        >
          <RemocnMaskedRevealText
            title={(ev.lines && ev.lines.join(" ")) || ev.title || ""}
            kicker={ev.kicker}
            subtitle={ev.subtitle}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            fontSizePx={Math.round(48 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            mutedColor={pal.muted_hex}
            underlineAccent={false}
            align={ev.align ?? "center"}
          />
        </div>
      );

    case "dx01.geom.svg_path_draw":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 26 }}>
          <Dx01SvgPathDraw
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            pathKind={ev.path_kind || "underline"}
            points={ev.points}
            strokeColor={pal.accent_hex}
            strokeWidth={Math.max(3, Math.round((ev.stroke_width ?? 7) * sc))}
          />
        </div>
      );

    case "dx01.geom.callout_arrow":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 32 }}>
          <Dx01CalloutArrow
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            fromPoint={ev.from_point || { x: 0.25, y: 0.22 }}
            toPoint={ev.to_point || { x: 0.56, y: 0.42 }}
            controlPoint={ev.control_point}
            label={ev.label}
            accentColor={pal.accent_hex}
            fgColor={pal.fg_hex}
            strokeWidth={Math.max(3, Math.round((ev.stroke_width ?? 6) * sc))}
          />
        </div>
      );

    case "dx01.geom.focus_ring":
    case "dx01.evidence.ui_spotlight":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 29 }}>
          <Dx01FocusRingAndSpotlight
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            boxNorm={ev.focus_box_norm || [0.12, 0.28, 0.76, 0.24]}
            accentColor={pal.accent_hex}
            vignetteOpacity={
              ev.effect_id === "dx01.evidence.ui_spotlight"
                ? (ev.vignette_opacity ?? 0.46)
                : (ev.vignette_opacity ?? 0.0)
            }
            label={ev.label}
            fgColor={pal.fg_hex}
            strokeWidth={Math.max(3, Math.round((ev.stroke_width ?? 6) * sc))}
          />
        </div>
      );

    case "dx01.geom.shape_morph":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            zIndex: ev.z_order ?? 27,
          }}
        >
          <Dx01ShapeMorph
            sizePx={Math.round((ev.w_norm ?? 0.24) * width)}
            localFrame={localFrame}
            durationFrames={durationFrames}
            fromShape={ev.morph_from || "circle"}
            toShape={ev.morph_to || "pill"}
            accentColor={pal.accent_hex}
            fgColor={pal.fg_hex}
            label={ev.label}
          />
        </div>
      );

    case "dx01.data.kinetic_counter":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 34,
          }}
        >
          <RemocnKineticCounter
            fromValue={ev.from_value ?? 0}
            toValue={ev.to_value ?? 100}
            prefix={ev.prefix ?? ""}
            suffix={ev.suffix ?? ""}
            decimals={ev.decimals ?? 0}
            kicker={ev.kicker}
            qualifier={ev.qualifier}
            localFrame={localFrame}
            durationFrames={durationFrames}
            countFrames={Math.max(6, Math.round((ev.count_sec ?? 0.75) * fps))}
            enterFrames={enterFrames}
            exitFrames={exitFrames}
            fontSizePx={Math.round(62 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            positiveColor={pal.positive_hex}
            mutedColor={pal.muted_hex}
          />
        </div>
      );

    case "dx01.data.sequential_tally":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            width: boxW,
            opacity: evOpacity,
            zIndex: ev.z_order ?? 34,
          }}
        >
          <Dx01SequentialTally
            items={ev.items || []}
            absTimeSec={absTimeSec}
            localFrame={localFrame}
            durationFrames={durationFrames}
            fps={fps}
            fontSizePx={Math.round(44 * sc * fontScale)}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            dangerColor={pal.danger_hex}
          />
        </div>
      );

    case "dx01.data.flow_bar":
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            zIndex: ev.z_order ?? 31,
          }}
        >
          <RemocnAnimatedFlowBar
            bars={ev.bars || []}
            maxValue={ev.max_value}
            title={ev.title}
            localFrame={localFrame}
            durationFrames={durationFrames}
            fps={fps}
            eventStartSec={ev.start_sec}
            widthPx={boxW}
            fgColor={pal.fg_hex}
            accentColor={pal.accent_hex}
            positiveColor={pal.positive_hex}
            dangerColor={pal.danger_hex}
            mutedColor={pal.muted_hex}
          />
        </div>
      );

    case "dx01.evidence.before_after_wipe":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 33 }}>
          <RemocnBeforeAfterDivider
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            pauseAt={ev.divider_pause_at ?? 0.5}
            beforeLabel={ev.before_label || "BEFORE"}
            afterLabel={ev.after_label || "AFTER"}
            accentColor={pal.accent_hex}
            fgColor={pal.fg_hex}
          />
        </div>
      );

    case "dx01.atmo.light_sweep":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 15 }}>
          <RemocnLightSweep
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            accentColor={pal.accent_hex}
            angleDeg={ev.angle_deg ?? 28}
            intensity={ev.intensity ?? 0.42}
          />
        </div>
      );

    case "dx01.atmo.seeded_particles_grain":
      return (
        <div key={ev.event_id} style={{ position: "absolute", inset: 0, zIndex: ev.z_order ?? 12 }}>
          <BitsSeededParticlesAndGrain
            width={width}
            height={height}
            localFrame={localFrame}
            durationFrames={durationFrames}
            seed={ev.seed ?? 20261126}
            particleCount={ev.particle_count ?? 18}
            accentColor={pal.accent_hex}
            intensity={ev.intensity ?? 0.35}
            grainOpacity={ev.grain_opacity ?? 0.06}
          />
        </div>
      );

    default:
      return (
        <div
          key={ev.event_id}
          style={{
            position: "absolute",
            left: boxLeft,
            top: boxTop,
            fontFamily: LOCAL_SANS_STACK,
            color: pal.fg_hex,
            fontSize: Math.round(32 * sc),
          }}
        >
          {ev.title || ""}
        </div>
      );
  }
};

export const EffectSpanComposition: React.FC<Dx01SpanCompositionProps> = (props) => {
  const frame = useCurrentFrame();
  const { width, height, fps } = useVideoConfig();
  const absTimeSec = (props.span_start_sec ?? 0) + frame / Math.max(1, fps);
  const globalPal = resolveArtPalette(props.palette);
  const sortedEvents = [...(props.events || [])].sort(
    (a, b) => (a.z_order ?? 20) - (b.z_order ?? 20),
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: props.transparent
          ? "transparent"
          : props.background_hex || globalPal.canvas_hex,
        overflow: "hidden",
      }}
    >
      {sortedEvents.map((ev) =>
        renderSingleEffect(ev, absTimeSec, width, height, fps, globalPal),
      )}
    </AbsoluteFill>
  );
};
