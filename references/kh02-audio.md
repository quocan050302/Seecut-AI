# KH-02 Sound Direction & Audio QA (`kh02-audio.md`)

Sound direction is authored in **Phase 02** inside `SHOTBOOK_V2.json` (`audio_cues[]`) before graphics compilation; actual audio materialization/mixing happens in **Phase 05** (and final EBU R128 verification in **Phase 06**).

## 1. Voice Backbone & Rights Policy
- **Default `LOCK_VOICE`**: Original spoken words, cadence, and file SHA-256 remain untouched (`-c:a copy`). Voice cloning, TTS replacement, and silent loudness alteration are forbidden.
- **Explicit `TRIM_APPROVED`**: Only when the brief explicitly approves silence/retake cuts, `edit_decisions.py::apply_approved_cut_map()` builds a derived A/V intermediate with safe adaptive `<= 30ms` boundary fades (never mutating the raw source, never cutting mid-word).
- **Optional Licensed SFX & BGM**: Never synthesize placeholder SFX or download unauthorized music. When no verified licensed SFX/BGM library exists, `sound.py::plan_audio_cues()` records `SFX_NOT_AVAILABLE` / `BGM_NOT_AVAILABLE` honestly (non-blocking for core talking-head delivery, blocking only if brief explicitly sets `REQUIRED_LICENSED`).

## 2. Cue Placement & Guarded Validation (`scripts/kh02/sound.py`)
- Preserves upstream `sfx.py` onset timing offsets (`impact: +0.12s`, `whoosh: -0.08s`) while clamping negative start times safely to `>= 0.0s` and validating finite gain (`[-60, +12] dB`), exact frame/sample bounds, non-empty licensed asset files, and density warnings (`<= 2 events / 3s`, `<= 0.7 / s`).

## 3. Real EBU R128 Measurement & Honest Perceptual Status
- `measure_final_audio()` runs real `ffmpeg -af ebur128=peak=true` and `volumedetect`. Missing measurements fail QA (`AUDIO_UNMEASURED`) — loudness is never inferred or fabricated.
- Under `LOCK_VOICE`, discrepancies against profile targets (e.g., `-16 ± 2 LUFS` social voice-forward target) are measured and reported with an actionable recommendation rather than silently altering the voice track.
- `inspect_audio()` computes dry-vs-final PCM Pearson correlation (`>= 0.995` required under `LOCK_VOICE`).
- Waveform/LUFS checks never substitute for human listening: `OWNER_AUDIO=PENDING` and `AUTO_PERCEPTUAL=PENDING` remain explicit until owner playback signoff.
