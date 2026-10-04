"""Build a private podcast feed from the audio files in the episodes/ folder.

You don't run this yourself: GitHub runs it automatically every time you push
new files (see .github/workflows/publish.yml). It writes a ready-to-publish
website into the _site/ folder: your audio files, the cover art, feed.xml
(the podcast feed Apple Podcasts subscribes to) and a small index page.

Settings like the show title live in podcast.json.
"""

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
EPISODES = ROOT / "episodes"
AUDIO_TYPES = {".m4a": "audio/x-m4a", ".mp3": "audio/mpeg"}
GITHUB_FILE_LIMIT = 100 * 1024 * 1024


def site_url(cli_url):
    """Work out the public address of the site (https://<user>.github.io/<repo>/)."""
    if cli_url:
        return cli_url.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY")  # set automatically inside GitHub Actions
    if repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return "http://localhost:8000/"


def added_date(path):
    """When the file was first committed to the repo (falls back to its modified time)."""
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--follow", "--format=%aI", "--", str(path)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        if out:
            return datetime.fromisoformat(out[-1])
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def duration(path):
    """Length of the audio as H:MM:SS, or None if it can't be read."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        seconds = int(round(audio.info.length))
    except Exception:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def nice_title(path):
    """Episode title from the file name: underscores become spaces, and so do
    hyphens when the name has no spaces (e.g. "lesson-2-notice" -> "lesson 2 notice")."""
    name = path.stem.replace("_", " ")
    if " " not in name:
        name = name.replace("-", " ")
    return " ".join(name.split())


def build(out_dir, base_url):
    cfg = json.loads((ROOT / "podcast.json").read_text(encoding="utf-8"))
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "episodes").mkdir(parents=True)

    files = [p for p in EPISODES.iterdir() if p.suffix.lower() in AUDIO_TYPES]
    skipped = [p.name for p in EPISODES.iterdir()
               if p.is_file() and p.suffix.lower() not in AUDIO_TYPES and p.name != ".gitkeep"]

    episodes = []
    for p in files:
        size = p.stat().st_size
        if size > GITHUB_FILE_LIMIT:
            print(f"WARNING: {p.name} is over 100 MB and GitHub will reject it.")
        shutil.copy2(p, out / "episodes" / p.name)
        episodes.append({
            "file": p.name,
            "title": nice_title(p),
            "size": size,
            "type": AUDIO_TYPES[p.suffix.lower()],
            "date": added_date(p),
            "duration": duration(p),
        })
    # Newest first; ties broken by file name so the order is stable.
    episodes.sort(key=lambda e: (e["date"], e["file"]), reverse=True)

    cover = None
    for name in ("cover.jpg", "cover.png"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, out / name)
            cover = base_url + name
            break

    items = []
    for e in episodes:
        url = base_url + "episodes/" + quote(e["file"])
        dur = f"\n      <itunes:duration>{e['duration']}</itunes:duration>" if e["duration"] else ""
        items.append(f"""    <item>
      <title>{escape(e['title'])}</title>
      <guid isPermaLink="false">{escape(e['file'])}</guid>
      <pubDate>{format_datetime(e['date'])}</pubDate>
      <enclosure url="{escape(url)}" length="{e['size']}" type="{e['type']}"/>{dur}
      <itunes:episodeType>full</itunes:episodeType>
    </item>""")

    image = f'\n    <itunes:image href="{escape(cover)}"/>' if cover else ""
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(cfg['title'])}</title>
    <link>{escape(base_url)}</link>
    <atom:link href="{escape(base_url)}feed.xml" rel="self" type="application/rss+xml"/>
    <description>{escape(cfg['description'])}</description>
    <language>{escape(cfg.get('language', 'en-us'))}</language>
    <itunes:author>{escape(cfg['author'])}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>{image}
    <itunes:category text="Education"/>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (out / "feed.xml").write_text(feed, encoding="utf-8")

    rows = "".join(f"<li>{escape(e['title'])}</li>" for e in episodes) or "<li>No episodes yet</li>"
    (out / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(cfg['title'])}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 16px;line-height:1.5}}
code{{background:#eee;padding:2px 6px;border-radius:4px;word-break:break-all}}</style></head>
<body><h1>{escape(cfg['title'])}</h1>
<p>Feed address for Apple Podcasts (Library &rarr; &hellip; &rarr; Follow a Show by URL):</p>
<p><code>{escape(base_url)}feed.xml</code></p>
<h2>{len(episodes)} episode(s)</h2><ul>{rows}</ul></body></html>
""", encoding="utf-8")

    print(f"Built feed with {len(episodes)} episode(s) at {base_url}feed.xml")
    for name in skipped:
        print(f"Skipped {name}: only .m4a and .mp3 files become episodes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="_site")
    parser.add_argument("--url", help="Override the site address (normally detected automatically)")
    args = parser.parse_args()
    build(args.out, site_url(args.url))
