#!/usr/bin/env python3
"""Turn content/interviews.txt and content/posts.txt into data/instagram.json for the website.

Runs inside GitHub Actions on every change. Cover pictures from content/images are resized
and copied to assets/ig/<number>.jpg (the number comes from the picture itself). Mistakes never stop the website from publishing:
a block with a problem is skipped and explained as a warning on the Actions run.
Interviews and posts are shown newest first automatically, so blocks can be added anywhere.
"""
import hashlib, json, os, re, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, 'content')
IMAGES = os.path.join(CONTENT, 'images')
OUT_IMG = os.path.join(ROOT, 'assets', 'ig')
OUT_JSON = os.path.join(ROOT, 'data', 'instagram.json')
TAG = 'asibizinterview'
LINK = re.compile(r'^https://(www\.)?instagram\.com/[A-Za-z0-9_./?=&%-]+$')
CODE = re.compile(r'instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)')
ABC = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'


def age(link):
    """Instagram's link code is the post's number, and newer posts have bigger numbers.
    So sorting by it puts the newest first, whatever order the blocks are written in."""
    m = CODE.search(link)
    if not m or len(m.group(1)) > 14:
        return -1  # unknown: goes to the end, in the order written
    n = 0
    for ch in m.group(1):
        n = n * 64 + ABC.index(ch)
    return n


def warn(msg):
    print(f'::warning::{msg}')


def blocks(name):
    """Read 'key: value' blocks separated by empty lines; '#' lines are notes."""
    path = os.path.join(CONTENT, name)
    if not os.path.exists(path):
        warn(f'content/{name} is missing.')
        return []
    out, cur, line_no, start = [], {}, 0, 0
    for raw in open(path, encoding='utf-8-sig').read().splitlines() + ['']:
        line_no += 1
        line = raw.strip()
        if line.startswith('#'):
            continue
        if not line:
            if cur:
                out.append((start, cur))
            cur = {}
            continue
        if not cur:
            start = line_no
        if ':' not in line:
            warn(f'content/{name} line {line_no}: "{line}" has no colon, so it was ignored.')
            continue
        k, v = line.split(':', 1)
        cur[k.strip().lower()] = v.strip()
    return out


def picture(name, where, long_side):
    if not name:
        return None
    if '/' in name or '\\' in name or not re.search(r'\.(jpe?g|png|webp)$', name, re.I):
        warn(f'{where}: "{name}" is not a picture file name (use e.g. nepal-4.jpg).')
        return None
    src = os.path.join(IMAGES, name)
    if not os.path.exists(src):
        warn(f'{where}: picture "{name}" was not found in content/images. Check the spelling, including capital letters.')
        return None
    # Name the copy after the picture's content, so a changed picture always gets a new name
    # and browsers never show an old cached copy.
    with open(src, 'rb') as fh:
        num = int(hashlib.sha256(fh.read() + str(long_side).encode()).hexdigest()[:13], 16)
    dst = os.path.join(OUT_IMG, f'{num}.jpg')
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(src)).convert('RGB')
        im.thumbnail((long_side, long_side))
        im.save(dst, 'JPEG', quality=82, optimize=True, progressive=True)
    except ImportError:
        shutil.copyfile(src, dst)
    except Exception as e:
        warn(f'{where}: could not read picture "{name}" ({e}).')
        return None
    return f'assets/ig/{num}.jpg'


def main():
    os.makedirs(OUT_IMG, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    posts = []
    for line, b in blocks('interviews.txt'):
        where = f'content/interviews.txt (block starting on line {line})'
        link = b.get('link', '')
        if not LINK.match(link):
            warn(f'{where}: the link must be an Instagram link starting with https://www.instagram.com/ . Block skipped.')
            continue
        if not b.get('title'):
            warn(f'{where}: add a "title:" line.')
        text = '\n'.join(x for x in [b.get('title', ''), b.get('subtitle', '')] if x)
        posts.append({'permalink': link, 'mediaType': 'VIDEO', 'isReel': True, 'hashtags': [TAG],
                      'prunedCaption': text, 'image': picture(b.get('image', ''), where, 900)})
    for line, b in blocks('posts.txt'):
        where = f'content/posts.txt (block starting on line {line})'
        link = b.get('link', '')
        if not LINK.match(link):
            warn(f'{where}: the link must be an Instagram link starting with https://www.instagram.com/ . Block skipped.')
            continue
        posts.append({'permalink': link, 'mediaType': 'IMAGE', 'hashtags': [],
                      'prunedCaption': b.get('text', ''), 'image': picture(b.get('image', ''), where, 700)})
    # Newest first, no matter where a block was pasted in the file.
    posts.sort(key=lambda p: (p['mediaType'] != 'VIDEO', -age(p['permalink'])))
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({'posts': posts}, f, ensure_ascii=False, indent=1)
    n_iv = sum(1 for p in posts if p['mediaType'] == 'VIDEO')
    print(f'Built {n_iv} interviews and {len(posts) - n_iv} posts.')


if __name__ == '__main__':
    main()
