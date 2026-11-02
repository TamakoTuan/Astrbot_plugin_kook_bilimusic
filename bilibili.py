"""B 站匿名搜索与音源解析（WBI 签名版）。

只依赖 ``aiohttp``，不登录也能搜索并使用 DASH 音轨；不需要 Cookie 配置。
部分仅限登录/大会员的视频会拿不到音源，此时会抛出 :class:`BilibiliError`。
"""

from __future__ import annotations

import contextlib
import hashlib
import html as html_lib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

API_ROOT = "https://api.bilibili.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"

# 官方 WBI 混入密钥用的固定置换表。
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"(?i)\bav(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")


class BilibiliError(RuntimeError):
    """可以直接展示给用户的 B 站错误。"""


@dataclass(frozen=True, slots=True)
class Song:
    """一条可以直接播放的 B 站分 P。"""

    bvid: str
    title: str
    artist: str
    duration: int
    cover: str = ""
    cid: int = 0
    page_title: str = ""

    @property
    def display_title(self) -> str:
        if self.page_title and self.page_title != self.title:
            return f"{self.title}（{self.page_title}）"
        return self.title

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration)


@dataclass(frozen=True, slots=True)
class AudioStream:
    url: str
    backup_urls: tuple[str, ...]
    duration: int
    headers: dict[str, str]


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_duration(value: Any) -> int:
    """把搜索接口返回的 ``m:ss`` / ``h:mm:ss`` 转成秒。"""

    text = str(value or "").strip()
    if not text:
        return 0
    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return 0


def parse_reference(text: str) -> tuple[str, str] | None:
    """从文本中识别 BV 号或 AV 号，返回 ``("bvid"|"aid", value)``。"""

    match = _BV_RE.search(text)
    if match:
        return ("bvid", match.group(0))
    match = _AV_RE.search(text)
    if match:
        return ("aid", match.group(1))
    return None


def _clean_title(value: Any) -> str:
    text = _TAG_RE.sub("", str(value or ""))
    return " ".join(html_lib.unescape(text).split())


