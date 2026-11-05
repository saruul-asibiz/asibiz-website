#!/usr/bin/env python3
"""Get the latest Instagram posts for the website. Runs inside GitHub Actions.

What it does
  1. Reads the Instagram access key (the IG_TOKEN secret, or the encrypted copy in data/token.enc).
  2. Once a week, swaps the key for a fresh one (keys expire after 60 days) and saves it encrypted.
  3. Fetches the latest posts, saves each cover image to assets/ig/<id>.jpg,
     and writes data/instagram.json, which the website reads.

If anything goes wrong it prints a warning and stops quietly, so the website still
publishes (the Interviews and Latest posts blocks then show their placeholder cards).

Two kinds of key work:
  * a Facebook Page key (starts with "EAA"; from "API setup with Facebook login"). It never expires.
    The Instagram account must be linked to that Facebook Page.
  * an Instagram key (from "API setup with Instagram login", older apps only). It is renewed weekly.

Secrets (GitHub repo > Settings > Secrets and variables > Actions)
  IG_TOKEN   the access key. Paste a new one to switch accounts.
  TOKEN_KEY  any long password; used only to encrypt data/token.enc. Never change it afterwards.
"""
import datetime, hashlib, io, json, os, re, subprocess, sys, urllib.error, urllib.parse, urllib.request

API = os.environ.get('IG_API', 'https://graph.instagram.com')
FB_API = os.environ.get('FB_API', 'https://graph.facebook.com')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
IMG_DIR = os.path.join(ROOT, 'assets', 'ig')
TOKEN_FILE = os.path.join(DATA, 'token.enc')
REFRESH_EVERY_DAYS = 7
MAX_POSTS = 60
FIELDS = 'id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url,thumbnail_url}'


def warn(msg):
    print(f'::warning::{msg}')


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def get(url, params=None):
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'asibiz-website-build'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def openssl(extra, data, key):
    cmd = ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-salt', '-a', '-A', '-pass', 'env:TOKEN_KEY'] + extra
    return subprocess.run(cmd, input=data, capture_output=True, check=True, env=dict(os.environ, TOKEN_KEY=key)).stdout


def load_saved(key):
    if not (key and os.path.exists(TOKEN_FILE)):
        return None
    try:
        return json.loads(openssl(['-d'], open(TOKEN_FILE, 'rb').read().strip(), key))
    except Exception:
        warn('Could not open data/token.enc. Was TOKEN_KEY changed? Using the IG_TOKEN secret instead.')
        return None


def save(state, key):
    if not key:
        warn('TOKEN_KEY secret is missing, so the refreshed Instagram key was not saved. Add TOKEN_KEY.')
        return
    os.makedirs(DATA, exist_ok=True)
    with open(TOKEN_FILE, 'wb') as f:
        f.write(openssl([], json.dumps(state).encode(), key) + b'\n')


def clean_lines(caption):
    """Caption without #hashtags, one entry per non-empty line."""
    out = []
    for line in caption.splitlines():
        line = re.sub(r'(^|\s)#[^\s#]+', ' ', line).strip()
        if line:
            out.append(line)
    return '\n'.join(out)


def save_image(url, path):
    raw = get(url)
    try:  # shrink to 720px on the long side when Pillow is available
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        im.thumbnail((720, 720))
        im.save(path, 'JPEG', quality=82, optimize=True, progressive=True)
    except ImportError:
        open(path, 'wb').write(raw)


def main():
    key = os.environ.get('TOKEN_KEY', '').strip()
    secret = os.environ.get('IG_TOKEN', '').strip()
    seed = hashlib.sha256(secret.encode()).hexdigest() if secret else None

    state, changed = load_saved(key), False
    if seed and (not state or state.get('seed') != seed):
        # first run, or a new key was pasted into IG_TOKEN (e.g. switching to the official account)
        state, changed = {'token': secret, 'refreshed': now().isoformat(), 'seed': seed}, True
    if not state or not state.get('token'):
        warn('No Instagram key yet (add the IG_TOKEN secret). The site keeps its placeholder cards.')
        return

    facebook = state['token'].startswith('EAA')
    last = state.get('refreshed')
    age = (now() - datetime.datetime.fromisoformat(last)).days if last else 999
    if facebook and age >= REFRESH_EVERY_DAYS:
        # Page keys don't expire. Re-saving weekly still makes a small commit, which keeps
        # GitHub from pausing the daily schedule (it pauses after 60 days without activity).
        state['refreshed'] = now().isoformat()
        changed = True
    elif age >= REFRESH_EVERY_DAYS:
        try:
            r = json.loads(get(API + '/refresh_access_token', {'grant_type': 'ig_refresh_token', 'access_token': state['token']}))
            state.update(token=r['access_token'], refreshed=now().isoformat())
            changed = True
            print('Instagram key refreshed.')
        except Exception as e:
            warn(f'Could not refresh the Instagram key ({e}). Will try again on the next run.')
    if changed:
        save(state, key)

    try:
        if facebook:
            page = json.loads(get(FB_API + '/me', {'fields': 'instagram_business_account', 'access_token': state['token']}))
            ig = (page.get('instagram_business_account') or {}).get('id')
            if not ig:
                warn('This Facebook Page has no linked Instagram account. Link the Instagram account to the Page.')
                return
            media = json.loads(get(FB_API + f'/{ig}/media', {'fields': FIELDS, 'limit': MAX_POSTS, 'access_token': state['token']}))
        else:
            media = json.loads(get(API + '/me/media', {'fields': FIELDS, 'limit': MAX_POSTS, 'access_token': state['token']}))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        warn(f'Instagram refused the request ({e.code}): {detail}. If the key expired, paste a new one into IG_TOKEN.')
        return
    except Exception as e:
        warn(f'Could not reach Instagram ({e}).')
        return

    os.makedirs(IMG_DIR, exist_ok=True)
    posts = []
    for m in media.get('data', []):
        pid, link, kind = str(m.get('id', '')), m.get('permalink', ''), m.get('media_type')
        if not re.fullmatch(r'\d+', pid) or not link.startswith('https://www.instagram.com/'):
            continue
        cover = m.get('thumbnail_url') if kind == 'VIDEO' else m.get('media_url')
        if not cover and kind == 'CAROUSEL_ALBUM':
            first = ((m.get('children') or {}).get('data') or [{}])[0]
            cover = first.get('thumbnail_url') or first.get('media_url')
        image = None
        if cover:
            try:
                save_image(cover, os.path.join(IMG_DIR, pid + '.jpg'))
                image = f'assets/ig/{pid}.jpg'
            except Exception as e:
                warn(f'Could not download the cover of post {pid} ({e}).')
        caption = m.get('caption') or ''
        posts.append({
            'id': pid,
            'permalink': link,
            'mediaType': kind,
            'isReel': m.get('media_product_type') == 'REELS',
            'timestamp': m.get('timestamp'),
            'prunedCaption': clean_lines(caption),
            'hashtags': [t.lower() for t in re.findall(r'#([^\s#]+)', caption)],
            'image': image,
        })

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, 'instagram.json'), 'w', encoding='utf-8') as f:
        json.dump({'updated': now().isoformat(), 'posts': posts}, f, ensure_ascii=False, indent=1)
    print(f'Saved {len(posts)} Instagram posts.')


if __name__ == '__main__':
    main()
