"""KOOK 语音频道点歌。

独立插件：自己搜索 B 站、自己解析音源、自己用 ffmpeg 推 RTP，
不依赖「我想听歌」或其他音乐插件。

支持选歌卡片、多首队列、播放进度、切歌/停止按钮。
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import secrets
import shutil
import time
from dataclasses import dataclass, replace
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import AstrBotMessage, Group, MessageMember, MessageType
from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.platform.sources.kook.kook_types import KookMessageType
from astrbot.core.star.filter.command import GreedyStr

from .bilibili import (
    REFERER,
    USER_AGENT,
    AudioStream,
    BilibiliClient,
    BilibiliError,
    Song,
    parse_reference,
)
from .cards import build_now_playing_card, build_queue_card, build_search_cards
from .voice import KOOK_API, KookVoice, KookVoiceError

PLUGIN_NAME = "Astrbot_plugin_kook_bilimusic"
PLUGIN_AUTHOR = "TamakoTuan"
PLUGIN_VERSION = "1.2.2"

_INDEX_RE = re.compile(r"\d{1,2}")


@dataclass(slots=True)
class _Pending:
    songs: list[Song]
    query: str
    user_id: str
    expires_at: float
    token: str = ""


@dataclass(slots=True)
class _CardSession:
    songs: list[Song]
    guild_id: str
    channel_id: str
    user_id: str
    adapter: Any
    origin: str
    expires_at: float


@dataclass(slots=True)
class _Player:
    bot_key: str
    guild_id: str
    channel: dict[str, Any]
    channel_name: str
    origin_event: AstrMessageEvent
    queue: list[Song]
    current: Song | None = None
    worker: asyncio.Task | None = None
    stream_task: asyncio.Task | None = None
    skip_requested: bool = False
    stopping: bool = False


class KookVoiceMusicPlugin(Star):
    """在 KOOK 语音频道播放 B 站歌曲，支持队列与切歌。"""

    name = PLUGIN_NAME
    author = PLUGIN_AUTHOR

    def __init__(self, context: Context, config: dict[str, Any] | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._bilibili: BilibiliClient | None = None
        self._pending: dict[str, _Pending] = {}
        self._cards: dict[str, _CardSession] = {}
        self._cover_cache: dict[str, str] = {}
        self._players: dict[str, _Player] = {}
        self._jobs: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            connector=connector,
            headers={"User-Agent": USER_AGENT, "Referer": REFERER},
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
        )
        self._bilibili = BilibiliClient(self._session)
        self.context.add_llm_tools(KookPlayTool(self))
        logger.info("[kook-voice] 插件已加载（作者 %s）", PLUGIN_AUTHOR)

    async def terminate(self) -> None:
        for player in list(self._players.values()):
            player.stopping = True
            player.queue.clear()
            if player.stream_task is not None:
                player.stream_task.cancel()
            if player.worker is not None:
                player.worker.cancel()
        self._players.clear()
        for job in list(self._jobs):
            job.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._bilibili = None

    # -------------------------------------------------------------- 卡片按钮交互

    @filter.on_platform_loaded()
    async def _on_platform_loaded(self) -> None:
        self._install_click_observer()

    def _install_click_observer(self) -> None:
        """监听 KOOK 的 message_btn_click 事件，处理卡片按钮。"""

        platform_manager = getattr(self.context, "platform_manager", None)
        for platform in getattr(platform_manager, "platform_insts", []) or []:
            client = getattr(platform, "client", None)
            if client is None:
                continue
            if "kook" not in type(client).__module__.lower():
                continue
            callback = getattr(client, "event_callback", None)
            if not callable(callback) or getattr(client, "_kook_voice_click", False):
                continue

            async def wrapped(
                data: Any, _callback: Any = callback, _platform: Any = platform
            ) -> None:
                try:
                    self._maybe_handle_click(_platform, data)
                finally:
                    await _callback(data)

            client.event_callback = wrapped
            client._kook_voice_click = True
            logger.info("[kook-voice] 已启用卡片按钮交互")

    def _maybe_handle_click(self, adapter: Any, data: Any) -> None:
        try:
            if int(getattr(data, "type", 0) or 0) != 255:
                return
            extra = getattr(data, "extra", None)
            if str(getattr(extra, "type", "")) != "message_btn_click":
                return
            body = getattr(extra, "body", None)
            if not isinstance(body, dict):
                return
            value = str(body.get("value") or "")
            if not value.startswith(("kvp:", "kvq:")):
                return
            task = asyncio.create_task(self._handle_card_click(adapter, body))
            self._jobs.add(task)
            task.add_done_callback(self._jobs.discard)
        except Exception:
            logger.debug("[kook-voice] 解析按钮事件失败", exc_info=True)

    async def _handle_card_click(self, adapter: Any, body: dict[str, Any]) -> None:
        value = str(body.get("value") or "")
        if value.startswith("kvp:"):
            await self._click_pick(adapter, body, value)
        elif value.startswith("kvq:"):
            await self._click_control(adapter, body, value)

    async def _click_pick(
        self, adapter: Any, body: dict[str, Any], value: str
    ) -> None:
        parts = value.split(":")
        if len(parts) != 3:
            return
        try:
            index = int(parts[2])
        except ValueError:
            return
        session = self._cards.get(parts[1])
        if session is None:
            return
        if time.monotonic() > session.expires_at:
            self._cards.pop(parts[1], None)
            return
        user_id = str(body.get("user_id") or "")
        if user_id != session.user_id:
            return
        if not 1 <= index <= len(session.songs):
            return

        event = self._synthetic_event(
            adapter, session.channel_id, session.guild_id, user_id
        )
        if event is None:
            return
        player = self._player_for_adapter(adapter)
        channel = player.channel if player is not None else await self._require_channel(event)
        if channel is None:
            return
        song = session.songs[index - 1]
        await self._enqueue(event, [song], channel, announce=False)
        await self._say(event, f"已加入队列：{song.display_title}")

    async def _click_control(
        self, adapter: Any, body: dict[str, Any], value: str
    ) -> None:
        player = self._player_for_adapter(adapter)
        channel_id = str(body.get("target_id") or "")
        if player is None:
            if channel_id:
                with contextlib.suppress(Exception):
                    await adapter.client.send_text(
                        channel_id,
                        "当前没有在播放。",
                        MessageType.GROUP_MESSAGE,
                        KookMessageType.KMARKDOWN,
                    )
            return
        event = self._synthetic_event(
            adapter,
            channel_id or self._channel_id(player.origin_event),
            player.guild_id,
            str(body.get("user_id") or ""),
        )
        if event is None:
            return
        operation = value.split(":")[1] if ":" in value else ""
        if operation == "skip":
            self._skip(player)
            await self._say(event, "已切歌。")
        elif operation == "stop":
            self._stop(player)
            await self._say(event, "已停止播放并清空队列。")
        elif operation == "list":
            await self._send_card(
                event,
                build_queue_card(player.current, list(player.queue), player.channel_name),
            )

    def _synthetic_event(
        self, adapter: Any, channel_id: str, guild_id: str, user_id: str
    ) -> AstrMessageEvent | None:
        client = getattr(adapter, "client", None)
        create_event = getattr(adapter, "create_event", None)
        if client is None or not callable(create_event):
            return None
        message = AstrBotMessage()
        message.self_id = getattr(client, "bot_id", "")
        message.session_id = channel_id
        message.type = MessageType.GROUP_MESSAGE
        message.group = Group(group_id=channel_id, group_name="")
        message.sender = MessageMember(user_id=user_id, nickname="")
        message.message_str = ""
        message.message = []
        message.raw_message = {"extra": {"guild_id": guild_id}}
        return create_event(message)

    # ------------------------------------------------------------------ 基础工具

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _ffmpeg(self) -> str:
        configured = str(self._cfg("ffmpeg_path", "")).strip()
        if configured:
            return configured
        return shutil.which("ffmpeg") or ""

    def _voice(self) -> KookVoice:
        return KookVoice(
            ffmpeg_path=self._ffmpeg(),
            bitrate=int(self._cfg("default_bitrate", 48000) or 48000),
            volume=float(self._cfg("volume", 0.8) or 0.8),
            max_seconds=int(self._cfg("max_play_seconds", 1800) or 1800),
        )

    def _kook_session(self, event: AstrMessageEvent) -> aiohttp.ClientSession | None:
        """KOOK 调用必须用适配器自己的会话，里面带着 Authorization。"""

        client = getattr(event, "client", None)
        session = getattr(client, "http_client", None)
        if isinstance(session, aiohttp.ClientSession) and not session.closed:
            return session
        return None

    def _adapter_for(self, event: AstrMessageEvent) -> Any | None:
        client = getattr(event, "client", None)
        platform_manager = getattr(self.context, "platform_manager", None)
        for platform in getattr(platform_manager, "platform_insts", []) or []:
            if getattr(platform, "client", None) is client:
                return platform
        return None

    def _channel_id(self, event: AstrMessageEvent) -> str:
        group = getattr(event.message_obj, "group", None)
        return (
            str(getattr(group, "group_id", "") or "")
            or event.get_group_id()
            or str(getattr(event, "session_id", "") or "")
            or event.get_sender_id()
        )

    def _guild_id(self, event: AstrMessageEvent) -> str:
        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, dict):
            extra = raw.get("extra") or {}
            if isinstance(extra, dict) and extra.get("guild_id"):
                return str(extra["guild_id"])
            if raw.get("guild_id"):
                return str(raw["guild_id"])
        return ""

    def _bot_key(self, event: AstrMessageEvent) -> str:
        return event.get_self_id() or "kook"

    def _player_for_adapter(self, adapter: Any) -> _Player | None:
        bot_id = str(getattr(getattr(adapter, "client", None), "bot_id", "") or "")
        if bot_id:
            player = self._players.get(bot_id)
            if player is not None:
                return player
        return next(iter(self._players.values()), None)

    def _max_duration(self) -> int:
        return max(1, int(self._cfg("max_duration_minutes", 30) or 30)) * 60

    async def _say(self, event: AstrMessageEvent, text: str) -> None:
        with contextlib.suppress(Exception):
            await event.send(event.plain_result(text))

    async def _send_card(
        self, event: AstrMessageEvent, cards: list[dict[str, Any]]
    ) -> bool:
        if not cards:
            return False
        client = getattr(event, "client", None)
        send_text = getattr(client, "send_text", None)
        if not callable(send_text):
            return False
        channel_id = self._channel_id(event)
        content = json.dumps(cards, ensure_ascii=False)
        for attempt in range(2):
            try:
                await send_text(
                    channel_id,
                    content,
                    MessageType.GROUP_MESSAGE,
                    KookMessageType.CARD,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "[kook-voice] 发送卡片失败（%s 字节，第 %s 次）: %s",
                    len(content.encode("utf-8")),
                    attempt + 1,
                    exc,
                )
                if attempt == 0:
                    await asyncio.sleep(1.0)
        return False

    async def _require_channel(
        self, event: AstrMessageEvent
    ) -> dict[str, Any] | None:
        if event.get_platform_name() != "kook":
            await self._say(event, "这个插件只支持 KOOK 平台。")
            return None
        guild_id = self._guild_id(event)
        user_id = event.get_sender_id()
        if not guild_id or not user_id:
            await self._say(event, "只能在 KOOK 服务器频道里点歌。")
            return None
        session = self._kook_session(event)
        if session is None:
            await self._say(event, "拿不到 KOOK 连接，请稍后再试。")
            return None
        try:
            channel = await self._voice().find_channel(session, guild_id, user_id)
        except KookVoiceError as exc:
            logger.warning("[kook-voice] 查询语音频道失败: %s", exc)
            await self._say(event, f"查询语音频道失败：{exc}")
            return None
        if channel is None:
            await self._say(event, "你还没进语音频道。先进一个语音频道再点歌。")
            return None
        if channel.get("has_password"):
            await self._say(
                event,
                f"你在语音频道「{channel.get('name')}」，但这个频道有密码，机器人进不去。",
            )
            return None
        return channel

    # ------------------------------------------------------------------ 封面转存

    async def _host_covers(
        self, session: aiohttp.ClientSession, songs: list[Song]
    ) -> list[Song]:
        semaphore = asyncio.Semaphore(3)

        async def host(song: Song) -> Song:
            if not song.cover:
                return song
            async with semaphore:
                remote = await self._upload_cover(session, song.cover)
            return replace(song, cover=remote) if remote else replace(song, cover="")

        return list(await asyncio.gather(*(host(song) for song in songs)))

    async def _upload_cover(self, session: aiohttp.ClientSession, url: str) -> str:
        cached = self._cover_cache.get(url)
        if cached:
            return cached
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return ""
        if not data or len(data) > 3_000_000:
            return ""
        with contextlib.suppress(Exception):
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                image = image.convert("RGB")
                image.thumbnail((512, 512))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85)
                data = buffer.getvalue()
                content_type = "image/jpeg"
        form = aiohttp.FormData()
        form.add_field("file", data, filename="cover.jpg", content_type=content_type)
        try:
            async with session.post(
                f"{KOOK_API}/asset/create",
                data=form,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return ""
        if isinstance(payload, dict) and payload.get("code") == 0:
            remote = str((payload.get("data") or {}).get("url") or "")
            if remote:
                self._cover_cache[url] = remote
                return remote
        return ""

    async def _send_song_cards(
        self,
        event: AstrMessageEvent,
        songs: list[Song],
        token: str,
        timeout: int,
        query: str,
    ) -> bool:
        cards = build_search_cards(songs, token, timeout, query)
        if await self._send_card(event, cards):
            return True
        session = self._kook_session(event)
        if session is not None:
            hosted = await self._host_covers(session, songs)
            cards = build_search_cards(hosted, token, timeout, query)
            if await self._send_card(event, cards):
                logger.info("[kook-voice] 封面转存 KOOK 后卡片发送成功")
                return True
        if await self._send_card(event, _strip_images(cards)):
            logger.info("[kook-voice] 去掉封面后卡片发送成功")
            return True
        return False

    # ------------------------------------------------------------------ 命令

    @filter.command("点歌")
    async def cmd_request_song(self, event: AstrMessageEvent, keyword: GreedyStr):
        event.stop_event()
        await self._request(event, str(keyword or ""), direct=False)

    @filter.command("直接点歌")
    async def cmd_play_song(self, event: AstrMessageEvent, keyword: GreedyStr):
        event.stop_event()
        await self._request(event, str(keyword or ""), direct=True)

    @filter.command("队列")
    async def cmd_queue(self, event: AstrMessageEvent):
        event.stop_event()
        adapter = self._adapter_for(event)
        player = (
            self._player_for_adapter(adapter)
            if adapter is not None
            else self._player_for_event(event)
        )
        if player is None:
            await self._say(event, "当前没有在播放。")
            return
        await self._send_card(
            event, build_queue_card(player.current, list(player.queue), player.channel_name)
        )

    @filter.command("切歌")
    async def cmd_skip(self, event: AstrMessageEvent):
        event.stop_event()
        player = self._player_for_event(event)
        if player is None:
            await self._say(event, "当前没有在播放。")
            return
        self._skip(player)
        await self._say(event, "已切歌。")

    @filter.command("停止")
    async def cmd_stop(self, event: AstrMessageEvent):
        event.stop_event()
        player = self._player_for_event(event)
        if player is None:
            await self._say(event, "当前没有在播放。")
            return
        self._stop(player)
        await self._say(event, "已停止播放并清空队列。")

    @filter.command("kook语音状态")
    async def cmd_status(self, event: AstrMessageEvent):
        event.stop_event()
        lines = ["KOOK 语音点歌状态："]
        lines.append(f"ffmpeg：{self._ffmpeg() or '未找到'}")
        player = self._player_for_event(event)
        if player is None:
            lines.append("当前没有在播放。")
        else:
            current = player.current.display_title if player.current else "准备中"
            lines.append(f"正在播放：{current}")
            lines.append(f"队列剩余：{len(player.queue)} 首")
            lines.append(f"语音频道：{player.channel_name}")
        session = self._kook_session(event)
        if (
            event.get_platform_name() == "kook"
            and session is not None
            and self._guild_id(event)
        ):
            try:
                channel = await self._voice().find_channel(
                    session, self._guild_id(event), event.get_sender_id()
                )
            except KookVoiceError as exc:
                lines.append(f"查询语音频道失败：{exc}")
            else:
                if channel is None:
                    lines.append("你当前不在语音频道里。")
                else:
                    lines.append(f"你当前在：「{channel.get('name')}」")
        await self._say(event, "\n".join(lines))

    # ------------------------------------------------------------------ 点歌流程

    async def _request(
        self, event: AstrMessageEvent, keyword: str, *, direct: bool
    ) -> None:
        if not bool(self._cfg("enable", True)):
            await self._say(event, "KOOK 语音点歌已关闭。")
            return
        query = " ".join(keyword.split())
        if not query:
            await self._say(event, "用法：点歌 歌名，例如「点歌 晴天」")
            return

        player = self._player_for_event(event)
        if player is not None:
            channel = player.channel
        else:
            channel = await self._require_channel(event)
            if channel is None:
                return
        assert self._bilibili is not None

        try:
            reference = parse_reference(query)
            if reference is not None:
                songs = [await self._bilibili.from_reference(*reference)]
            else:
                limit = int(self._cfg("search_limit", 5) or 5)
                songs = await self._bilibili.search(query, limit=limit)
        except BilibiliError as exc:
            await self._say(event, f"搜索失败：{exc}")
            return

        songs = [song for song in songs if song.duration <= self._max_duration()]
        if not songs:
            await self._say(
                event,
                f"没有找到 {int(self._cfg('max_duration_minutes', 30))} 分钟以内的版本。",
            )
            return

        if direct:
            await self._enqueue(event, [songs[0]], channel)
            return

        session = self._kook_session(event)
        if session is not None:
            songs = await self._host_covers(session, songs)

        timeout = int(self._cfg("selection_timeout", 60) or 60)
        songs = songs[:10]
        token = secrets.token_hex(4)
        self._pending[event.unified_msg_origin] = _Pending(
            songs=songs,
            query=query,
            user_id=event.get_sender_id(),
            expires_at=time.monotonic() + timeout,
            token=token,
        )

        adapter = self._adapter_for(event)
        if adapter is not None:
            self._cards[token] = _CardSession(
                songs=songs,
                guild_id=self._guild_id(event),
                channel_id=self._channel_id(event),
                user_id=event.get_sender_id(),
                adapter=adapter,
                origin=event.unified_msg_origin,
                expires_at=time.monotonic() + timeout,
            )
            now = time.monotonic()
            for key in [k for k, v in self._cards.items() if v.expires_at < now]:
                self._cards.pop(key, None)

        if not await self._send_song_cards(event, songs, token, timeout, query):
            lines = [f"在 B 站找到这些版本（{timeout} 秒内回复序号）："]
            for index, song in enumerate(songs, start=1):
                artist = f" — {song.artist}" if song.artist else ""
                lines.append(
                    f"{index}. {song.display_title}（{song.duration_text}）{artist}"
                )
            lines.append("回复「取消」结束。")
            await self._say(event, "\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_selection_reply(self, event: AstrMessageEvent):
        pending = self._pending.get(event.unified_msg_origin)
        if pending is None:
            return
        if event.get_sender_id() != pending.user_id:
            return
        text = (event.message_str or "").strip()
        if text == "取消":
            self._pending.pop(event.unified_msg_origin, None)
            if pending.token:
                self._cards.pop(pending.token, None)
            event.stop_event()
            await self._say(event, "已取消点歌。")
            return
        if time.monotonic() > pending.expires_at:
            self._pending.pop(event.unified_msg_origin, None)
            return
        indices = _parse_indices(text)
        if not indices:
            return
        self._pending.pop(event.unified_msg_origin, None)
        if pending.token:
            self._cards.pop(pending.token, None)
        event.stop_event()

        player = self._player_for_event(event)
        channel = player.channel if player is not None else await self._require_channel(event)
        if channel is None:
            return
        selected = [
            pending.songs[index - 1]
            for index in indices
            if 1 <= index <= len(pending.songs)
        ]
        if selected:
            await self._enqueue(event, selected, channel)

    # ------------------------------------------------------------------ 队列播放

    def _player_for_event(self, event: AstrMessageEvent) -> _Player | None:
        return self._players.get(self._bot_key(event))

    async def _enqueue(
        self,
        event: AstrMessageEvent,
        songs: list[Song],
        channel: dict[str, Any],
        *,
        announce: bool = True,
    ) -> None:
        if not songs:
            return
        bot_key = self._bot_key(event)
        player = self._players.get(bot_key)
        if player is None:
            player = _Player(
                bot_key=bot_key,
                guild_id=self._guild_id(event),
                channel=channel,
                channel_name=str(channel.get("name") or channel.get("id") or ""),
                origin_event=event,
                queue=[],
            )
            self._players[bot_key] = player
            player.queue.extend(songs)
            player.worker = asyncio.create_task(self._run_queue(player))
        else:
            player.queue.extend(songs)
        if announce:
            names = "、".join(song.display_title for song in songs[:3])
            suffix = " 等" if len(songs) > 3 else ""
            await self._say(event, f"已加入队列：{names}{suffix}")

    async def _run_queue(self, player: _Player) -> None:
        try:
            while player.queue and not player.stopping:
                song = player.queue.pop(0)
                player.current = song
                await self._play_song(player, song)
                player.current = None
        finally:
            was_stopping = player.stopping
            player.current = None
            player.stopping = True
            self._players.pop(player.bot_key, None)
            if bool(self._cfg("announce", True)) and not was_stopping:
                with contextlib.suppress(Exception):
                    await self._say(player.origin_event, "播放结束，已退出语音频道。")

    async def _play_song(self, player: _Player, song: Song) -> None:
        event = player.origin_event
        session = self._kook_session(event)
        if session is None or self._bilibili is None:
            await self._say(event, "KOOK 连接不可用，队列已停止。")
            player.stopping = True
            return
        if player.stopping:
            return
        try:
            resolved, audio = await self._bilibili.resolve(song)
        except BilibiliError as exc:
            await self._say(event, f"《{song.title}》播放失败：{exc}")
            return
        if player.stopping:
            return
        if resolved.duration > self._max_duration():
            await self._say(event, f"《{resolved.title}》超过了时长上限，已跳过。")
            return

        channel_id = str(player.channel.get("id") or "")
        try:
            endpoint = await self._voice().join(session, player.channel)
        except KookVoiceError as exc:
            await self._say(event, f"进不去语音频道：{exc}")
            player.stopping = True
            return

        try:
            if player.skip_requested:
                player.skip_requested = False
                return
            if bool(self._cfg("announce", True)):
                if not await self._send_card(
                    event,
                    build_now_playing_card(
                        resolved, player.channel_name, len(player.queue)
                    ),
                ):
                    await self._say(
                        event,
                        f"正在语音频道「{player.channel_name}」"
                        f"播放《{resolved.display_title}》…",
                    )
            player.stream_task = asyncio.create_task(
                self._voice().stream(session, channel_id, endpoint, audio)
            )
            try:
                await player.stream_task
            except asyncio.CancelledError:
                if not (player.skip_requested or player.stopping):
                    raise
            finally:
                player.stream_task = None
                player.skip_requested = False
        finally:
            await self._voice().leave(session, channel_id)

    def _skip(self, player: _Player) -> None:
        player.skip_requested = True
        if player.stream_task is not None:
            player.stream_task.cancel()

    def _stop(self, player: _Player) -> None:
        player.stopping = True
        player.queue.clear()
        if player.stream_task is not None:
            player.stream_task.cancel()

    # ------------------------------------------------------------------ LLM 工具

    async def play_from_llm(
        self, event: AstrMessageEvent, title: str, artist: str
    ) -> str:
        if not bool(self._cfg("enable", True)):
            return "KOOK 语音点歌已关闭。"
        query = " ".join(f"{title} {artist}".split())
        if not query:
            return "请提供歌名。"
        if event.get_platform_name() != "kook":
            return "这个功能只在 KOOK 平台可用。"
        if getattr(event, "is_at_or_wake_command", False):
            return "这是命令消息，已由点歌命令处理，不需要再调用工具。"
        assert self._bilibili is not None
        try:
            reference = parse_reference(query)
            if reference is not None:
                songs = [await self._bilibili.from_reference(*reference)]
            else:
                limit = int(self._cfg("search_limit", 5) or 5)
                songs = await self._bilibili.search(query, limit=limit)
        except BilibiliError as exc:
            return f"搜索失败：{exc}"
        songs = [song for song in songs if song.duration <= self._max_duration()]
        if not songs:
            return "没有找到合适的版本。"

        player = self._player_for_event(event)
        if player is not None:
            channel = player.channel
        else:
            channel = await self._require_channel(event)
            if channel is None:
                return "点歌人不在可加入的语音频道"
        best = _pick_best(songs, title, artist)
        await self._enqueue(event, [best], channel)
        return f"已加入队列：{best.display_title}"


class KookPlayTool(FunctionTool):
    """让模型可以用自然语言直接点歌。"""

    def __init__(self, plugin: KookVoiceMusicPlugin) -> None:
        super().__init__(
            name="kook_voice_play",
            description=(
                "在 KOOK 语音频道为点歌人点一首 B 站歌曲。用户说“点歌”“放首歌”"
                "“来一首……”时调用。工具会自己搜索并选择最匹配的版本，加入播放队列，"
                "当前没有播放时会立即开始。用户不在语音频道、频道有密码时会返回失败原因；"
                "失败时不要反复重试，把原因告诉用户即可。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "歌名，例如“晴天”。",
                    },
                    "artist": {
                        "type": "string",
                        "description": "歌手名，可选。",
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        )
        self._plugin = plugin

    async def call(self, context: Any, **kwargs: Any) -> str:
        event = getattr(getattr(context, "context", None), "event", None)
        if not isinstance(event, AstrMessageEvent):
            return "无法获取当前聊天会话。"
        return await self._plugin.play_from_llm(
            event,
            str(kwargs.get("title") or ""),
            str(kwargs.get("artist") or ""),
        )


def _parse_indices(text: str) -> list[int]:
    """支持 “1”“1 2 3”“1,2”“1-3” 这几种选歌方式。"""

    value = text.strip()
    if not value:
        return []
    range_match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", value)
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        if start <= end:
            return list(range(start, end + 1))
        return list(range(end, start + 1))
    return [int(item) for item in _INDEX_RE.findall(value)]


def _strip_images(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去掉卡片里的图片，保留文字和按钮。"""

    result: list[dict[str, Any]] = []
    for card in cards:
        modules: list[dict[str, Any]] = []
        for module in card.get("modules") or []:
            module_type = module.get("type")
            if module_type == "context":
                elements = [
                    element
                    for element in module.get("elements") or []
                    if element.get("type") != "image"
                ]
                if elements:
                    modules.append({**module, "elements": elements})
            elif module_type in ("image-group", "container"):
                continue
            elif module_type == "section" and (
                (module.get("accessory") or {}).get("type") == "image"
            ):
                modules.append(
                    {key: value for key, value in module.items() if key != "accessory"}
                )
            else:
                modules.append(module)
        result.append({**card, "modules": modules})
    return result


def _pick_best(songs: list[Song], title: str, artist: str) -> Song:
    """在自动模式下挑一个最像“正常完整录音”的版本。"""

    title_key = title.lower().strip()
    artist_key = artist.lower().strip()
    noise = (
        "live",
        "现场",
        "翻唱",
        "cover",
        "伴奏",
        "纯音乐",
        "dj",
        "remix",
        "片段",
        "剪辑",
        "试听",
    )

    def score(song: Song) -> int:
        text = f"{song.title} {song.artist}".lower()
        value = 0
        if title_key and title_key in text:
            value += 4
        if artist_key and artist_key in text:
            value += 3
        if 60 <= song.duration <= 900:
            value += 1
        value -= sum(2 for word in noise if word in text)
        return value

    return max(songs, key=score)
