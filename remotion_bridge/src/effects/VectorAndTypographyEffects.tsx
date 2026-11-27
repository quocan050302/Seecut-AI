/**
 * Clean-Room DX-01 Vector Path, Callout, Focus Ring, Shape Morph,
 * Creator Karaoke Cue, Sequential Tally & Evidence Spotlight Effects.
 * Uses `@remotion/paths` (`evolvePath`, `interpolatePath`, `getPointAtLength`, `getTangentAtLength`)
 * with 100% deterministic frame-driven evaluation.
 */

import React from "react";
import { Easing, interpolate, spring, useVideoConfig } from "remotion";
import {
  evolvePath,
  getLength,
  getPointAtLength,
  getTangentAtLength,
  interpolatePath,
} from "@remotion/paths";
import {
  LOCAL_SANS_STACK,
  LOCAL_SERIF_STACK,
  PointNorm,
  TallyItem,
  WordCue,
} from "../types";

const CLAMP = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};
const EASE_OUT = Easing.bezier(0.22, 1, 0.36, 1);

/** Progressive SVG path draw (`evolvePath`) for non-card underlines, dividers, brackets & polylines. */
export const Dx01SvgPathDraw: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  enterFrames: number;
  exitFrames: number;
  pathKind: string;
  points?: PointNorm[];
  strokeColor: string;
  strokeWidth: number;
}> = ({
  width,
  height,
  localFrame,
  durationFrames,
  enterFrames,
  exitFrames,
  pathKind,
  points,
  strokeColor,
  strokeWidth,
}) => {
  const drawP = interpolate(localFrame, [0, Math.max(4, enterFrames)], [0, 1], {
    ...CLAMP,
    easing: EASE_OUT,
  });
  const outOp = interpolate(
    localFrame,
    [Math.max(enterFrames, durationFrames - exitFrames), durationFrames],
    [1, 0],
    CLAMP,
  );

  let d = "";
  if (points && points.length >= 2) {
    d = points
      .map((pt, i) => `${i === 0 ? "M" : "L"} ${(pt.x * width).toFixed(1)} ${(pt.y * height).toFixed(1)}`)
      .join(" ");
  } else if (pathKind === "bracket") {
    const x0 = width * 0.14;
    const x1 = width * 0.86;
    const y0 = height * 0.72;
    const y1 = height * 0.76;
    d = `M ${x0} ${y0} L ${x0} ${y1} L ${x1} ${y1} L ${x1} ${y0}`;
  } else {
    // Organic curved editorial underline
    const x0 = width * 0.18;
    const x1 = width * 0.82;
    const ym = height * 0.24;
    d = `M ${x0} ${ym} Q ${(x0 + x1) * 0.5} ${ym - 14} ${x1} ${ym}`;
  }

  const evo = evolvePath(drawP, d);
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ position: "absolute", inset: 0, opacity: outOp, pointerEvents: "none" }}
    >
      <path
        d={d}
        fill="none"
        stroke="rgba(0,0,0,0.45)"
        strokeWidth={strokeWidth + 4}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={evo.strokeDasharray}
        strokeDashoffset={evo.strokeDashoffset}
      />
      <path
        d={d}
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={evo.strokeDasharray}
        strokeDashoffset={evo.strokeDashoffset}
      />
    </svg>
  );
};

