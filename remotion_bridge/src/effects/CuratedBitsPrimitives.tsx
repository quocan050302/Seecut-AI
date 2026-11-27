/**
 * Curated MIT Motion Primitives adapted from `av/remotion-bits`
 * Upstream: https://github.com/av/remotion-bits
 * Pinned SHA: de35fda84b7b6acbe549a0b302a82ef211e8ef1e
 * License: MIT (verified in package.json & README.md; see THIRD_PARTY_NOTICES_DX01.md)
 *
 * Adapted to remove external `culori` / `three` dependencies while preserving
 * 100% deterministic `useCurrentFrame()` / `random(seed)` behavior and zero network access.
 */

import React from "react";
import { Easing, interpolate, random, spring, useVideoConfig } from "remotion";
import {
  ArtPalette,
  DEFAULT_PALETTE,
  LOCAL_SANS_STACK,
  LOCAL_SERIF_STACK,
} from "../types";

const CLAMP = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};

/** Deterministic seeded float helper adapted from av/remotion-bits `src/utils/random.ts`. */
export const seededFloat = (
  seed: number | string,
  min: number,
  max: number,
): number => {
  return random(seed) * (max - min) + min;
};

/** Deterministic seeded integer helper adapted from av/remotion-bits `src/utils/random.ts`. */
export const seededInt = (
  seed: number | string,
  min: number,
  max: number,
): number => {
  return Math.floor(random(seed) * (max - min + 1) + min);
};

/** Staggered word-by-word kinetic text animation (no card chrome, crisp legibility). */
export const BitsStaggeredText: React.FC<{
  text: string;
  localFrame: number;
  durationFrames: number;
  enterFrames: number;
  exitFrames: number;
  emphasisWords?: string[];
  fontSizePx: number;
  fgColor: string;
  accentColor: string;
  strokeColor: string;
  align?: "left" | "center" | "right";
  mixedSerifItalic?: boolean;
  textShadow?: boolean;
}> = ({
  text,
  localFrame,
  durationFrames,
  enterFrames,
  exitFrames,
  emphasisWords = [],
  fontSizePx,
  fgColor,
  accentColor,
  strokeColor,
  align = "center",
  mixedSerifItalic = true,
  textShadow = true,
}) => {
  const { fps } = useVideoConfig();
  const words = text.trim().split(/\s+/).filter(Boolean);
  const emphSet = new Set(emphasisWords.map((w) => w.toLowerCase().replace(/[^a-z0-9$%+-]/g, "")));

  const exitStart = Math.max(enterFrames, durationFrames - exitFrames);
  const outOpacity = interpolate(
    localFrame,
    [exitStart, durationFrames],
    [1, 0],
    { ...CLAMP, easing: Easing.out(Easing.cubic) },
  );
  const outShiftY = interpolate(
    localFrame,
    [exitStart, durationFrames],
    [0, -10],
    { ...CLAMP, easing: Easing.in(Easing.cubic) },
  );

  const stagger = Math.max(2, Math.min(4, Math.floor(enterFrames / Math.max(2, words.length))));

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent:
          align === "left" ? "flex-start" : align === "right" ? "flex-end" : "center",
        alignItems: "baseline",
        columnGap: Math.round(fontSizePx * 0.28),
        rowGap: Math.round(fontSizePx * 0.14),
        opacity: outOpacity,
        transform: `translate3d(0, ${outShiftY}px, 0)`,
      }}
    >
      {words.map((word, idx) => {
        const clean = word.toLowerCase().replace(/[^a-z0-9$%+-]/g, "");
        const isEmph = emphSet.has(clean) || (mixedSerifItalic && (word.endsWith("?") || word.startsWith("$")));
        const wFrame = Math.max(0, localFrame - idx * stagger);
        const sp = spring({
          frame: wFrame,
          fps,
          config: { damping: 16, stiffness: 185, mass: 0.72 },
        });
        const yOff = interpolate(sp, [0, 1], [28, 0], CLAMP);
        const op = interpolate(wFrame, [0, Math.max(3, Math.round(fps * 0.14))], [0, 1], CLAMP);
        const sc = interpolate(sp, [0, 1], [0.92, 1], CLAMP);

        return (
          <span
            key={`${idx}_${word}`}
            style={{
              display: "inline-block",
              fontFamily: isEmph && mixedSerifItalic ? LOCAL_SERIF_STACK : LOCAL_SANS_STACK,
              fontStyle: isEmph && mixedSerifItalic ? "italic" : "normal",
              fontWeight: isEmph && mixedSerifItalic ? 700 : 800,
              fontSize: Math.round(fontSizePx * (isEmph && mixedSerifItalic ? 1.1 : 1.0)),
              lineHeight: 1.08,
              letterSpacing: isEmph && mixedSerifItalic ? "-0.01em" : "-0.025em",
              color: isEmph ? accentColor : fgColor,
              opacity: op,
              transform: `translate3d(0, ${yOff}px, 0) scale(${sc})`,
              textShadow: textShadow
                ? `0 3px 16px rgba(0,0,0,0.68), 0 1px 3px ${strokeColor}`
                : "none",
              WebkitTextStroke: textShadow ? `1.5px rgba(10,12,14,0.48)` : "0px transparent",
            }}
          >
            {word}
          </span>
        );
      })}
    </div>
  );
};

