/**
 * Curated MIT Motion Primitives adapted from `Remocn/remocn`
 * Upstream: https://github.com/Remocn/remocn
 * Pinned SHA: ac1e4081edcc70757db4dc4af32b079cb9ae5490
 * License: MIT License, Copyright (c) 2026 Remocn (see THIRD_PARTY_NOTICES_DX01.md)
 *
 * Adapted cleanly to remove `@remotion/google-fonts` remote network fetches,
 * `@paper-design/shaders-react`, and uninstalled `@remotion/transitions`, keeping
 * deterministic pure Remotion frame-driven motion and local typography.
 */

import React from "react";
import { Easing, interpolate, spring, useVideoConfig } from "remotion";
import { BarItem, LOCAL_SANS_STACK, LOCAL_SERIF_STACK } from "../types";

const CLAMP = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};

const EASE_OUT_EXPO = Easing.bezier(0.22, 1, 0.36, 1);
const EASE_SWEEP = Easing.bezier(0.45, 0, 0.55, 1);

/** Masked baseline typography reveal (non-card, crisp editorial clip-path entrance). */
export const RemocnMaskedRevealText: React.FC<{
  title: string;
  kicker?: string;
  subtitle?: string;
  localFrame: number;
  durationFrames: number;
  enterFrames: number;
  exitFrames: number;
  fontSizePx: number;
  fgColor: string;
  accentColor: string;
  mutedColor: string;
  underlineAccent?: boolean;
  align?: "left" | "center" | "right";
}> = ({
  title,
  kicker,
  subtitle,
  localFrame,
  durationFrames,
  enterFrames,
  exitFrames,
  fontSizePx,
  fgColor,
  accentColor,
  mutedColor,
  underlineAccent = true,
  align = "center",
}) => {
  const inP = interpolate(localFrame, [0, Math.max(4, enterFrames)], [0, 1], {
    ...CLAMP,
    easing: EASE_OUT_EXPO,
  });
  const exitStart = Math.max(enterFrames, durationFrames - exitFrames);
  const outP = interpolate(localFrame, [exitStart, durationFrames], [1, 0], {
    ...CLAMP,
    easing: Easing.out(Easing.cubic),
  });

  const translateY = interpolate(inP, [0, 1], [fontSizePx * 1.15, 0], CLAMP);
  const lineW = interpolate(localFrame, [Math.round(enterFrames * 0.35), Math.round(enterFrames * 1.15)], [0, 100], {
    ...CLAMP,
    easing: EASE_OUT_EXPO,
  });
  const textAlign = align;

  return (
    <div style={{ opacity: outP, textAlign, width: "100%" }}>
      {kicker ? (
        <div style={{ overflow: "hidden", marginBottom: Math.round(fontSizePx * 0.14) }}>
          <div
            style={{
              fontFamily: LOCAL_SANS_STACK,
              fontSize: Math.max(16, Math.round(fontSizePx * 0.36)),
              fontWeight: 700,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: accentColor,
              transform: `translate3d(0, ${interpolate(inP, [0, 1], [24, 0], CLAMP)}px, 0)`,
              textShadow: "0 2px 10px rgba(0,0,0,0.72)",
            }}
          >
            {kicker}
          </div>
        </div>
      ) : null}

      <div style={{ overflow: "hidden", paddingBottom: 4 }}>
        <div
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: fontSizePx,
            fontWeight: 800,
            lineHeight: 1.08,
            letterSpacing: "-0.025em",
            color: fgColor,
            transform: `translate3d(0, ${translateY}px, 0)`,
            textShadow: "0 4px 18px rgba(0,0,0,0.75)",
          }}
        >
          {title}
        </div>
      </div>

      {underlineAccent ? (
        <div
          style={{
            height: Math.max(3, Math.round(fontSizePx * 0.08)),
            width: `${lineW}%`,
            maxWidth: 260,
            marginLeft: align === "center" ? "auto" : align === "right" ? "auto" : 0,
            marginRight: align === "center" ? "auto" : align === "left" ? "auto" : 0,
            marginTop: Math.round(fontSizePx * 0.16),
            borderRadius: 99,
            background: `linear-gradient(90deg, ${accentColor}, ${fgColor})`,
            boxShadow: `0 2px 12px rgba(0,0,0,0.5)`,
          }}
        />
      ) : null}

      {subtitle ? (
        <div style={{ overflow: "hidden", marginTop: Math.round(fontSizePx * 0.18) }}>
          <div
            style={{
              fontFamily: LOCAL_SERIF_STACK,
              fontStyle: "italic",
              fontSize: Math.max(18, Math.round(fontSizePx * 0.46)),
              fontWeight: 600,
              color: mutedColor,
              opacity: interpolate(inP, [0.25, 1], [0, 1], CLAMP),
              textShadow: "0 2px 12px rgba(0,0,0,0.72)",
            }}
          >
            {subtitle}
          </div>
        </div>
      ) : null}
    </div>
  );
};

