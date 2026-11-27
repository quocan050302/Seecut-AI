# Third-Party Notices — DX-01 Universal Motion Graphics & Hybrid Remotion Bridge

This document records verified provenance and licenses for all third-party components and runtimes referenced by the DX-01 motion registry and Remotion bridge (`remotion_bridge/` and `scripts/kh03/`).

> **Important Isolation Notice**: DX-01's clean-room independent renderer (`INDEPENDENT_FFMPEG_PIL`) and hybrid Remotion bridge (`HYBRID_REMOTION_FFMPEG`) contain **ZERO** code, templates, or assets copied from `YeJe-cpu/SeeCut` (PolyForm Noncommercial License 1.0.0). `NATIVE_SEECUT` remains strictly isolated behind PROBLEM-01 license checks.

---

## 1. Remotion Runtime (`remotion@4.0.545`)
- **Packages**: `remotion`, `@remotion/bundler`, `@remotion/renderer`, `@remotion/compositor-win32-x64-msvc` (Remotion License); `@remotion/paths`, `@remotion/captions`, `@remotion/media-utils` (MIT).
- **License**: Remotion License (`https://github.com/remotion-dev/remotion/blob/main/LICENSE.md`).
  - Free License covers individuals, non-profit organizations, evaluation, and for-profit organizations with up to 3 employees (including commercial video creation).
  - For-profit organizations with more than 3 employees require a Remotion Company License.
  - Not permitted to resell/relicense a derivative of Remotion itself.
- **Usage**: Invoked via local preinstalled pinned workspace without modifying that workspace or triggering runtime downloads.

---

## 2. Remocn (`https://github.com/Remocn/remocn`)
- **Pinned Commit SHA**: `ac1e4081edcc70757db4dc4af32b079cb9ae5490`
- **License**: MIT License
- **Adapted in**: `remotion_bridge/src/effects/CuratedRemocnPrimitives.tsx` (zero-network local-font deterministic adaptations of split before/after reveal, beat-synced numeric counter, animated data bars, diagonal light sweep/flare, and masked baseline typography).

```text
MIT License

Copyright (c) 2026 Remocn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 3. remotion-bits (`https://github.com/av/remotion-bits`)
- **Pinned Commit SHA**: `de35fda84b7b6acbe549a0b302a82ef211e8ef1e`
- **License**: MIT (declared in `package.json` and `README.md`)
- **Adapted in**: `remotion_bridge/src/effects/CuratedBitsPrimitives.tsx` (deterministic seeded PRNG helpers wrapping Remotion `random(seed)`, staggered word/character motion primitives, deterministic particle field, and formatted numeric counter interpolation without external `culori`/`three` runtime deps).

---

## 4. Guidance-Only Sources (Zero Code Copied)
- `remotion-dev/skills` @ `a3f4098a2afd0e13f6a6f8c8760e275f75457ea9` — official Remotion API usage guidance only.
- `iart-ai/motion-skills` @ `e8ba1f4eb2bcca3d1639d7545b9cd2bcfe4a3c6f` (MIT) — editorial motion design guidance only.
- `haidrrrry/claude-remotion-skill` @ `1dcbe5e3fc6cf970bd10d3cc05f0a8a5d19d0383` (MIT) — typography & shot choreography guidance only.

## 5. Skipped Optional Sources
- `GovinduSasanka/opus-motion-library` @ `7bde15aa904671fb0a1a06c2d88e7012dbab2069` (MIT) — `SKIPPED_SOURCE` (not required for DX-01 scope).
- `reactvideoeditor/remotion-templates` @ `6209b724798e48ff395f8df1a6fa2d26082372b5` — `SKIPPED_SOURCE` (no verifiable top-level license file).
