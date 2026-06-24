"""Pull a user's X (Twitter) posts + images via API v2.

Token from ``data/research/x_token.txt`` or ``$X_BEARER_TOKEN``.

Usage: ``python -m research.x_pull Adaamset [max_tweets]``

Saves: ``data/research/<user>/tweets.json``, ``tweets.md``, ``images/<id>-<n>.<ext>``
Cost: ~$0.005 per post read (pay-per-use model). Prints an estimate.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from research.bars import RESEARCH_DATA_DIR

BASE = "https://api.x.com/2"
DEFAULT_TOKEN_PATH = RESEARCH_DATA_DIR / "x_token.txt"


def get_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    if token_path.exists():
        for line in token_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    env_token = os.environ.get("X_BEARER_TOKEN")
    if env_token:
        return env_token.strip()
    sys.exit(
        "No token. Put your bearer token in data/research/x_token.txt "
        "(gitignored) or set $X_BEARER_TOKEN."
    )


def merge_tweets(existing: list[dict], fetched: list[dict]) -> list[dict]:
    """Union tweets by ``id``; fetched rows win on conflict, sorted by ``created_at``."""
    by_id = {tweet["id"]: tweet for tweet in existing}
    for tweet in fetched:
        by_id[tweet["id"]] = tweet
    return sorted(by_id.values(), key=lambda item: item.get("created_at", ""))


def _api_get(
    client: httpx.Client,
    path: str,
    *,
    params: dict | None = None,
) -> dict:
    for _attempt in range(6):
        response = client.get(f"{BASE}{path}", params=params)
        if response.status_code == 429:
            reset = int(response.headers.get("x-rate-limit-reset", 0))
            wait = max(reset - int(time.time()), 15)
            print(f"  rate-limited, waiting {wait}s...")
            time.sleep(min(wait, 900))
            continue
        response.raise_for_status()
        return response.json()
    sys.exit("too many retries")


def fetch_user_tweets(
    username: str,
    max_tweets: int,
    *,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Fetch up to ``max_tweets`` for ``username``; return tweets (with ``_media``) + media map."""
    bearer = token or get_token()
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=30.0,
        )

    try:
        user = _api_get(client, f"/users/by/username/{username}")
        uid = user["data"]["id"]
        print(f"@{username} -> id {uid}")

        tweets: list[dict] = []
        media_by_key: dict[str, dict] = {}
        pagination_token: str | None = None
        pulled = 0

        while pulled < max_tweets:
            params: dict[str, str | int] = {
                "max_results": 100,
                "tweet.fields": "created_at,public_metrics,referenced_tweets,conversation_id",
                "expansions": "attachments.media_keys",
                "media.fields": "url,preview_image_url,type,variants,alt_text",
            }
            if pagination_token:
                params["pagination_token"] = pagination_token
            page = _api_get(client, f"/users/{uid}/tweets", params=params)
            batch = page.get("data", [])
            if not batch:
                break
            tweets.extend(batch)
            pulled += len(batch)
            for media in page.get("includes", {}).get("media", []):
                media_by_key[media["media_key"]] = media
            print(f"  pulled {pulled} tweets...")
            pagination_token = page.get("meta", {}).get("next_token")
            if not pagination_token:
                break

        for tweet in tweets:
            keys = tweet.get("attachments", {}).get("media_keys", [])
            tweet["_media"] = [media_by_key.get(key, {}) for key in keys]

        return tweets, media_by_key
    finally:
        if owns_client:
            client.close()


def _image_ext(media: dict) -> str:
    url = media.get("url") or media.get("preview_image_url") or ""
    return os.path.splitext(urlparse(url).path)[1] or ".jpg"


def save_tweets(
    user_dir: Path,
    tweets: list[dict],
    media_map: dict[str, dict] | None = None,
    *,
    download_client: httpx.Client | None = None,
) -> list[dict]:
    """Merge ``tweets`` into ``user_dir/tweets.json``, write markdown, download images."""
    user_dir.mkdir(parents=True, exist_ok=True)
    imgdir = user_dir / "images"
    imgdir.mkdir(parents=True, exist_ok=True)

    tweets_path = user_dir / "tweets.json"
    existing: list[dict] = []
    if tweets_path.exists():
        existing = json.loads(tweets_path.read_text(encoding="utf-8"))

    merged = merge_tweets(existing, tweets)

    owns_client = download_client is None
    if download_client is None:
        download_client = httpx.Client(timeout=30.0)

    n_img = 0
    try:
        for tweet in merged:
            if media_map is not None and "_media" not in tweet:
                keys = tweet.get("attachments", {}).get("media_keys", [])
                tweet["_media"] = [media_map.get(key, {}) for key in keys]
            for index, media in enumerate(tweet.get("_media", [])):
                if not media:
                    continue
                url = media.get("url") or media.get("preview_image_url")
                if not url:
                    continue
                ext = _image_ext(media)
                dest = imgdir / f"{tweet['id']}-{index}{ext}"
                if dest.exists():
                    continue
                try:
                    dest.write_bytes(download_client.get(url, timeout=30.0).content)
                    n_img += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  img fail {url}: {exc}")
    finally:
        if owns_client:
            download_client.close()

    tweets_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    with (user_dir / "tweets.md").open("w", encoding="utf-8") as handle:
        for tweet in merged:
            handle.write(
                f"### {tweet.get('created_at', '')}  ·  {tweet['id']}\n\n"
                f"{tweet.get('text', '')}\n\n"
            )
            for index, media in enumerate(tweet.get("_media", [])):
                if media:
                    ext = _image_ext(media)
                    handle.write(
                        f"![{media.get('type', 'img')}](images/{tweet['id']}-{index}{ext})\n\n"
                    )
            handle.write("---\n\n")

    print(f"\nDONE: {len(merged)} tweets ({len(tweets)} fetched), {n_img} images -> {user_dir}")
    print(f"est. read cost: {len(tweets)} x $0.005 = ${len(tweets) * 0.005:.2f}")
    return merged


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    username = args[0] if args else "Adaamset"
    max_tweets = int(args[1]) if len(args) > 1 else 3200
    user_dir = RESEARCH_DATA_DIR / username.lower()
    fetched, media_map = fetch_user_tweets(username, max_tweets)
    save_tweets(user_dir, fetched, media_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