/** Kinetic Beat-Count numeric delta / payoff (adapted from Remocn `beat-count`, floating editorial form). */
export const RemocnKineticCounter: React.FC<{
  fromValue: number;
  toValue: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  kicker?: string;
  qualifier?: string;
  localFrame: number;
  durationFrames: number;
  countFrames: number;
  enterFrames: number;
  exitFrames: number;
  fontSizePx: number;
  fgColor: string;
  accentColor: string;
  positiveColor: string;
  mutedColor: string;
}> = ({
  fromValue,
  toValue,
  prefix = "",
  suffix = "",
  decimals = 0,
  kicker,
  qualifier,
  localFrame,
  durationFrames,
  countFrames,
  enterFrames,
  exitFrames,
  fontSizePx,
  fgColor,
  accentColor,
  positiveColor,
  mutedColor,
}) => {
  const { fps } = useVideoConfig();
  const inSp = spring({
    frame: localFrame,
    fps,
    config: { damping: 16, stiffness: 170, mass: 0.8 },
  });
  const exitStart = Math.max(enterFrames, durationFrames - exitFrames);
  const outOp = interpolate(localFrame, [exitStart, durationFrames], [1, 0], CLAMP);

  const countStart = Math.max(2, Math.round(enterFrames * 0.45));
  const prog = interpolate(
    localFrame,
    [countStart, countStart + Math.max(6, countFrames)],
    [0, 1],
    { ...CLAMP, easing: EASE_OUT_EXPO },
  );
  const current = fromValue + (toValue - fromValue) * prog;
  const landed = prog >= 0.985;
  const landPulse = landed
    ? 1 +
      0.06 *
        Math.sin(
          interpolate(
            localFrame,
            [countStart + countFrames, countStart + countFrames + 10],
            [0, Math.PI],
            CLAMP,
          ),
        )
    : 1;

  const fmt = (v: number) =>
    v.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });

  const fromStr = `${prefix}${fmt(fromValue)}${suffix}`;
  const curStr = `${prefix}${fmt(decimals === 0 ? Math.round(current) : current)}${suffix}`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        opacity: interpolate(inSp, [0, 0.35], [0, 1], CLAMP) * outOp,
        transform: `translate3d(0, ${interpolate(inSp, [0, 1], [22, 0], CLAMP)}px, 0)`,
      }}
    >
      {kicker ? (
        <div
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.max(16, Math.round(fontSizePx * 0.28)),
            fontWeight: 800,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            color: accentColor,
            textShadow: "0 2px 10px rgba(0,0,0,0.8)",
            marginBottom: 6,
          }}
        >
          {kicker}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: Math.round(fontSizePx * 0.24),
          padding: "8px 22px",
          borderRadius: 999,
          background: "rgba(12, 16, 20, 0.62)",
          backdropFilter: "blur(6px)",
          border: `2px solid ${landed ? positiveColor : accentColor}`,
          boxShadow: "0 10px 28px rgba(0,0,0,0.42)",
        }}
      >
        <span
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.round(fontSizePx * 0.64),
            fontWeight: 700,
            color: mutedColor,
            textDecoration: prog > 0.55 ? "line-through" : "none",
            opacity: prog > 0.55 ? 0.72 : 0.95,
          }}
        >
          {fromStr}
        </span>
        <span
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.round(fontSizePx * 0.56),
            fontWeight: 800,
            color: accentColor,
          }}
        >
          →
        </span>
        <span
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: fontSizePx,
            fontWeight: 900,
            color: landed ? positiveColor : fgColor,
            transform: `scale(${landPulse})`,
            display: "inline-block",
            fontVariantNumeric: "tabular-nums",
            textShadow: "0 3px 14px rgba(0,0,0,0.65)",
          }}
        >
          {curStr}
        </span>
      </div>

      {qualifier ? (
        <div
          style={{
            marginTop: 8,
            fontFamily: LOCAL_SERIF_STACK,
            fontStyle: "italic",
            fontSize: Math.max(17, Math.round(fontSizePx * 0.30)),
            fontWeight: 600,
            color: fgColor,
            textShadow: "0 2px 10px rgba(0,0,0,0.85)",
          }}
        >
          {qualifier}
        </div>
      ) : null}
    </div>
  );
};

