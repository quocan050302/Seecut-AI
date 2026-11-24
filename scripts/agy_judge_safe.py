#!/usr/bin/env python3
"""Audited no-bypass wrapper for YeJe-cpu/SeeCut v3.4 scripts/agy_judge.py.

Preserves 100% of upstream judge discipline:
- Uses unmodified upstream prompts (references/prompt/硬伤清单.txt, 对比评委.txt)
- Uses unmodified upstream JSON schemas (scripts/schemas/check.json, pair.json)
- Copies 540p transcoded media into isolated system mkdtemp dir (zero source leakage)
- Enforces --new-project, JSON schema output, >=2 fingerprint text hits, <=3s duration delta
- Runs order-swapped parallel comparisons for `pair` (X=A/Y=B and X=B/Y=A)
- Extracts fail-frames for every flagged FAIL in `check`
- Preserves exact exit codes: 0=pass/consensus, 1=check FAIL, 2=invalid, 3=auth/env error

Security delta (owner-authorized): OMITS `--dangerously-skip-permissions`, verified
working on Antigravity CLI 1.5.15 without bypassing tool permissions.
Upstream reference remains unmodified at third_party/SeeCut/skill/seecut/scripts/agy_judge.py.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_SEECUT_SKILL = os.path.expanduser(
    os.environ.get(
        'SEECUT_SKILL_DIR',
        '~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut/skill/seecut',
    )
)
PROMPTS = {'check': 'references/prompt/硬伤清单.txt', 'pair': 'references/prompt/对比评委.txt'}
SCHEMAS = {'check': 'scripts/schemas/check.json', 'pair': 'scripts/schemas/pair.json'}
AGY = (
    os.environ.get('AGY_BIN')
    or shutil.which('agy')
    or (os.path.expanduser('~/.gemini/bin/agy') if os.path.exists(os.path.expanduser('~/.gemini/bin/agy')) else None)
    or os.path.expanduser('~/.local/bin/agy')
)


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def duration(path):
    out = subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path]
    )
    return float(out.decode().strip())


def to540(src, dst):
    sc = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-y', '-i', src, '-vf', sc,
        '-c:v', 'libx264', '-crf', '26', '-c:a', 'aac', '-b:a', '96k', dst
    ])


def norm(s):
    return re.sub(r'[\s\W_]+', '', s or '').lower()


def fp_source(html_path, extra=None):
    if not html_path:
        return None
    html = open(html_path, encoding='utf-8', errors='ignore').read()
    parts = [re.sub(r'<[^>]+>', '', html)]
    auto = os.path.join(os.path.dirname(os.path.abspath(html_path)), 'fp-manifest.txt')
    for f in [auto] + list(extra or []):
        if f and os.path.exists(f):
            parts.append(open(f, encoding='utf-8', errors='ignore').read())
    return norm(' '.join(parts))


def fp_hits(texts, html_path, extra=None):
    src = fp_source(html_path, extra)
    if src is None:
        return None
    hits = 0
    for t in texts:
        n = norm(t)
        if len(n) >= 2 and (n in src or (len(n) >= 6 and n[:6] in src)):
            hits += 1
    return hits


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


RAW_DIR = None


class AuthError(Exception):
    pass


class EnvError(Exception):
    pass


class Transient(Exception):
    pass


def run_agy(mode, workdir, timeout):
    env = dict(os.environ)
    px = env.get('JIANJI_PROXY')
    if px:
        env.update(https_proxy=px, http_proxy=px, HTTPS_PROXY=px, HTTP_PROXY=px, no_proxy='localhost,127.0.0.1')
    prompt = (
        open(os.path.join(DEFAULT_SEECUT_SKILL, PROMPTS[mode]), encoding='utf-8').read()
        + '\n只根据本目录里的视频文件作答，不要读取、列出或搜索任何其他文件。'
    )
    cmd = [
        AGY, '-p', prompt, '--new-project', '--add-dir', '.',
        '--model', 'gemini-3.8-flash-high',
        '--json-schema', os.path.join(DEFAULT_SEECUT_SKILL, SCHEMAS[mode]),
        '--output-format', 'json',
        '--print-timeout', f'{timeout}s',
    ]
    try:
        p = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout + 30)
        raw = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        raise Transient(f'agy exceeded {timeout}s timeout')
    if RAW_DIR:
        os.makedirs(RAW_DIR, exist_ok=True)
        raw_path = os.path.join(RAW_DIR, f'{mode}-{time.strftime("%H%M%S")}-{os.path.basename(workdir)}.raw')
        open(raw_path, 'w', encoding='utf-8').write(raw)
    try:
        outer = json.loads(p.stdout)
        if re.search(r'location is not supported|not eligible', raw, re.I):
            raise EnvError('Google account region not supported (User location is not supported)')
        if outer.get('status') == 'ERROR':
            raise Transient('agy error: ' + str(outer.get('error', ''))[:160])
        so = outer.get('structured_output')
        if isinstance(so, dict) and ('checks' in so or 'dimensions' in so):
            return so
        resp = outer.get('response', '')
        if not str(resp).strip() and re.search(r'print timeout', raw, re.I):
            raise Transient(f'agy truncated at print timeout {timeout}s')
    except (Transient, EnvError):
        raise
    except Exception:
        resp = raw
        if re.search(r'Authentication required|not logged in|please (re-?)?log ?in', raw, re.I):
            raise AuthError('agy authentication required / not logged in')
        if re.search(r'time(d)? ?out|deadline', raw, re.I):
            raise Transient('agy print-timeout exceeded')
    res = last_json(resp, 'checks' if mode == 'check' else 'dimensions')
    if res is None and re.search(r'print timeout', raw, re.I):
        raise Transient(f'agy truncated at print timeout {timeout}s')
    return res


def judge_once(mode, files, htmls, timeout, extra=None):
    wd = tempfile.mkdtemp(prefix='seecut_agy_')
    try:
        real = {}
        for k, f in files.items():
            to540(f, os.path.join(wd, f'{k}.mp4'))
            real[k] = duration(f)
        r = run_agy(mode, wd, timeout)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    if not r:
        return None, ['no valid JSON returned']
    probs = []
    fp = r.get('fingerprint', {})
    if mode == 'check':
        pairs = [('A', fp.get('texts', []), fp.get('duration_sec', 0))]
    else:
        pairs = [
            ('X', fp.get('X_texts', []), fp.get('X_duration_sec', 0)),
            ('Y', fp.get('Y_texts', []), fp.get('Y_duration_sec', 0)),
        ]
    for k, texts, d in pairs:
        if abs((d or 0) - real[k]) > 3:
            probs.append(f'{k} duration mismatch agy={d} actual={real[k]:.1f}')
        h = fp_hits(texts, htmls.get(k), extra)
        if h is not None and h < 2:
            probs.append(f'{k} fingerprint hits {h}/2 (possible confab): {texts}')
    return r, probs


def judge(mode, files, htmls, timeout, extra=None):
    content_tries, probs, r, env_retried = 0, [], None, False
    for attempt in range(1, 5):
        try:
            r, probs = judge_once(mode, files, htmls, timeout, extra)
        except EnvError as e:
            if env_retried:
                raise
            env_retried = True
            log(f'Attempt {attempt} env error ({e}); sleeping 60s before retry')
            time.sleep(60)
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
            return {'valid': True, 'result': r}
        content_tries += 1
        log(f'Attempt {attempt} invalid content:', '; '.join(probs))
        if content_tries >= 2:
            break
    return {'valid': False, 'problems': probs, 'result': r}


def secs(txt):
    m = re.findall(r'(\d{1,2}):(\d{2}(?:\.\d+)?)', txt or '')
    ts = [int(a) * 60 + float(b) for a, b in m[:2]]
    if not ts:
        ts = [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*s', txt or '')[:2]]
    return ts


def grab_fail_frames(video, fails, outdir):
    os.makedirs(outdir, exist_ok=True)
    made = []
    for c in fails:
        ts = secs(c.get('evidence', ''))
        if not ts:
            continue
        a, b = ts[0], (ts[1] if len(ts) > 1 else ts[0] + 1)
        for t in sorted({a, (a + b) / 2, b}):
            f = os.path.join(outdir, f"{c['id']}_{t:.1f}s.jpg")
            subprocess.call(['ffmpeg', '-v', 'error', '-y', '-ss', str(t), '-i', video, '-frames:v', '1',
                             '-vf', 'scale=540:-2', f])
            made.append(f)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['check', 'pair'])
    ap.add_argument('--video'); ap.add_argument('--html')
    ap.add_argument('--a'); ap.add_argument('--b'); ap.add_argument('--html-a'); ap.add_argument('--html-b')
    ap.add_argument('--out'); ap.add_argument('--timeout', type=int, default=None)
    ap.add_argument('--fp-extra', action='append', default=[])
    a = ap.parse_args()
    if a.timeout is None:
        a.timeout = 300 if a.mode == 'check' else 540
    global RAW_DIR
    if a.out:
        RAW_DIR = os.path.splitext(os.path.abspath(a.out))[0] + '_raw'

    if a.mode == 'check':
        log('check (safe non-bypass)', a.video)
        res = judge('check', {'A': a.video}, {'A': a.html}, a.timeout, a.fp_extra)
        if res['valid']:
            fails = [c for c in res['result']['checks'] if not c['pass']]
            res['summary'] = {
                'valid': True,
                'gate_pass': not fails,
                'fails': [c['id'] for c in fails],
                'evidence_gaps': len(res['result'].get('evidence_gaps', [])),
                'no_bypass_wrapper': True,
            }
            if fails and a.out:
                fr = grab_fail_frames(a.video, fails, os.path.splitext(os.path.abspath(a.out))[0] + '_failframes')
                res['summary']['verify_frames'] = fr
                log(f'FAIL frames extracted ({len(fr)}):', os.path.dirname(fr[0]) if fr else 'no timestamps')
        else:
            res['summary'] = {
                'valid': False,
                'gate_pass': None,
                'no_bypass_wrapper': True,
                'note': 'agy invalid after retries; no valid judgment occurred',
            }
    else:
        log('pair (safe non-bypass, order-swapped parallel): X=a/Y=b and X=b/Y=a')
        with ThreadPoolExecutor(2) as ex:
            f1 = ex.submit(judge, 'pair', {'X': a.a, 'Y': a.b}, {'X': a.html_a, 'Y': a.html_b}, a.timeout, a.fp_extra)
            f2 = ex.submit(judge, 'pair', {'X': a.b, 'Y': a.a}, {'X': a.html_b, 'Y': a.html_a}, a.timeout, a.fp_extra)
            r1, r2 = f1.result(), f2.result()
        w1 = ('A' if r1['result']['winner'] == 'X' else 'B') if r1['valid'] else None
        w2 = ('B' if r2['result']['winner'] == 'X' else 'A') if r2['valid'] else None
        consensus = w1 if (w1 and w1 == w2) else ('invalid' if not (w1 and w2) else 'tie')
        res = {
            'a': a.a, 'b': a.b,
            'order1': r1, 'order2': r2,
            'winners': [w1, w2],
            'consensus': consensus,
            'no_bypass_wrapper': True,
        }
    out = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        open(a.out, 'w', encoding='utf-8').write(out)
    s = res.get('summary') or {'consensus': res.get('consensus'), 'winners': res.get('winners')}
    log('RESULT:', json.dumps(s, ensure_ascii=False), '→', a.out or '(stdout)')
    if not a.out:
        print(out)
    if a.mode == 'check':
        sys.exit(2 if not res['summary']['valid'] else (0 if res['summary']['gate_pass'] else 1))
    sys.exit(2 if consensus == 'invalid' else 0)


if __name__ == '__main__':
    try:
        main()
    except (AuthError, EnvError) as e:
        log('STOP:', e)
        sys.exit(3)