/** Deterministic seeded ambient particles & fine film grain (adapted from av/remotion-bits ParticleSystem). */
export const BitsSeededParticlesAndGrain: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  seed: number;
  particleCount: number;
  accentColor: string;
  intensity: number;
  grainOpacity: number;
}> = ({
  width,
  height,
  localFrame,
  durationFrames,
  seed,
  particleCount,
  accentColor,
  intensity,
  grainOpacity,
}) => {
  const count = Math.max(6, Math.min(36, Math.round(particleCount)));
  const envIn = interpolate(localFrame, [0, 8], [0, 1], CLAMP);
  const envOut = interpolate(
    localFrame,
    [Math.max(8, durationFrames - 8), durationFrames],
    [1, 0],
    CLAMP,
  );
  const env = Math.min(envIn, envOut) * Math.max(0.05, Math.min(1.0, intensity));

  const particles = Array.from({ length: count }, (_, i) => {
    const x0 = seededFloat(`p_${seed}_${i}_x`, 0.06, 0.94) * width;
    const y0 = seededFloat(`p_${seed}_${i}_y`, 0.08, 0.92) * height;
    const vx = seededFloat(`p_${seed}_${i}_vx`, -0.8, 0.8);
    const vy = seededFloat(`p_${seed}_${i}_vy`, -1.6, -0.35);
    const r = seededFloat(`p_${seed}_${i}_r`, 2.2, 6.2) * (width / 1080);
    const phase = seededFloat(`p_${seed}_${i}_ph`, 0, Math.PI * 2);
    const cx = x0 + vx * localFrame + Math.sin(localFrame * 0.09 + phase) * 8;
    const cy = ((y0 + vy * localFrame) % height + height) % height;
    const twinkle = 0.45 + 0.55 * Math.sin(localFrame * 0.16 + phase);
    return { cx, cy, r, op: env * twinkle * 0.65 };
  });

  // Deterministic low-cost grain speckles refreshed every 2 frames
  const gStep = Math.floor(localFrame / 2);
  const grainDots =
    grainOpacity > 0.005
      ? Array.from({ length: 44 }, (_, gi) => ({
          x: seededFloat(`g_${seed}_${gStep}_${gi}_x`, 0, width),
          y: seededFloat(`g_${seed}_${gStep}_${gi}_y`, 0, height),
          s: seededFloat(`g_${seed}_${gStep}_${gi}_s`, 1.2, 2.8),
          light: seededInt(`g_${seed}_${gStep}_${gi}_c`, 0, 1) === 1,
        }))
      : [];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    >
      {particles.map((p, idx) => (
        <circle
          key={`pt_${idx}`}
          cx={p.cx}
          cy={p.cy}
          r={p.r}
          fill={accentColor}
          opacity={p.op}
        />
      ))}
      {grainDots.map((g, idx) => (
        <rect
          key={`gr_${idx}`}
          x={g.x}
          y={g.y}
          width={g.s}
          height={g.s}
          fill={g.light ? "#FFFFFF" : "#000000"}
          opacity={Math.min(0.28, grainOpacity) * envIn * envOut}
        />
      ))}
    </svg>
  );
};

export function resolveArtPalette(pal?: ArtPalette): Required<ArtPalette> {
  return {
    bg_hex: pal?.bg_hex || DEFAULT_PALETTE.bg_hex,
    surface_hex: pal?.surface_hex || DEFAULT_PALETTE.surface_hex,
    accent_hex: pal?.accent_hex || DEFAULT_PALETTE.accent_hex,
    fg_hex: pal?.fg_hex || DEFAULT_PALETTE.fg_hex,
    muted_hex: pal?.muted_hex || DEFAULT_PALETTE.muted_hex,
    danger_hex: pal?.danger_hex || DEFAULT_PALETTE.danger_hex,
    positive_hex: pal?.positive_hex || DEFAULT_PALETTE.positive_hex,
    canvas_hex: pal?.canvas_hex || DEFAULT_PALETTE.canvas_hex,
    stroke_hex: pal?.stroke_hex || DEFAULT_PALETTE.stroke_hex,
  };
}
