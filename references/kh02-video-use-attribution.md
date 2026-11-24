# Selective Third-Party Attribution: `browser-use/video-use` (MIT)

## Provenance & Scope
- **Upstream Reference Repository**: `https://github.com/browser-use/video-use`
- **Pinned Commit SHA**: `43cfc566833548093455c9d4a46bd69913b7e552` (read-only audit checkout; never executed as a second rendering engine)
- **License of `browser-use/video-use`**: MIT License (reproduced verbatim below)

## Exact Adapted Helpers in `seecut-ai` KH-02
1. `scripts/kh02/transcript.py::group_phrases`, `build_packed_transcript` — adapted from `helpers/pack_transcripts.py::group_into_phrases`, `render_markdown` for local `faster-whisper` word timestamps (ElevenLabs Scribe API intentionally excluded).
2. `scripts/kh02/qa.py::render_timeline_diagnostics`, `_compute_pcm_envelope` — adapted from `helpers/timeline_view.py::extract_frames`, `compute_envelope`, `words_in_range`, `find_silences`, `render_timeline` for bounded boundary filmstrip + real PCM waveform + output-clock word label QA diagnostics.
3. `scripts/kh02/typography.py::chunk_words`, `build_master_srt` — adapted from `helpers/render.py::chunk_words`, `build_master_srt` for opt-in (`ON_REQUESTED`) output-timeline SRT generation only.
4. `scripts/kh02/sound.py::measure_final_audio` — loudness measurement pattern referenced alongside FFmpeg `ebur128=peak=true` and `volumedetect` (`helpers/render.py::measure_loudness`).

## Explicit License Boundary Notice
- **IMPORTANT**: The MIT license of `browser-use/video-use` applies **solely** to the selective helper logic adapted above. It does **NOT** re-license `YeJe-cpu/SeeCut` or `seecut-ai` as MIT.
- `YeJe-cpu/SeeCut` (`@b05d7ed27404a064060e0d370402f562b0c0c02e`) and this `seecut-ai` integration remain governed by the **PolyForm Noncommercial License 1.0.0** (`LICENSE`).
- `browser-use/video-use/helpers/render.py` is **NEVER** invoked as a video rendering engine; **YeJe-cpu/SeeCut v3.4 Core** (`HyperFrames + HTML/CSS/GSAP + Playwright + render.mjs`) remains the sole rendering engine.

## Verbatim MIT License (`browser-use/video-use`)

```text
MIT License

Copyright (c) 2026 Browser Use

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
