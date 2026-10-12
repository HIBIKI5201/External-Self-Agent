"""Read-only YouTube library and search; playback uses the official IFrame player."""
import html
import re
import urllib.parse

import gapi
import gauth

API = "https://www.googleapis.com/youtube/v3"


class YouTubeError(Exception):
    def __init__(self, message, code=503, link=""):
        super().__init__(message)
        self.code = code
        self.link = link


_CONSOLE_HOSTS = ("console.developers.google.com", "console.cloud.google.com")


def enable_link(body):
    """The page that enables the YouTube Data API, taken from Google's error text (#86). Only an https link
    on Google's own console for the YouTube API is returned; anything else is dropped."""
    for raw in re.findall(r"https://[^\s\"'<>\\]+", body or ""):
        url = urllib.parse.urlsplit(raw.rstrip(".,)"))
        query = urllib.parse.parse_qs(url.query)
        if url.scheme != "https" or url.netloc not in _CONSOLE_HOSTS or url.username or url.port:
            continue
        if not re.fullmatch(r"/apis/(?:api|library)/youtube\.googleapis\.com(?:/overview)?", url.path):
            continue
        project = (query.get("project") or [""])[0]
        if project and not re.fullmatch(r"[A-Za-z0-9-]{1,40}", project):
            continue
        return urllib.parse.urlunsplit(("https", url.netloc, url.path,
                                        urllib.parse.urlencode({"project": project}) if project else "", ""))
    return ""


def status():
    auth = gauth.status()
    return {"client": auth["client"], "connected": auth["connected"] and auth.get("youtube", False)}


def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name}が不正です。")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name}を入力してください。")
    return value


def _request(path, params, page_token):
    page_token = _text(page_token, "ページ", 512)
    if not status()["connected"]:
        raise YouTubeError("「GoogleでYouTubeに接続」を押して、読み取りを許可してください。", 401)
    params = {**params, "maxResults": 20}
    if page_token:
        params["pageToken"] = page_token
    try:
        return gapi.request(API, "GET", path, params)
    except gauth.AuthError:
        raise YouTubeError("Googleへの接続が切れました。YouTubeに再接続してください。", 401) from None
    except gauth.OfflineError:
        raise YouTubeError("YouTubeに接続できません。ネットワークを確認して再試行してください。") from None
    except gapi.SyncError as e:
        if e.reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"):
            raise YouTubeError("YouTubeの利用上限に達しました。時間をおいて再試行してください。", 429) from None
        if e.reason in ("accessNotConfigured", "serviceDisabled"):
            # Signed in fine; the Google Cloud project has the API switched off. Reconnecting does not help.
            raise YouTubeError("Googleへのログインは済んでいます。Google Cloud のプロジェクトで YouTube Data API v3 が"
                               "有効になっていないため読み込めません（再接続では直りません）。下のリンクから API を有効にして、"
                               "数分おいてから開き直してください。", 403, link=enable_link(getattr(e, "body", ""))) from None
        if e.code == 403:
            raise YouTubeError("YouTubeの読み取りが許可されていません。再接続し、接続設定も確認してください。", 403) from None
        if e.code == 404:
            raise YouTubeError("このプレイリストは見つからないか、閲覧できません。", 404) from None
        if e.code == 400:
            raise YouTubeError("一覧を取得できませんでした。最初のページから開き直してください。", 400) from None
        raise YouTubeError("YouTubeの取得に失敗しました。時間をおいて再試行してください。") from None


def _page(data, items):
    return {"items": items, "next": data.get("nextPageToken", ""),
            "previous": data.get("prevPageToken", "")}


def playlists(page_token=""):
    data = _request("/playlists", {"part": "snippet,contentDetails", "mine": "true"}, page_token)
    items = [{"id": x["id"], "title": html.unescape(x.get("snippet", {}).get("title", "")),
              "count": x.get("contentDetails", {}).get("itemCount", 0)} for x in data.get("items", [])]
    return _page(data, items)


def _video(video_id, snippet):
    return {"id": video_id, "title": html.unescape(snippet.get("title", "")),
            "channel": html.unescape(snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle", "")),
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/default.jpg"}


def playlist_items(playlist_id, page_token=""):
    playlist_id = _text(playlist_id, "プレイリスト", 150, required=True)
    if not re.fullmatch(r"[\w-]+", playlist_id, flags=re.ASCII):
        raise ValueError("プレイリストが不正です。")
    data = _request("/playlistItems", {"part": "snippet,contentDetails", "playlistId": playlist_id}, page_token)
    items = []
    unavailable = 0
    for x in data.get("items", []):
        snippet = x.get("snippet", {})
        video_id = x.get("contentDetails", {}).get("videoId") or snippet.get("resourceId", {}).get("videoId", "")
        if not re.fullmatch(r"[\w-]{11}", video_id, flags=re.ASCII) or snippet.get("title") in ("Private video", "Deleted video"):
            unavailable += 1
            continue
        items.append(_video(video_id, snippet))
    return {**_page(data, items), "unavailable": unavailable}


def search(query, page_token=""):
    query = _text(query, "検索する言葉", 200, required=True)
    data = _request("/search", {"part": "snippet", "type": "video", "q": query,
                               "videoEmbeddable": "true"}, page_token)
    items = []
    for x in data.get("items", []):
        video_id = x.get("id", {}).get("videoId", "")
        if re.fullmatch(r"[\w-]{11}", video_id, flags=re.ASCII):
            items.append(_video(video_id, x.get("snippet", {})))
    return _page(data, items)