def _normalize_cover(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return f"https://{url[len('http://'):]}"
    return url if url.startswith("https://") else ""


def _mixin_key(origin: str) -> str:
    return "".join(origin[index] for index in _MIXIN_KEY_ENC_TAB)[:32]


class BilibiliClient:
    """最小可用的 B 站客户端：搜索、视频详情、DASH 音源。"""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._wbi: tuple[str, str] | None = None
        self._wbi_at = 0.0
        self._warmed = False

    async def _warm_up(self) -> None:
        """先访问一次主页，让 B 站下发 buvid 等基础 Cookie。"""

        if self._warmed:
            return
        self._warmed = True
        with contextlib.suppress(Exception):
            async with self._session.get("https://www.bilibili.com/") as resp:
                await resp.read()

    async def _json(
        self, url: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self._session.get(
                url, params=params, headers={"Referer": REFERER}
            ) as resp:
                if resp.status != 200:
                    raise BilibiliError(f"B 站返回 HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BilibiliError("连接 B 站失败，请稍后重试") from exc
        if not isinstance(payload, dict):
            raise BilibiliError("B 站返回了无法识别的数据")
        if payload.get("code") != 0:
            message = str(payload.get("message") or "").strip()
            raise BilibiliError(message or f"B 站错误码 {payload.get('code')}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def _wbi_keys(self) -> tuple[str, str]:
        if self._wbi and time.monotonic() - self._wbi_at < 6 * 3600:
            return self._wbi
        await self._warm_up()
        try:
            async with self._session.get(
                f"{API_ROOT}/x/web-interface/nav",
                headers={"Referer": REFERER},
            ) as resp:
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BilibiliError("连接 B 站失败，请稍后重试") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        wbi_img = (data or {}).get("wbi_img") or {}
        img_url = str(wbi_img.get("img_url") or "")
        sub_url = str(wbi_img.get("sub_url") or "")
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        if not img_key or not sub_key:
            raise BilibiliError("B 站没有下发签名密钥，搜索暂不可用")
        self._wbi = (img_key, sub_key)
        self._wbi_at = time.monotonic()
        return self._wbi

    async def _signed_get(
        self, url: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        img_key, sub_key = await self._wbi_keys()
        signing = {
            key: re.sub(r"[!'()*]", "", str(value)) for key, value in params.items()
        }
        signing["wts"] = int(time.time())
        query = "&".join(
            f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
            for key, value in sorted(signing.items())
        )
        signature = hashlib.md5(
            f"{query}{_mixin_key(img_key + sub_key)}".encode()
        ).hexdigest()
        await self._warm_up()
        return await self._json(f"{url}?{query}&w_rid={signature}")

    async def search(self, keyword: str, *, limit: int = 5) -> list[Song]:
        keyword = " ".join(str(keyword).split())
        if not keyword:
            raise BilibiliError("请提供歌名")
        data = await self._signed_get(
            f"{API_ROOT}/x/web-interface/wbi/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "page": 1,
                "page_size": max(1, min(int(limit), 10)),
            },
        )
        songs: list[Song] = []
        for item in data.get("result") or []:
            if not isinstance(item, dict):
                continue
            bvid = str(item.get("bvid") or "").strip()
            title = _clean_title(item.get("title"))
            if not bvid or not title:
                continue
            songs.append(
                Song(
                    bvid=bvid,
                    title=title,
                    artist=str(item.get("author") or "").strip(),
                    duration=parse_duration(item.get("duration")),
                    cover=_normalize_cover(item.get("pic")),
                )
            )
        if not songs:
            raise BilibiliError(f"没有找到「{keyword}」")
        return songs

    async def from_reference(self, kind: str, value: str) -> Song:
        params = {"bvid": value} if kind == "bvid" else {"aid": value}
        data = await self._json(f"{API_ROOT}/x/web-interface/view", params=params)
        bvid = str(data.get("bvid") or "").strip()
        title = _clean_title(data.get("title"))
        if not bvid or not title:
            raise BilibiliError("没有找到这个视频")
        pages = data.get("pages") or []
        page_title = ""
        cid = 0
        if pages:
            cid = int(pages[0].get("cid") or 0)
            page_title = str(pages[0].get("part") or "").strip() if len(pages) > 1 else ""
        return Song(
            bvid=bvid,
            title=title,
            artist=str((data.get("owner") or {}).get("name") or "").strip(),
            duration=int(data.get("duration") or 0),
            cover=_normalize_cover(data.get("pic")),
            cid=cid,
            page_title=page_title,
        )

    async def _playurl(self, bvid: str, cid: int) -> dict[str, Any]:
        params = {"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1}
        try:
            return await self._json(
                f"{API_ROOT}/x/player/playurl", params=params
            )
        except BilibiliError:
            # 新版接口要求 WBI 签名，失败时再签一次。
            return await self._signed_get(
                f"{API_ROOT}/x/player/wbi/playurl", params
            )

    async def resolve(self, song: Song) -> tuple[Song, AudioStream]:
        """补全 cid 和时长，并选出码率最高的 DASH 音轨。"""

        data = await self._json(
            f"{API_ROOT}/x/web-interface/view", params={"bvid": song.bvid}
        )
        pages = data.get("pages") or []
        if not pages:
            raise BilibiliError("这个视频没有可播放的分 P")

        page = pages[0]
        cid = int(page.get("cid") or song.cid or 0)
        duration = int(page.get("duration") or data.get("duration") or song.duration or 0)
        title = _clean_title(data.get("title")) or song.title
        artist = str((data.get("owner") or {}).get("name") or song.artist).strip()
        page_title = str(page.get("part") or "").strip() if len(pages) > 1 else ""

        play = await self._playurl(song.bvid, cid)
        dash = play.get("dash") or {}
        audios = [item for item in (dash.get("audio") or []) if isinstance(item, dict)]
        url = ""
        backups: tuple[str, ...] = ()
        if audios:
            best = max(
                audios,
                key=lambda item: (
                    int(item.get("id") or 0),
                    int(item.get("bandwidth") or 0),
                ),
            )
            url = str(best.get("baseUrl") or best.get("base_url") or "")
            backups = tuple(
                str(item)
                for item in (best.get("backupUrl") or best.get("backup_url") or [])
                if item
            )
        elif play.get("durl"):
            first = (play.get("durl") or [{}])[0]
            url = str(first.get("url") or "")
        if not url:
            raise BilibiliError("这个视频没有可用音源（可能需要登录或属于付费内容）")

        resolved = Song(
            bvid=song.bvid,
            title=title,
            artist=artist,
            duration=duration,
            cover=_normalize_cover(data.get("pic")) or song.cover,
            cid=cid,
            page_title=page_title,
        )
        stream = AudioStream(
            url=url,
            backup_urls=backups,
            duration=duration,
            headers={"Referer": REFERER, "User-Agent": USER_AGENT},
        )
        return resolved, stream
