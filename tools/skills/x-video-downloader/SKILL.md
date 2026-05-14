---
name: x-video-downloader
description: Download videos from X/Twitter posts. Use this skill whenever the user wants to download, save, or grab a video from an X/Twitter tweet, or when they share an x.com or twitter.com link that contains a video. Also use when the user asks to save Twitter/X media, extract video from a tweet, or mentions wanting a local copy of a video they saw on X/Twitter.
---

# X/Twitter Video Downloader

Download videos from X/Twitter posts by extracting the video URL through third-party APIs and saving the file locally.

## Workflow

### Step 1: Parse the tweet URL

Extract the username and status ID from the X/Twitter URL. Supported URL formats:

- `https://x.com/<username>/status/<id>`
- `https://twitter.com/<username>/status/<id>`
- `https://x.com/<username>/status/<id>?s=20` (with query params)
- Mobile variants, etc.

The key parts are `<username>` and `<id>` (the numeric status ID).

### Step 2: Get tweet metadata and video URL via vxtwitter API

Call the vxtwitter API to get the tweet data including the video direct link:

```
GET https://api.vxtwitter.com/<username>/status/<id>
```

This returns a JSON object. The important fields:

- `text` — tweet text content
- `media_extended` — array of media objects, each with:
  - `type` — "video" or "photo"
  - `url` — direct media URL (for videos, this is the MP4 link)
  - `size` — `{width, height}`
  - `duration_millis` — video duration in milliseconds
  - `thumbnail_url` — video thumbnail
- `likes`, `retweets`, `replies` — engagement stats
- `date` — post date
- `user_name`, `user_screen_name` — author info

If `media_extended` contains an entry with `type: "video"`, use its `url` as the download link.

If the vxtwitter API fails (returns error or empty), try these fallbacks in order:

1. **fxtwitter**: `GET https://api.fxtwitter.com/<username>/status/<id>` — same structure, but may be blocked by Cloudflare.
2. **twittpr.com**: Fetch `https://twittpr.com/<username>/status/<id>` and parse the HTML meta tags — look for `og:video` or `twitter:player:stream` meta tags for the video URL.

### Step 3: Download the video

Use Python `urllib.request` to download the video file. Set appropriate headers to avoid being blocked:

```python
import urllib.request

req = urllib.request.Request(video_url, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://x.com/"
})

with urllib.request.urlopen(req, timeout=120) as response:
    with open(output_path, "wb") as f:
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            f.write(chunk)
```

### Step 4: Save and report

- Save the video to the current working directory with filename: `<username>_<status_id>.mp4`
- Report to the user:
  - File path
  - File size (in MB)
  - Resolution (from the API response)
  - Duration (from the API response)
  - Tweet text content

## Error handling

- **No video found**: If the tweet has no video (only photos or text), tell the user this tweet doesn't contain a video.
- **API returns "doesn't exist"**: The tweet ID may be wrong. Double-check the URL. Common issue: the status ID may be truncated or have extra digits when copy-pasted.
- **Cloudflare block on fxtwitter**: Fall back to vxtwitter API or twittpr.com HTML parsing.
- **Download timeout**: For large videos, increase the timeout. Report partial downloads clearly.
- **Private/deleted tweet**: Tell the user the tweet may be private, deleted, or from a suspended account.

## Tips

- The vxtwitter API (`api.vxtwitter.com`) is generally the most reliable and doesn't require authentication.
- Video URLs from Twitter's CDN (`video.twimg.com`) are temporary and may expire — download promptly.
- Some tweets have multiple videos or a mix of photos and videos. Check all entries in `media_extended`.
- If the highest resolution video URL fails, try removing the `?tag=27` query parameter from the URL.