/** Curved SVG Callout Arrow + floating free-space label (`evolvePath` + arrowhead tangent). */
export const Dx01CalloutArrow: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  enterFrames: number;
  exitFrames: number;
  fromPoint: PointNorm;
  toPoint: PointNorm;
  controlPoint?: PointNorm;
  label?: string;
  accentColor: string;
  fgColor: string;
  strokeWidth: number;
}> = ({
  width,
  height,
  localFrame,
  durationFrames,
  enterFrames,
  exitFrames,
  fromPoint,
  toPoint,
  controlPoint,
  label,
  accentColor,
  fgColor,
  strokeWidth,
}) => {
  const p = interpolate(localFrame, [0, Math.max(5, enterFrames)], [0, 1], {
    ...CLAMP,
    easing: EASE_OUT,
  });
  const outOp = interpolate(
    localFrame,
    [Math.max(enterFrames, durationFrames - exitFrames), durationFrames],
    [1, 0],
    CLAMP,
  );

  const fx = fromPoint.x * width;
  const fy = fromPoint.y * height;
  const tx = toPoint.x * width;
  const ty = toPoint.y * height;
  const cx = (controlPoint ? controlPoint.x : (fromPoint.x + toPoint.x) * 0.5) * width;
  const cy = (controlPoint ? controlPoint.y : Math.min(fromPoint.y, toPoint.y) - 0.06) * height;
  const d = `M ${fx.toFixed(1)} ${fy.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${tx.toFixed(1)} ${ty.toFixed(1)}`;
  const evo = evolvePath(p, d);
  const len = Math.max(1, getLength(d));
  const tip = getPointAtLength(d, len * p) ?? { x: tx, y: ty };
  const tan = getTangentAtLength(d, Math.max(0.01, len * p)) ?? { x: tx - fx, y: ty - fy };
  const angleDeg = (Math.atan2(tan.y, tan.x) * 180) / Math.PI;
  const arrowSize = Math.max(12, strokeWidth * 2.4);

  return (
    <div style={{ position: "absolute", inset: 0, opacity: outOp, pointerEvents: "none" }}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        <path
          d={d}
          fill="none"
          stroke="rgba(0,0,0,0.55)"
          strokeWidth={strokeWidth + 4}
          strokeLinecap="round"
          strokeDasharray={evo.strokeDasharray}
          strokeDashoffset={evo.strokeDashoffset}
        />
        <path
          d={d}
          fill="none"
          stroke={accentColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={evo.strokeDasharray}
          strokeDashoffset={evo.strokeDashoffset}
        />
        {p > 0.12 ? (
          <g transform={`translate(${tip.x.toFixed(1)}, ${tip.y.toFixed(1)}) rotate(${angleDeg.toFixed(1)})`}>
            <polygon
              points={`0,0 ${-arrowSize},${-arrowSize * 0.55} ${-arrowSize * 0.72},0 ${-arrowSize},${arrowSize * 0.55}`}
              fill={accentColor}
              stroke="rgba(0,0,0,0.5)"
              strokeWidth={1.5}
            />
          </g>
        ) : null}
      </svg>
      {label ? (
        <div
          style={{
            position: "absolute",
            left: Math.max(24, Math.min(width - 340, fx - 110)),
            top: Math.max(24, fy + 10),
            padding: "8px 18px",
            borderRadius: 999,
            background: "rgba(14, 20, 18, 0.86)",
            border: `2px solid ${accentColor}`,
            boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.round(30 * (width / 1080)),
            fontWeight: 800,
            color: fgColor,
            whiteSpace: "nowrap",
            opacity: interpolate(p, [0, 0.45], [0, 1], CLAMP),
          }}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
};

/** Animated Focus Ring / UI Spotlight Border (non-card vector outline + optional feathered vignette). */
export const Dx01FocusRingAndSpotlight: React.FC<{
  width: number;
  height: number;
  localFrame: number;
  durationFrames: number;
  enterFrames: number;
  exitFrames: number;
  boxNorm: [number, number, number, number];
  accentColor: string;
  vignetteOpacity: number;
  label?: string;
  fgColor: string;
  strokeWidth: number;
}> = ({
  width,
  height,
  localFrame,
  durationFrames,
  enterFrames,
  exitFrames,
  boxNorm,
  accentColor,
  vignetteOpacity,
  label,
  fgColor,
  strokeWidth,
}) => {
  const drawP = interpolate(localFrame, [0, Math.max(4, enterFrames)], [0, 1], {
    ...CLAMP,
    easing: EASE_OUT,
  });
  const outOp = interpolate(
    localFrame,
    [Math.max(enterFrames, durationFrames - exitFrames), durationFrames],
    [1, 0],
    CLAMP,
  );
  const bx = boxNorm[0] * width;
  const by = boxNorm[1] * height;
  const bw = Math.max(24, boxNorm[2] * width);
  const bh = Math.max(24, boxNorm[3] * height);
  const r = Math.min(26, bw * 0.16, bh * 0.28);

  // Rounded-rect SVG path for smooth perimeter draw
  const d = [
    `M ${(bx + r).toFixed(1)} ${by.toFixed(1)}`,
    `L ${(bx + bw - r).toFixed(1)} ${by.toFixed(1)}`,
    `Q ${(bx + bw).toFixed(1)} ${by.toFixed(1)} ${(bx + bw).toFixed(1)} ${(by + r).toFixed(1)}`,
    `L ${(bx + bw).toFixed(1)} ${(by + bh - r).toFixed(1)}`,
    `Q ${(bx + bw).toFixed(1)} ${(by + bh).toFixed(1)} ${(bx + bw - r).toFixed(1)} ${(by + bh).toFixed(1)}`,
    `L ${(bx + r).toFixed(1)} ${(by + bh).toFixed(1)}`,
    `Q ${bx.toFixed(1)} ${(by + bh).toFixed(1)} ${bx.toFixed(1)} ${(by + bh - r).toFixed(1)}`,
    `L ${bx.toFixed(1)} ${(by + r).toFixed(1)}`,
    `Q ${bx.toFixed(1)} ${by.toFixed(1)} ${(bx + r).toFixed(1)} ${by.toFixed(1)} Z`,
  ].join(" ");

  const evo = evolvePath(drawP, d);
  const pulse = 1 + 0.015 * Math.sin(localFrame * 0.24);
  const maskId = `spot_mask_${Math.round(bx)}_${Math.round(by)}`;

  return (
    <div style={{ position: "absolute", inset: 0, opacity: outOp, pointerEvents: "none" }}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        {vignetteOpacity > 0.01 ? (
          <>
            <defs>
              <mask id={maskId}>
                <rect x={0} y={0} width={width} height={height} fill="white" />
                <rect x={bx} y={by} width={bw} height={bh} rx={r} ry={r} fill="black" />
              </mask>
            </defs>
            <rect
              x={0}
              y={0}
              width={width}
              height={height}
              fill={`rgba(6, 10, 14, ${(vignetteOpacity * drawP).toFixed(3)})`}
              mask={`url(#${maskId})`}
            />
          </>
        ) : null}
        <g transform={`translate(${(bx + bw / 2).toFixed(1)}, ${(by + bh / 2).toFixed(1)}) scale(${pulse.toFixed(4)}) translate(${(-(bx + bw / 2)).toFixed(1)}, ${(-(by + bh / 2)).toFixed(1)})`}>
          <path
            d={d}
            fill="none"
            stroke="rgba(0,0,0,0.5)"
            strokeWidth={strokeWidth + 4}
            strokeDasharray={evo.strokeDasharray}
            strokeDashoffset={evo.strokeDashoffset}
          />
          <path
            d={d}
            fill="none"
            stroke={accentColor}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={evo.strokeDasharray}
            strokeDashoffset={evo.strokeDashoffset}
          />
        </g>
      </svg>
      {label ? (
        <div
          style={{
            position: "absolute",
            left: Math.max(24, bx),
            top: Math.max(20, by - 46),
            padding: "4px 14px",
            borderRadius: 999,
            background: accentColor,
            color: "#101214",
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.round(24 * (width / 1080)),
            fontWeight: 800,
            opacity: interpolate(drawP, [0.3, 1], [0, 1], CLAMP),
            boxShadow: "0 4px 12px rgba(0,0,0,0.45)",
          }}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
};

/** Valid closed-topology SVG Shape Morph (`@remotion/paths` `interpolatePath`). */
const SHAPE_PATHS: Record<string, string> = {
  circle: "M 100 20 C 144 20 180 56 180 100 C 180 144 144 180 100 180 C 56 180 20 144 20 100 C 20 56 56 20 100 20 Z",
  diamond: "M 100 14 L 186 100 L 100 186 L 14 100 Z",
  pill: "M 55 48 L 145 48 C 174 48 188 70 188 100 C 188 130 174 152 145 152 L 55 152 C 26 152 12 130 12 100 C 12 70 26 48 55 48 Z",
  chevron: "M 30 30 L 125 30 L 180 100 L 125 170 L 30 170 L 82 100 Z",
};

export const Dx01ShapeMorph: React.FC<{
  sizePx: number;
  localFrame: number;
  durationFrames: number;
  fromShape: string;
  toShape: string;
  accentColor: string;
  fgColor: string;
  label?: string;
}> = ({ sizePx, localFrame, durationFrames, fromShape, toShape, accentColor, fgColor, label }) => {
  const p = interpolate(localFrame, [4, Math.max(10, durationFrames - 8)], [0, 1], {
    ...CLAMP,
    easing: EASE_OUT,
  });
  const op = Math.min(
    interpolate(localFrame, [0, 5], [0, 1], CLAMP),
    interpolate(localFrame, [Math.max(5, durationFrames - 5), durationFrames], [1, 0], CLAMP),
  );
  const p0 = SHAPE_PATHS[fromShape] || SHAPE_PATHS.circle;
  const p1 = SHAPE_PATHS[toShape] || SHAPE_PATHS.pill;
  const morphed = interpolatePath(p, p0, p1);

  return (
    <div style={{ position: "relative", width: sizePx, height: sizePx, opacity: op }}>
      <svg width={sizePx} height={sizePx} viewBox="0 0 200 200">
        <path d={morphed} fill={`${accentColor}30`} stroke={accentColor} strokeWidth={7} />
      </svg>
      {label ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: LOCAL_SANS_STACK,
            fontSize: Math.round(sizePx * 0.16),
            fontWeight: 800,
            color: fgColor,
            textShadow: "0 2px 10px rgba(0,0,0,0.8)",
          }}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
};

/** Word-synced creator karaoke cue (2-5 words, bold sans + italic serif emphasis, zero opaque box). */
export const Dx01KaraokeCue: React.FC<{
  wordCues: WordCue[];
  absTimeSec: number;
  localFrame: number;
  durationFrames: number;
  fontSizePx: number;
  fgColor: string;
  accentColor: string;
}> = ({ wordCues, absTimeSec, localFrame, durationFrames, fontSizePx, fgColor, accentColor }) => {
  const env = Math.min(
    interpolate(localFrame, [0, 4], [0, 1], CLAMP),
    interpolate(localFrame, [Math.max(4, durationFrames - 4), durationFrames], [1, 0], CLAMP),
  );

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        alignItems: "baseline",
        columnGap: Math.round(fontSizePx * 0.36),
        rowGap: Math.round(fontSizePx * 0.16),
        opacity: env,
      }}
    >
      {wordCues.map((wc, i) => {
        const wText = wc.text || (wc as unknown as { word?: string }).word || "";
        const spoken = absTimeSec >= wc.start_sec - 0.02;
        const active = absTimeSec >= wc.start_sec - 0.02 && absTimeSec <= wc.end_sec + 0.08;
        const useSerif = Boolean(wc.serif_italic);
        const popScale = active ? 1.07 : 1.0;
        const col = active || wc.emphasis ? accentColor : spoken ? fgColor : "rgba(255,255,255,0.68)";
        return (
          <span
            key={`wc_${i}_${wText}`}
            style={{
              display: "inline-block",
              fontFamily: useSerif ? LOCAL_SERIF_STACK : LOCAL_SANS_STACK,
              fontStyle: useSerif ? "italic" : "normal",
              fontWeight: useSerif ? 700 : 900,
              fontSize: Math.round(fontSizePx * (useSerif ? 1.08 : 1.0)),
              lineHeight: 1.08,
              color: col,
              transform: `scale(${popScale})`,
              textShadow: "0 3px 12px rgba(0,0,0,0.88), 0 0 2px rgba(0,0,0,0.95)",
              WebkitTextStroke: "2px rgba(10,12,14,0.72)",
            }}
          >
            {wText}
          </span>
        );
      })}
    </div>
  );
};

/** Non-spoiler staggered sequential tally (reveals each item strictly at `reveal_sec` with optional strike). */
export const Dx01SequentialTally: React.FC<{
  items: TallyItem[];
  absTimeSec: number;
  localFrame: number;
  durationFrames: number;
  fps: number;
  fontSizePx: number;
  fgColor: string;
  accentColor: string;
  dangerColor: string;
}> = ({
  items,
  absTimeSec,
  localFrame,
  durationFrames,
  fps,
  fontSizePx,
  fgColor,
  accentColor,
  dangerColor,
}) => {
  const outOp = interpolate(
    localFrame,
    [Math.max(4, durationFrames - 5), durationFrames],
    [1, 0],
    CLAMP,
  );

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: Math.round(fontSizePx * 0.42),
        opacity: outOp,
      }}
    >
      {items.map((item, idx) => {
        if (absTimeSec < item.reveal_sec - 0.001) {
          return null;
        }
        const fSince = Math.max(0, Math.round((absTimeSec - item.reveal_sec) * fps));
        const sp = spring({
          frame: fSince,
          fps,
          config: { damping: 14, stiffness: 210, mass: 0.7 },
        });
        const struck = item.strike_after_sec !== undefined && absTimeSec >= item.strike_after_sec;
        return (
          <div
            key={`ti_${idx}`}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              padding: "8px 18px",
              borderRadius: 999,
              background: struck ? "rgba(180, 50, 50, 0.78)" : "rgba(12, 16, 20, 0.72)",
              border: `2px solid ${struck ? dangerColor : item.accent ? accentColor : fgColor}`,
              transform: `translate3d(0, ${interpolate(sp, [0, 1], [18, 0], CLAMP)}px, 0) scale(${interpolate(sp, [0, 1], [0.85, 1], CLAMP)})`,
              boxShadow: "0 6px 18px rgba(0,0,0,0.45)",
            }}
          >
            <span
              style={{
                fontFamily: LOCAL_SANS_STACK,
                fontSize: fontSizePx,
                fontWeight: 900,
                color: struck ? "#FFF0F0" : accentColor,
                textDecoration: struck ? "line-through" : "none",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {item.value_text}
            </span>
            {item.label ? (
              <span
                style={{
                  fontFamily: LOCAL_SANS_STACK,
                  fontSize: Math.max(14, Math.round(fontSizePx * 0.36)),
                  fontWeight: 700,
                  color: fgColor,
                  opacity: 0.9,
                }}
              >
                {item.label}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
};
