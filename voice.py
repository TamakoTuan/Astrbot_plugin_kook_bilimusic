"""KOOK 语音频道接口封装：找人、进房、RTP 推流、退房。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import aiohttp

from .bilibili import USER_AGENT, AudioStream

KOOK_API = "https://www.kookapp.cn/api/v3"
KEEP_ALIVE_SECONDS = 45


class KookVoiceError(RuntimeError):
    """可以直接展示给用户的语音频道错误。"""


class KookVoice:
    def __init__(
        self,
        *,
        ffmpeg_path: str,
        bitrate: int = 48000,
        volume: float = 0.8,
        max_seconds: int = 1800,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.bitrate = bitrate
        self.volume = volume
        self.max_seconds = max_seconds
        # KOOK 返回 X-Rate-Limit-Remaining=0 时，该 bucket 需要等待到 reset。
        self._blocked_until: dict[str, float] = {}

    # ------------------------------------------------------------------ 基础

    async def api(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if session.closed:
            raise KookVoiceError("KOOK 连接已关闭")

        delay = self._blocked_until.get(path, 0.0) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(min(delay, 15.0))

        attempt = 0
        while True:
            try:
                async with session.request(
                    method,
                    f"{KOOK_API}/{path}",
                    params=params,
                    json=json,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept-Language": "zh-CN",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    status = resp.status
                    bucket = resp.headers.get("X-Rate-Limit-Bucket") or path
                    remaining = resp.headers.get("X-Rate-Limit-Remaining", "")
                    reset = resp.headers.get("X-Rate-Limit-Reset", "")
                    payload = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise KookVoiceError(f"请求 KOOK 失败：{exc}") from exc

            if remaining.isdigit() and int(remaining) <= 0 and reset.isdigit():
                until = time.monotonic() + float(reset) + 0.5
                self._blocked_until[bucket] = until
                self._blocked_until[path] = until

            if (
                status == 200
                and isinstance(payload, dict)
                and payload.get("code") == 0
            ):
                return payload.get("data")

            message = payload.get("message") if isinstance(payload, dict) else None
            text = str(message or f"KOOK HTTP {status}")
            retryable = (
                method == "GET"
                and attempt == 0
                and (
                    status == 429
                    or "登录环境异常" in text
                    or "限制" in text
                    or "频繁" in text
                )
            )
            if retryable:
                attempt += 1
                wait = float(reset) + 0.5 if reset.isdigit() else 3.0
                await asyncio.sleep(min(max(wait, 2.0), 10.0))
                continue
            raise KookVoiceError(text)

    # ------------------------------------------------------------------ 频道

    async def find_channel(
        self,
        session: aiohttp.ClientSession,
        guild_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """一次请求查到用户当前所在的语音频道（没有则 None）。

        用的是 KOOK 客户端同款接口 ``channel-user/get-joined-channel``，
        不再遍历服务器里所有语音频道，避免触发 KOOK 的异常环境拦截。
        """

        data = await self.api(
            session,
            "GET",
            "channel-user/get-joined-channel",
            params={"guild_id": str(guild_id), "user_id": str(user_id)},
        )
        if not isinstance(data, dict):
            return None
        for item in data.get("items") or []:
            if isinstance(item, dict) and int(item.get("type") or 0) == 2:
                return item
        return None

    # ------------------------------------------------------------------ 播放

    async def join(
        self, session: aiohttp.ClientSession, channel: dict[str, Any]
    ) -> dict[str, Any]:
        await self.leave_all(session)
        data = await self.api(
            session, "POST", "voice/join", json={"channel_id": str(channel["id"])}
        )
        if not isinstance(data, dict) or not data.get("ip") or not data.get("port"):
            raise KookVoiceError("KOOK 没有返回推流地址")
        return data

    async def leave(self, session: aiohttp.ClientSession, channel_id: str) -> None:
        if session.closed:
            return
        with contextlib.suppress(KookVoiceError):
            await self.api(
                session, "POST", "voice/leave", json={"channel_id": str(channel_id)}
            )

    async def leave_all(self, session: aiohttp.ClientSession) -> None:
        """机器人同一时间只能待在一个语音房间，先清掉残留连接。"""

        data = await self.api(session, "GET", "voice/list")
        payload = data if isinstance(data, dict) else {}
        for item in payload.get("items") or []:
            channel_id = str((item or {}).get("id") or "")
            if not channel_id:
                continue
            await self.leave(session, channel_id)
            await asyncio.sleep(1.0)

    async def stream(
        self,
        session: aiohttp.ClientSession,
        channel_id: str,
        endpoint: dict[str, Any],
        audio: AudioStream,
    ) -> None:
        """把远程音源转成 Opus RTP 推到 KOOK，直到播放结束。"""

        if not self.ffmpeg_path:
            raise KookVoiceError("没有找到 ffmpeg，请在插件配置里填写路径")

        cmd = self._ffmpeg_cmd(endpoint, audio)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise KookVoiceError("无法启动 ffmpeg") from exc

        assert proc.stderr is not None
        stderr = asyncio.create_task(proc.stderr.read())
        keep_alive = asyncio.create_task(self._keep_alive(session, channel_id))
        timeout = self.max_seconds
        if audio.duration > 0:
            timeout = min(timeout, audio.duration + 90)
        try:
            await asyncio.wait_for(proc.wait(), timeout=max(30, timeout))
        except asyncio.TimeoutError:
            raise KookVoiceError("播放超时") from None
        finally:
            keep_alive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keep_alive
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
            output = b""
            with contextlib.suppress(Exception):
                output = await stderr

        if proc.returncode not in (0, None):
            tail = output.decode("utf-8", "replace").strip()[-400:]
            raise KookVoiceError(f"ffmpeg 推流失败（退出码 {proc.returncode}）{tail}")

    async def _keep_alive(
        self, session: aiohttp.ClientSession, channel_id: str
    ) -> None:
        while True:
            await asyncio.sleep(KEEP_ALIVE_SECONDS)
            with contextlib.suppress(Exception):
                await self.api(
                    session,
                    "POST",
                    "voice/keep-alive",
                    json={"channel_id": channel_id},
                )

    def _ffmpeg_cmd(
        self, endpoint: dict[str, Any], audio: AudioStream
    ) -> list[str]:
        ip = str(endpoint.get("ip") or "").strip()
        port = str(endpoint.get("port") or "").strip()
        if not ip or not port:
            raise KookVoiceError("KOOK 没有返回推流地址")

        bitrate = int(endpoint.get("bitrate") or self.bitrate or 48000)
        ssrc = str(endpoint.get("audio_ssrc") or "1111")
        payload_type = str(endpoint.get("audio_pt") or "111")
        target = f"rtp://{ip}:{port}"
        if not bool(endpoint.get("rtcp_mux", True)):
            rtcp_port = endpoint.get("rtcp_port")
            if rtcp_port:
                target = f"{target}?rtcpport={rtcp_port}"
        tee = f"[select=a:f=rtp:ssrc={ssrc}:payload_type={payload_type}]{target}"
        headers = "".join(f"{key}: {value}\r\n" for key, value in audio.headers.items())

        return [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-rw_timeout",
            "20000000",
            "-headers",
            headers,
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-re",
            "-i",
            audio.url,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-acodec",
            "libopus",
            "-application",
            "audio",
            "-b:a",
            str(bitrate),
            "-ac",
            "2",
            "-ar",
            "48000",
            "-af",
            f"volume={self.volume}",
            "-f",
            "tee",
            tee,
        ]