/** Animated horizontal data comparison bars (adapted from Remocn `animated-bar-chart`). */
export const RemocnAnimatedFlowBar: React.FC<{
  bars: BarItem[];
  maxValue?: number;
  title?: string;
  localFrame: number;
  durationFrames: number;
  fps: number;
  eventStartSec: number;
  widthPx: number;
  fgColor: string;
  accentColor: string;
  positiveColor: string;
  dangerColor: string;
  mutedColor: string;
}> = ({
  bars,
  maxValue,
  title,
  localFrame,
  durationFrames,
  fps,
  eventStartSec,
  widthPx,
  fgColor,
  accentColor,
  positiveColor,
  dangerColor,
  mutedColor,
}) => {
  const maxVal = Math.max(1e-6, maxValue ?? Math.max(...bars.map((b) => Math.abs(b.value)), 1));
  const outOp = interpolate(
    localFrame,
    [Math.max(6, durationFrames - 6), durationFrames],
    [1, 0],
    CLAMP,
  );
  const colorMap: Record<string, string> = {
    accent: accentColor,
    positive: positiveColor,
    danger: dangerColor,
    muted: mutedColor,
    fg: fgColor,
  };

  return (
    <div
      style={{
        width: widthPx,
        opacity: outOp,
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "16px 20px",
        borderRadius: 18,
        background: "rgba(12, 16, 20, 0.72)",
        border: `1.5px solid rgba(255,255,255,0.16)`,
      }}
    >
      {title ? (
        <div
          style={{
            fontFamily: LOCAL_SANS_STACK,
            fontSize: 22,
            fontWeight: 800,
            color: fgColor,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          {title}
        </div>
      ) : null}
      {bars.map((b, idx) => {
        const relStart =
          b.reveal_sec !== undefined
            ? Math.max(0, Math.round((b.reveal_sec - eventStartSec) * fps))
            : idx * 5;
        const sp = spring({
          frame: Math.max(0, localFrame - relStart),
          fps,
          config: { damping: 18, stiffness: 150, mass: 0.8 },
        });
        const pct = Math.max(4, Math.min(100, (Math.abs(b.value) / maxVal) * 100 * sp));
        const fill = colorMap[b.color_role || "accent"] || accentColor;
        return (
          <div key={`bar_${idx}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontFamily: LOCAL_SANS_STACK,
                fontSize: 22,
                fontWeight: 700,
                color: fgColor,
              }}
            >
              <span>{b.label}</span>
              <span style={{ color: fill, fontVariantNumeric: "tabular-nums" }}>
                {b.display_text ?? String(b.value)}
              </span>
            </div>
            <div
              style={{
                height: 16,
                width: "100%",
                borderRadius: 99,
                background: "rgba(255,255,255,0.12)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${pct}%`,
                  borderRadius: 99,
                  background: fill,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
};

/** Diagonal soft light beam / anamorphic sweep accent (adapted from Remocn `beam-sweep` / `anamorphic-flare`). */
export const RemocnLightSweep: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  accentColor: string;
  angleDeg: number;
  intensity: number;
}> = ({ width, height, localFrame, durationFrames, accentColor, angleDeg, intensity }) => {
  const p = interpolate(localFrame, [0, Math.max(4, durationFrames)], [0, 1], {
    ...CLAMP,
    easing: EASE_SWEEP,
  });
  const sweepX = interpolate(p, [0, 1], [-width * 0.65, width * 1.65], CLAMP);
  const env = Math.sin(p * Math.PI) * Math.max(0.05, Math.min(0.85, intensity));

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: sweepX,
          top: -height * 0.25,
          width: Math.round(width * 0.24),
          height: height * 1.5,
          transform: `rotate(${angleDeg}deg)`,
          background: `linear-gradient(90deg, transparent 0%, ${accentColor}55 42%, #FFFFFF88 50%, ${accentColor}55 58%, transparent 100%)`,
          opacity: env,
          filter: "blur(18px)",
        }}
      />
    </div>
  );
};

/** Clean split divider HUD for Before/After comparison evidence (adapted from Remocn `before-after`). */
export const RemocnBeforeAfterDivider: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  pauseAt: number;
  beforeLabel: string;
  afterLabel: string;
  accentColor: string;
  fgColor: string;
}> = ({
  width,
  height,
  localFrame,
  durationFrames,
  pauseAt,
  beforeLabel,
  afterLabel,
  accentColor,
  fgColor,
}) => {
  const p = interpolate(localFrame, [0, Math.max(6, durationFrames)], [0, 1], CLAMP);
  const posNorm = interpolate(
    p,
    [0, 0.35, 0.65, 1],
    [0.18, pauseAt, pauseAt, 0.84],
    { ...CLAMP, easing: EASE_OUT_EXPO },
  );
  const divX = Math.round(width * posNorm);
  const env = Math.min(
    interpolate(localFrame, [0, 6], [0, 1], CLAMP),
    interpolate(localFrame, [Math.max(6, durationFrames - 6), durationFrames], [1, 0], CLAMP),
  );

  return (
    <div style={{ position: "absolute", inset: 0, opacity: env, pointerEvents: "none" }}>
      <div
        style={{
          position: "absolute",
          left: divX - 2,
          top: Math.round(height * 0.14),
          width: 4,
          height: Math.round(height * 0.72),
          background: accentColor,
          boxShadow: "0 0 18px rgba(0,0,0,0.65)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: divX - 18,
          top: Math.round(height * 0.5) - 18,
          width: 36,
          height: 36,
          borderRadius: 999,
          background: accentColor,
          border: `3px solid ${fgColor}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#101214",
          fontFamily: LOCAL_SANS_STACK,
          fontWeight: 900,
          fontSize: 16,
        }}
      >
        ⇄
      </div>
      <div
        style={{
          position: "absolute",
          left: 36,
          top: Math.round(height * 0.15),
          padding: "6px 14px",
          borderRadius: 999,
          background: "rgba(12,16,20,0.72)",
          color: fgColor,
          fontFamily: LOCAL_SANS_STACK,
          fontWeight: 800,
          fontSize: 22,
          letterSpacing: "0.06em",
        }}
      >
        {beforeLabel}
      </div>
      <div
        style={{
          position: "absolute",
          right: 36,
          top: Math.round(height * 0.15),
          padding: "6px 14px",
          borderRadius: 999,
          background: accentColor,
          color: "#101214",
          fontFamily: LOCAL_SANS_STACK,
          fontWeight: 800,
          fontSize: 22,
          letterSpacing: "0.06em",
        }}
      >
        {afterLabel}
      </div>
    </div>
  );
};
