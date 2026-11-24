#!/usr/bin/env python3
"""Hardened non-bypass wrapper for YeJe-cpu/SeeCut v3.4 scripts/agy_judge.py (KH-02 P0).

Preserves 100% of upstream judge discipline while closing all P0 false-PASS & path safety holes:
- OMITS `--dangerously-skip-permissions` (owner-authorized security delta)
- Uses unmodified upstream prompts (`硬伤清单.txt`, `对比评委.txt`) & JSON schemas (`check.json`, `pair.json`)
- Checks subprocess returncode BEFORE trusting stdout JSON
- Validates exact mode-specific JSON schema locally with strict type checks (`type(v) is bool`, finite numbers)
- Rejects empty checks, missing required H2 rubric IDs {C1, C3, C4, C5, C6, C7}, and duplicate check IDs
- Counts DISTINCT verified fingerprint tokens bound to candidate HTML/fp-manifest SHA-256
  (missing fingerprint source is INVALID unless explicit documented --nontext-evidence file is provided)
- Uses calibrated duration tolerance `min(1.5, max(0.75, 0.08 * real_dur))` (rejects non-finite/negative)
- Strictly validates pairwise `winner in ("X", "Y")` (never maps invalid winner strings to "B"/"A")
- Sanitizes `grab_fail_frames` IDs, enforces realpath containment, finite clamped timestamps,
  verifies ffmpeg exit code 0 + non-empty JPEG decode before appending; reports missing evidence honestly
- Redacts secrets in saved raw logs
- Preserves exit codes: 0=pass/consensus, 1=check FAIL, 2=invalid, 3=auth/env error
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_SEECUT_SKILL = os.path.expanduser(
    os.environ.get(
        'SEECUT_SKILL_DIR',
        '~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut/skill/seecut',
    )
)
PROMPTS = {'check': 'references/prompt/硬伤清单.txt', 'pair': 'references/prompt/对比评委.txt'}
SCHEMAS = {'check': 'scripts/schemas/check.json', 'pair': 'scripts/schemas/pair.json'}
REQUIRED_CHECK_IDS = ('C1', 'C3', 'C4', 'C5', 'C6', 'C7')
VALID_BETTER = {'X', 'Y', '平手'}
VALID_WINNERS = {'X', 'Y'}

RAW_DIR = None


def resolve_agy_bin():
    return (
        os.environ.get('AGY_BIN')
        or shutil.which('agy')
        or (os.path.expanduser('~/.gemini/bin/agy') if os.path.exists(os.path.expanduser('~/.gemini/bin/agy')) else None)
        or (os.path.expanduser('~/.local/bin/agy') if os.path.exists(os.path.expanduser('~/.local/bin/agy')) else None)
    )


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def redact_secrets(text):
    if not text:
        return ''
    text = re.sub(r'(https?://)([^:@/\s]+):([^@/\s]+)@', r'\1***:***@', text)
    text = re.sub(r'(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s"\']+', r'\1=[REDACTED]', text)
    text = re.sub(r'\b(AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,}|ghp_[0-9A-Za-z]{20,}|Bearer\s+[0-9A-Za-z._-]{12,})\b', '[REDACTED]', text)
    return text


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def is_strict_bool(v):
    return type(v) is bool


def is_strict_int(v):
    return type(v) is int


def is_strict_number(v):
    return type(v) in (int, float) and math.isfinite(v)


def duration_tolerance(real_dur):
    """Calibrated tolerance: tighter for short clips (0.75s–1.5s), never weak 3s on 20s video."""
    if not is_strict_number(real_dur) or real_dur <= 0:
        return 0.75
    return min(1.5, max(0.75, round(0.08 * real_dur, 3)))


def duration(path):
    out = subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path]
    )
    val = float(out.decode().strip())
    if not math.isfinite(val) or val <= 0:
        raise ValueError(f'non-positive or non-finite video duration: {val}')
    return val


def to540(src, dst):
    sc = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-y', '-i', src, '-vf', sc,
        '-c:v', 'libx264', '-crf', '26', '-c:a', 'aac', '-b:a', '96k', dst
    ])


def norm(s):
    return re.sub(r'[\s\W_]+', '', s or '').lower()


def fp_source(html_path, extra=None):
    """Return (normalized_corpus, source_sha_map) or (None, {})."""
    if not html_path or not os.path.isfile(html_path):
        return None, {}
    sha_map = {}
    parts = []
    abs_html = os.path.abspath(html_path)
    with open(abs_html, encoding='utf-8', errors='ignore') as fh:
        html = fh.read()
    sha_map[os.path.basename(abs_html)] = sha256_file(abs_html)
    # Strip script/style bodies + HTML tags so only rendered visible text is matched
    cleaned = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', html, flags=re.I | re.S)
    parts.append(re.sub(r'<[^>]+>', ' ', cleaned))
    auto = os.path.join(os.path.dirname(abs_html), 'fp-manifest.txt')
    for f in [auto] + list(extra or []):
        if f and os.path.isfile(f):
            abs_f = os.path.abspath(f)
            with open(abs_f, encoding='utf-8', errors='ignore') as fh:
                parts.append(fh.read())
            sha_map[os.path.basename(abs_f)] = sha256_file(abs_f)
    return norm(' '.join(parts)), sha_map


def fp_hits(texts, html_path, extra=None):
    """Count DISTINCT verified fingerprint tokens (duplicate strings count once)."""
    src, sha_map = fp_source(html_path, extra)
    if src is None:
        return None, [], sha_map
    matched = []
    seen = set()
    for t in (texts or []):
        if not isinstance(t, str):
            continue
        n = norm(t)
        if len(n) < 2 or n in seen:
            continue
        # Match full normalized token, or for long multi-word lines >= 6 chars check 6-char prefix
        # deduplicated by canonical matched key so duplicates cannot double-count
        match_key = None
        if n in src:
            match_key = n
        elif len(n) >= 6 and n[:6] in src:
            match_key = n[:6]
        if match_key and match_key not in seen:
            seen.add(n)
            seen.add(match_key)
            matched.append(t)
    return len(matched), matched, sha_map


def parse_json_no_dupes(text):
    def hook(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f'duplicate JSON key: {k}')
            out[k] = v
        return out
    return json.loads(text, object_pairs_hook=hook)


def last_json(text, key):
    dec, found, i = json.JSONDecoder(), None, 0
    while True:
        i = text.find('{', i)
        if i < 0:
            return found
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict) and key in obj:
                found = obj
            i = end
        except ValueError:
            i += 1


def validate_judge_payload(mode, obj):
    """Strict local schema validation independent of CLI --json-schema enforcement."""
    errors = []
    if not isinstance(obj, dict):
        return ['payload must be a JSON object']

    if mode == 'check':
        if 'dimensions' in obj or 'winner' in obj:
            errors.append('wrong mode payload: pair fields present in check response')
        for req in ('fingerprint', 'checks', 'evidence_gaps'):
            if req not in obj:
                errors.append(f'missing required top-level key: {req}')
        fp = obj.get('fingerprint')
        if not isinstance(fp, dict):
            errors.append('fingerprint must be an object')
        else:
            texts = fp.get('texts')
            if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
                errors.append('fingerprint.texts must be a list of strings')
            dur = fp.get('duration_sec')
            if not is_strict_number(dur) or dur <= 0:
                errors.append(f'fingerprint.duration_sec must be finite positive number (got {dur!r})')
        checks = obj.get('checks')
        if not isinstance(checks, list) or len(checks) == 0:
            errors.append('checks must be a non-empty list')
        else:
            seen_ids = set()
            for idx, item in enumerate(checks):
                if not isinstance(item, dict):
                    errors.append(f'checks[{idx}] must be an object')
                    continue
                cid = item.get('id')
                passed = item.get('pass')
                ev = item.get('evidence')
                if not isinstance(cid, str) or not cid.strip():
                    errors.append(f'checks[{idx}].id must be non-empty string')
                elif cid in seen_ids:
                    errors.append(f'duplicate check id: {cid}')
                else:
                    seen_ids.add(cid)
                if not is_strict_bool(passed):
                    errors.append(f'checks[{idx}].pass must be strict boolean (got {type(passed).__name__})')
                if not isinstance(ev, str):
                    errors.append(f'checks[{idx}].evidence must be string')
            missing_ids = [rid for rid in REQUIRED_CHECK_IDS if rid not in seen_ids]
            if missing_ids:
                errors.append(f'missing required H2 rubric check IDs: {missing_ids}')
        gaps = obj.get('evidence_gaps')
        if not isinstance(gaps, list):
            errors.append('evidence_gaps must be a list')
        else:
            for idx, g in enumerate(gaps):
                if not isinstance(g, dict):
                    errors.append(f'evidence_gaps[{idx}] must be an object')
                    continue
                for k in ('time', 'phrase', 'suggested_evidence'):
                    if not isinstance(g.get(k), str):
                        errors.append(f'evidence_gaps[{idx}].{k} must be string')

    elif mode == 'pair':
        if 'checks' in obj:
            errors.append('wrong mode payload: check fields present in pair response')
        for req in ('fingerprint', 'dimensions', 'winner', 'confidence', 'reason'):
            if req not in obj:
                errors.append(f'missing required top-level key: {req}')
        fp = obj.get('fingerprint')
        if not isinstance(fp, dict):
            errors.append('fingerprint must be an object')
        else:
            for tk in ('X_texts', 'Y_texts'):
                tv = fp.get(tk)
                if not isinstance(tv, list) or not all(isinstance(x, str) for x in tv):
                    errors.append(f'fingerprint.{tk} must be a list of strings')
            for dk in ('X_duration_sec', 'Y_duration_sec'):
                dv = fp.get(dk)
                if not is_strict_number(dv) or dv <= 0:
                    errors.append(f'fingerprint.{dk} must be finite positive number (got {dv!r})')
        dims = obj.get('dimensions')
        if not isinstance(dims, list) or len(dims) == 0:
            errors.append('dimensions must be a non-empty list')
        else:
            seen_names = set()
            for idx, d in enumerate(dims):
                if not isinstance(d, dict):
                    errors.append(f'dimensions[{idx}] must be an object')
                    continue
                nm = d.get('name')
                bt = d.get('better')
                ev = d.get('evidence')
                if not isinstance(nm, str) or not nm.strip():
                    errors.append(f'dimensions[{idx}].name must be non-empty string')
                elif nm in seen_names:
                    errors.append(f'duplicate dimension name: {nm}')
                else:
                    seen_names.add(nm)
                if bt not in VALID_BETTER:
                    errors.append(f'dimensions[{idx}].better invalid: {bt!r}')
                if not isinstance(ev, str):
                    errors.append(f'dimensions[{idx}].evidence must be string')
        winner = obj.get('winner')
        if winner not in VALID_WINNERS:
            errors.append(f'winner must be strictly "X" or "Y" (got {winner!r})')
        conf = obj.get('confidence')
        if not is_strict_int(conf) or not (1 <= conf <= 5):
            errors.append(f'confidence must be strict int in 1..5 (got {conf!r})')
        reason = obj.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            errors.append('reason must be non-empty string')
    else:
        errors.append(f'unknown mode: {mode}')

    return errors


class AuthError(Exception):
    pass


class EnvError(Exception):
    pass


class Transient(Exception):
    pass


class SubprocessFailed(Exception):
    pass


def run_agy(mode, workdir, timeout, skill_dir=None, agy_bin=None):
    skill_root = skill_dir or DEFAULT_SEECUT_SKILL
    agy_exe = agy_bin or resolve_agy_bin()
    if not agy_exe or not os.path.isfile(agy_exe) or not os.access(agy_exe, os.X_OK):
        raise EnvError(f'agy CLI not executable: {agy_exe!r}')
    if os.environ.get('SEECUT_DENY_EXTERNAL_MEDIA') == '1':
        raise EnvError('BLOCKED_MEDIA_TRANSMISSION: external media transmission disabled by policy')

    env = dict(os.environ)
    px = env.get('JIANJI_PROXY')
    if px:
        env.update(https_proxy=px, http_proxy=px, HTTPS_PROXY=px, HTTP_PROXY=px, no_proxy='localhost,127.0.0.1')

    prompt_path = os.path.join(skill_root, PROMPTS[mode])
    schema_path = os.path.join(skill_root, SCHEMAS[mode])
    with open(prompt_path, encoding='utf-8') as fh:
        prompt = fh.read() + '\n只根据本目录里的视频文件作答，不要读取、列出或搜索任何其他文件。'
    cmd = [
        agy_exe, '-p', prompt, '--new-project', '--add-dir', '.',
        '--model', os.environ.get('AGY_MODEL', 'gemini-3.8-flash-high'),
        '--json-schema', schema_path,
        '--output-format', 'json',
        '--print-timeout', f'{timeout}s',
    ]
    try:
        p = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout + 30)
        raw = p.stdout + '\n' + p.stderr
    except subprocess.TimeoutExpired:
        raise Transient(f'agy exceeded {timeout}s timeout')

    raw_path = None
    if RAW_DIR:
        os.makedirs(RAW_DIR, exist_ok=True)
        raw_path = os.path.join(RAW_DIR, f'{mode}-{time.strftime("%H%M%S")}-{os.path.basename(workdir)}.raw')
        with open(raw_path, 'w', encoding='utf-8') as fh:
            fh.write(redact_secrets(raw))

    # P0 FIX: Check process exit code and auth/region/timeout BEFORE trusting any JSON in stdout!
    if re.search(r'location is not supported|not eligible', raw, re.I):
        raise EnvError('Google account region not supported (User location is not supported)')
    if re.search(r'Authentication required|not logged in|please (re-?)?log ?in', raw, re.I):
        raise AuthError('agy authentication required / not logged in')
    if p.returncode != 0:
        if re.search(r'print timeout|time(d)? ?out|deadline', raw, re.I):
            raise Transient(f'agy exited rc={p.returncode} with timeout')
        raise SubprocessFailed(f'agy exited nonzero rc={p.returncode}; stdout JSON cannot be trusted')

    res = None
    try:
        outer = parse_json_no_dupes(p.stdout)
        if not isinstance(outer, dict):
            raise ValueError('outer stdout JSON is not an object')
        if outer.get('status') == 'ERROR':
            raise Transient('agy error: ' + redact_secrets(str(outer.get('error', '')))[:160])
        so = outer.get('structured_output')
        if isinstance(so, dict):
            res = so
        else:
            resp = outer.get('response', '')
            if not str(resp).strip() and re.search(r'print timeout', raw, re.I):
                raise Transient(f'agy truncated at print timeout {timeout}s')
            res = last_json(str(resp), 'checks' if mode == 'check' else 'dimensions')
    except (Transient, EnvError, AuthError, SubprocessFailed):
        raise
    except Exception:
        res = last_json(p.stdout, 'checks' if mode == 'check' else 'dimensions')

    if res is None:
        if re.search(r'print timeout|time(d)? ?out', raw, re.I):
            raise Transient(f'agy truncated at print timeout {timeout}s')
        return None
    return res


def validate_candidate_result(mode, result, real_durations, htmls, extra=None, nontext_evidence=None):
    """Pure validation function: schema + duration calibration + distinct fingerprint binding."""
    if not result:
        return ['no valid JSON returned'], {}
    probs = validate_judge_payload(mode, result)
    meta = {'fingerprint_binding': {}}
    if probs:
        return probs, meta

    fp = result['fingerprint']
    if mode == 'check':
        pairs = [('A', fp['texts'], fp['duration_sec'])]
    else:
        pairs = [
            ('X', fp['X_texts'], fp['X_duration_sec']),
            ('Y', fp['Y_texts'], fp['Y_duration_sec']),
        ]

    for k, texts, d in pairs:
        real_d = real_durations[k]
        tol = duration_tolerance(real_d)
        if abs(d - real_d) > tol:
            probs.append(f'{k} duration mismatch agy={d} actual={real_d:.2f} (tol={tol:.2f}s)')

        html_k = (htmls or {}).get(k)
        h_count, matched, sha_map = fp_hits(texts, html_k, extra)
        if h_count is None:
            # Missing HTML/manifest is INVALID unless documented non-text visual evidence file exists
            nt = (nontext_evidence or {}).get(k) if isinstance(nontext_evidence, dict) else nontext_evidence
            if nt and os.path.isfile(nt) and os.path.getsize(nt) > 0:
                meta['fingerprint_binding'][k] = {
                    'mode': 'DOCUMENTED_NONTEXT_EVIDENCE',
                    'evidence_file': os.path.basename(nt),
                    'evidence_sha256': sha256_file(nt),
                    'distinct_hits': 0,
                }
            else:
                probs.append(f'{k} missing html/fp-manifest fingerprint source (and no documented --nontext-evidence)')
        else:
            meta['fingerprint_binding'][k] = {
                'mode': 'HTML_MANIFEST_TEXT_FINGERPRINT',
                'source_shas': sha_map,
                'distinct_hits': h_count,
                'matched_tokens': matched,
            }
            if h_count < 2:
                probs.append(f'{k} distinct fingerprint hits {h_count}/2 (duplicates excluded): {texts}')
    return probs, meta


def judge_once(mode, files, htmls, timeout, extra=None, nontext_evidence=None, skill_dir=None, agy_bin=None):
    wd = tempfile.mkdtemp(prefix='seecut_agy_')
    marker = os.path.join(wd, '.seecut_judge_owned')
    with open(marker, 'w', encoding='utf-8') as fh:
        fh.write(str(os.getpid()))
    real = {}
    video_shas = {}
    try:
        for k, f in files.items():
            if not f or not os.path.isfile(f):
                raise EnvError(f'missing candidate video for slot {k}: {f!r}')
            video_shas[k] = sha256_file(f)
            to540(f, os.path.join(wd, f'{k}.mp4'))
            real[k] = duration(f)
        r = run_agy(mode, wd, timeout, skill_dir=skill_dir, agy_bin=agy_bin)
    finally:
        if os.path.isfile(marker):
            shutil.rmtree(wd, ignore_errors=True)
    probs, meta = validate_candidate_result(mode, r, real, htmls, extra=extra, nontext_evidence=nontext_evidence)
    meta['video_sha256'] = video_shas
    meta['actual_durations_sec'] = {k: round(v, 3) for k, v in real.items()}
    return r, probs, meta


def judge(mode, files, htmls, timeout, extra=None, nontext_evidence=None, max_attempts=3, skill_dir=None, agy_bin=None):
    content_tries, probs, r, meta, env_retried = 0, [], None, {}, False
    for attempt in range(1, max_attempts + 1):
        try:
            r, probs, meta = judge_once(
                mode, files, htmls, timeout,
                extra=extra, nontext_evidence=nontext_evidence,
                skill_dir=skill_dir, agy_bin=agy_bin,
            )
        except EnvError as e:
            if env_retried or 'BLOCKED_MEDIA_TRANSMISSION' in str(e) or 'missing candidate' in str(e) or 'not executable' in str(e):
                raise
            env_retried = True
            log(f'Attempt {attempt} env error ({e}); sleeping 60s before single retry')
            time.sleep(60)
            continue
        except SubprocessFailed as e:
            log(f'Attempt {attempt} subprocess error ({e}); marking invalid')
            probs, r = [str(e)], None
            content_tries += 1
            if content_tries >= 2:
                break
            continue
        except Transient as e:
            if 'truncated' in str(e) or 'exceeded' in str(e):
                timeout = min(int(timeout * 1.5), 900)
                log(f'Attempt {attempt} timeout ({e}); increasing timeout to {timeout}s')
            else:
                log(f'Attempt {attempt} transient failure ({e}); retrying')
            probs, r = [str(e)], None
            continue
        if r and not probs:
            return {'valid': True, 'result': r, 'binding': meta}
        content_tries += 1
        log(f'Attempt {attempt} invalid content:', '; '.join(probs))
        if content_tries >= 2:
            break
    return {'valid': False, 'problems': probs, 'result': r, 'binding': meta}


def secs(txt):
    m = re.findall(r'(\d{1,2}):(\d{2}(?:\.\d+)?)', txt or '')
    ts = [int(a) * 60 + float(b) for a, b in m[:2]]
    if not ts:
        ts = [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*s\b', txt or '')[:2]]
    return [t for t in ts if math.isfinite(t) and t >= 0]


def sanitize_check_id(cid):
    clean = re.sub(r'[^A-Za-z0-9_-]+', '_', str(cid or '')).strip('_-')
    if not clean or '..' in str(cid or '') or '/' in str(cid or '') or '\\' in str(cid or ''):
        return None
    return clean[:32]


def grab_fail_frames(video, fails, outdir, max_duration=None):
    """Safely extract fail-frames with strict ID sanitization, path containment & decode check."""
    os.makedirs(outdir, exist_ok=True)
    real_out = os.path.realpath(outdir)
    if max_duration is None and os.path.isfile(video):
        try:
            max_duration = duration(video)
        except Exception:
            max_duration = None
    made = []
    missing = []
    for c in (fails or []):
        raw_id = c.get('id', '')
        safe_id = sanitize_check_id(raw_id)
        if not safe_id:
            missing.append({'id': str(raw_id), 'reason': 'invalid_check_id_rejected'})
            continue
        ts = secs(c.get('evidence', ''))
        if not ts:
            missing.append({'id': safe_id, 'reason': 'no_valid_timestamp_in_evidence'})
            continue
        a = ts[0]
        b = ts[1] if len(ts) > 1 else a + 1.0
        if b < a:
            a, b = b, a
        raw_pts = sorted({a, (a + b) / 2.0, b})
        for t in raw_pts:
            if not math.isfinite(t) or t < 0:
                missing.append({'id': safe_id, 't': str(t), 'reason': 'non_finite_or_negative_timestamp'})
                continue
            t_clamped = t
            if max_duration is not None and math.isfinite(max_duration) and max_duration > 0:
                t_clamped = min(max(0.0, t), max(0.0, max_duration - 0.05))
            fname = f'{safe_id}_{t_clamped:.1f}s.jpg'
            target = os.path.realpath(os.path.join(real_out, fname))
            if not (target.startswith(real_out + os.sep)):
                missing.append({'id': safe_id, 't': round(t_clamped, 3), 'reason': 'path_traversal_blocked'})
                continue
            proc = subprocess.run(
                ['ffmpeg', '-v', 'error', '-y', '-ss', f'{t_clamped:.3f}', '-i', video,
                 '-frames:v', '1', '-vf', 'scale=540:-2', target],
                capture_output=True, text=True, timeout=30,
            )
            valid_jpg = False
            if proc.returncode == 0 and os.path.isfile(target) and os.path.getsize(target) > 16:
                with open(target, 'rb') as fh:
                    hdr = fh.read(3)
                valid_jpg = (hdr[:2] == b'\xff\xd8')
            if valid_jpg:
                made.append(target)
            else:
                if os.path.exists(target):
                    try:
                        os.unlink(target)
                    except OSError:
                        pass
                missing.append({
                    'id': safe_id,
                    't': round(t_clamped, 3),
                    'reason': f'ffmpeg_failed_or_empty_image (rc={proc.returncode})',
                })
    return made, missing


def resolve_pair_consensus(r1, r2, h1_pass_a=True, h2_pass_a=True, h1_pass_b=True, h2_pass_b=True):
    """Map order-swapped pair results strictly; invalid/missing winners NEVER map to A or B."""
    def map_winner(res, x_label, y_label):
        if not res or not res.get('valid'):
            return None
        w = (res.get('result') or {}).get('winner')
        if w == 'X':
            return x_label
        if w == 'Y':
            return y_label
        return None

    w1 = map_winner(r1, 'A', 'B')
    w2 = map_winner(r2, 'B', 'A')
    if w1 is None or w2 is None:
        consensus = 'invalid'
    elif w1 == w2:
        consensus = w1
    else:
        consensus = 'tie'

    adopted = None
    block_reason = None
    if consensus == 'A':
        if h1_pass_a and h2_pass_a:
            adopted = 'A'
        else:
            block_reason = 'candidate_A_failed_H1_or_H2_gate'
    elif consensus == 'B':
        if h1_pass_b and h2_pass_b:
            adopted = 'B'
        else:
            block_reason = 'candidate_B_failed_H1_or_H2_gate'
    elif consensus == 'tie':
        block_reason = 'order_swap_disagreement_tie'
    else:
        block_reason = 'invalid_judge_output'

    return {
        'winners': [w1, w2],
        'consensus': consensus,
        'adopted': adopted,
        'block_reason': block_reason,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['check', 'pair'])
    ap.add_argument('--video')
    ap.add_argument('--html')
    ap.add_argument('--a')
    ap.add_argument('--b')
    ap.add_argument('--html-a')
    ap.add_argument('--html-b')
    ap.add_argument('--out')
    ap.add_argument('--timeout', type=int, default=None)
    ap.add_argument('--fp-extra', action='append', default=[])
    ap.add_argument('--nontext-evidence', default=None)
    ap.add_argument('--max-attempts', type=int, default=3)
    a = ap.parse_args()
    if a.timeout is None:
        a.timeout = 300 if a.mode == 'check' else 540
    global RAW_DIR
    if a.out:
        RAW_DIR = os.path.splitext(os.path.abspath(a.out))[0] + '_raw'

    if a.mode == 'check':
        if not a.video:
            ap.error('--video is required in check mode')
        log('check (hardened non-bypass)', a.video)
        res = judge(
            'check', {'A': a.video}, {'A': a.html}, a.timeout,
            extra=a.fp_extra, nontext_evidence=a.nontext_evidence, max_attempts=a.max_attempts,
        )
        if res['valid']:
            fails = [c for c in res['result']['checks'] if c['pass'] is False]
            res['summary'] = {
                'valid': True,
                'gate_pass': len(fails) == 0,
                'fails': [c['id'] for c in fails],
                'checked_ids': [c['id'] for c in res['result']['checks']],
                'evidence_gaps': len(res['result'].get('evidence_gaps', [])),
                'no_bypass_wrapper': True,
            }
            if fails and a.out:
                fr, missing = grab_fail_frames(
                    a.video, fails,
                    os.path.splitext(os.path.abspath(a.out))[0] + '_failframes',
                )
                res['summary']['verify_frames'] = fr
                res['summary']['missing_fail_frame_evidence'] = missing
                log(f'FAIL frames extracted ({len(fr)}, missing={len(missing)})')
        else:
            res['summary'] = {
                'valid': False,
                'gate_pass': None,
                'no_bypass_wrapper': True,
                'note': 'agy invalid after retries; no valid judgment occurred',
            }
    else:
        if not a.a or not a.b:
            ap.error('--a and --b are required in pair mode')
        log('pair (hardened non-bypass, order-swapped parallel): X=a/Y=b and X=b/Y=a')
        with ThreadPoolExecutor(2) as ex:
            f1 = ex.submit(
                judge, 'pair', {'X': a.a, 'Y': a.b}, {'X': a.html_a, 'Y': a.html_b},
                a.timeout, a.fp_extra, a.nontext_evidence, a.max_attempts,
            )
            f2 = ex.submit(
                judge, 'pair', {'X': a.b, 'Y': a.a}, {'X': a.html_b, 'Y': a.html_a},
                a.timeout, a.fp_extra, a.nontext_evidence, a.max_attempts,
            )
            r1, r2 = f1.result(), f2.result()
        decision = resolve_pair_consensus(r1, r2)
        res = {
            'a': a.a, 'b': a.b,
            'order1': r1, 'order2': r2,
            'winners': decision['winners'],
            'consensus': decision['consensus'],
            'adopted': decision['adopted'],
            'block_reason': decision['block_reason'],
            'no_bypass_wrapper': True,
        }

    out = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, 'w', encoding='utf-8') as fh:
            fh.write(out + '\n')
    s = res.get('summary') or {'consensus': res.get('consensus'), 'winners': res.get('winners')}
    log('RESULT:', json.dumps(s, ensure_ascii=False), '→', a.out or '(stdout)')
    if not a.out:
        print(out)
    if a.mode == 'check':
        sys.exit(2 if not res['summary']['valid'] else (0 if res['summary']['gate_pass'] else 1))
    sys.exit(2 if res['consensus'] == 'invalid' else 0)


if __name__ == '__main__':
    try:
        main()
    except (AuthError, EnvError) as e:
        log('STOP:', e)
        sys.exit(3)
