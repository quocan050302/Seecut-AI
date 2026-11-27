import React from "react";
import { Composition } from "remotion";
import { EffectSpanComposition } from "./EffectSpanComposition";
import { DEFAULT_PALETTE, Dx01SpanCompositionProps } from "./types";

const defaultProps: Dx01SpanCompositionProps = {
  span_id: "default_span",
  width: 1080,
  height: 1920,
  fps: 24,
  duration_in_frames: 48,
  span_start_sec: 0,
  transparent: true,
  palette: DEFAULT_PALETTE,
  events: [],
  seed: 20261126,
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Dx01EffectSpan"
      component={EffectSpanComposition}
      width={1080}
      height={1920}
      fps={24}
      durationInFrames={48}
      defaultProps={defaultProps}
      calculateMetadata={({ props }) => {
        const w = Math.max(64, Math.round(Number(props.width) || 1080));
        const h = Math.max(64, Math.round(Number(props.height) || 1920));
        const fps = Math.max(1, Math.round(Number(props.fps) || 24));
        const dur = Math.max(1, Math.round(Number(props.duration_in_frames) || 48));
        return {
          width: w - (w % 2),
          height: h - (h % 2),
          fps,
          durationInFrames: dur,
        };
      }}
    />
  );
};
