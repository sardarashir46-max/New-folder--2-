"""Small, server-side YouTube Data API v3 client. It retrieves metadata only."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen


API_ROOT = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(Exception):
    pass


def _api_get(endpoint, params):
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise YouTubeAPIError("YouTube API is not configured. Set YOUTUBE_API_KEY in backend/.env.")
    query = urlencode({**params, "key": key})
    request = Request(f"{API_ROOT}/{endpoint}?{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise YouTubeAPIError("YouTube could not find that channel or video.") from exc
        if exc.code == 403:
            raise YouTubeAPIError("YouTube API access was denied. Check API enablement, key restrictions and quota.") from exc
        raise YouTubeAPIError("YouTube API request failed. Please try again later.") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise YouTubeAPIError("Could not reach YouTube. Please try again.") from exc


def _channel_lookup(value):
    value = value.strip()
    if re.fullmatch(r"UC[a-zA-Z0-9_-]{22}", value):
        return {"id": value}
    parsed = urlparse(value if "://" in value else f"https://www.youtube.com/{value.lstrip('/')}")
    if parsed.netloc and not (parsed.netloc.endswith("youtube.com") or parsed.netloc.endswith("youtu.be")):
        raise YouTubeAPIError("Enter a YouTube channel URL, @handle, or channel ID.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel" and re.fullmatch(r"UC[a-zA-Z0-9_-]{22}", parts[1]):
        return {"id": parts[1]}
    handle = next((part for part in parts if part.startswith("@")), None)
    if handle:
        return {"forHandle": handle}
    if value.startswith("@"):
        return {"forHandle": value}
    raise YouTubeAPIError("Use a channel URL containing /channel/UC…, an @handle, or a channel ID.")


def get_channel(value):
    data = _api_get("channels", {"part": "snippet,statistics,contentDetails", **_channel_lookup(value)})
    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError("No public YouTube channel was found for that input.")
    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {}).get("relatedPlaylists", {})
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
    return {
        "channel_id": item["id"],
        "title": snippet.get("title", "Untitled channel"),
        "description": snippet.get("description", ""),
        "custom_url": snippet.get("customUrl", ""),
        "thumbnail_url": thumbnail,
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "uploads_playlist_id": details.get("uploads", ""),
    }


def get_latest_videos(uploads_playlist_id, max_results=10):
    if not uploads_playlist_id:
        return []
    data = _api_get("playlistItems", {
        "part": "snippet,contentDetails",
        "playlistId": uploads_playlist_id,
        "maxResults": min(max(int(max_results), 1), 50),
    })
    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item.get("contentDetails", {}).get("videoId") or snippet.get("resourceId", {}).get("videoId")
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
        videos.append({
            "video_id": video_id,
            "title": snippet.get("title", "Untitled video"),
            "description": snippet.get("description", ""),
            "thumbnail_url": thumbnail,
            "published_at": snippet.get("publishedAt"),
            "video_url": f"https://www.youtube.com/watch?v={video_id}",
        })
    return videos
