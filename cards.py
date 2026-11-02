"""KOOK 卡片消息的 JSON 构造。

只负责把歌曲列表、正在播放和队列渲染成卡片，不涉及播放逻辑。
按钮统一使用 return-val：点击后 KOOK 会回传 value。
"""

from __future__ import annotations

import time

from .bilibili import Song


def _short(text: str, limit: int = 70) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _button(text: str, value: str, theme: str = "primary") -> dict:
    return {
        "type": "button",
        "theme": theme,
        "click": "return-val",
        "value": value,
        "text": {"type": "plain-text", "content": text},
    }


def _control_group() -> dict:
    return {
        "type": "action-group",
        "elements": [
            _button("切歌", "kvq:skip", "danger"),
            _button("停止", "kvq:stop", "secondary"),
            _button("队列", "kvq:list", "info"),
        ],
    }


def build_search_cards(
    songs: list[Song], token: str, timeout: int, query: str
) -> list[dict]:
    """一张卡片列出候选：标题 + 点歌按钮 + 封面/时长/UP 主。"""

    modules: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain-text", "content": "🎵 B 站搜索结果"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "kmarkdown",
                    "content": (
                        f"搜索 **{_short(query, 40)}** · "
                        f"{timeout} 秒内点击按钮加入队列，可连续点选多首"
                    ),
                }
            ],
        },
        {"type": "divider"},
    ]

    for index, song in enumerate(songs, start=1):
        modules.append(
            {
                "type": "section",
                "mode": "right",
                "text": {
                    "type": "kmarkdown",
                    "content": f"**({index}) {_short(song.title)}**",
                },
                "accessory": _button("点歌", f"kvp:{token}:{index}", "success"),
            }
        )
        elements: list[dict] = []
        if song.cover:
            elements.append({"type": "image", "src": song.cover, "size": "sm"})
        detail = song.duration_text
        if song.artist:
            detail = f"{detail} · {_short(song.artist, 30)}"
        elements.append({"type": "kmarkdown", "content": detail})
        modules.append({"type": "context", "elements": elements})
        if index < len(songs):
            modules.append({"type": "divider"})

    return [{"type": "card", "theme": "secondary", "size": "lg", "modules": modules}]


def build_now_playing_card(
    song: Song, channel_name: str, queue_left: int
) -> list[dict]:
    now_ms = int(time.time() * 1000) + 1000
    end_ms = now_ms + max(1, int(song.duration)) * 1000

    detail = f"**{_short(song.title)}**"
    if song.artist:
        detail += f"\n{_short(song.artist, 30)}"
    detail += f"\n{song.duration_text}"

    section: dict = {
        "type": "section",
        "mode": "right",
        "text": {"type": "kmarkdown", "content": detail},
    }
    if song.cover:
        section["accessory"] = {"type": "image", "src": song.cover, "size": "sm"}

    return [
        {
            "type": "card",
            "theme": "info",
            "size": "lg",
            "modules": [
                {
                    "type": "header",
                    "text": {"type": "plain-text", "content": "▶ 正在播放"},
                },
                {"type": "divider"},
                section,
                {
                    "type": "countdown",
                    "mode": "second",
                    "startTime": now_ms,
                    "endTime": end_ms,
                },
                _control_group(),
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "kmarkdown",
                            "content": (
                                f"语音频道：**{_short(channel_name, 30)}** · "
                                f"队列还有 **{queue_left}** 首"
                            ),
                        }
                    ],
                },
            ],
        }
    ]


def build_queue_card(
    current: Song | None, queue: list[Song], channel_name: str
) -> list[dict]:
    modules: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain-text", "content": "🎶 播放队列"},
        },
        {"type": "divider"},
    ]

    if current is not None:
        text = f"**▶ 正在播放**\n{_short(current.title)}"
        if current.artist:
            text += f" — {_short(current.artist, 30)}"
        modules.append({"type": "section", "text": {"type": "kmarkdown", "content": text}})
        modules.append({"type": "divider"})

    if queue:
        for index, song in enumerate(queue[:8], start=1):
            text = f"**{index}. {_short(song.title)}**"
            if song.artist:
                text += f" — {_short(song.artist, 30)}"
            modules.append(
                {"type": "section", "text": {"type": "kmarkdown", "content": text}}
            )
            modules.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "kmarkdown", "content": f"时长 {song.duration_text}"}
                    ],
                }
            )
            modules.append({"type": "divider"})
        if len(queue) > 8:
            modules.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "kmarkdown",
                            "content": f"还有 {len(queue) - 8} 首未显示",
                        }
                    ],
                }
            )
    else:
        modules.append(
            {
                "type": "context",
                "elements": [{"type": "kmarkdown", "content": "队列是空的"}],
            }
        )

    modules.append(_control_group())
    modules.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "kmarkdown",
                    "content": f"语音频道：**{_short(channel_name, 30)}**",
                }
            ],
        }
    )
    return [{"type": "card", "theme": "secondary", "size": "lg", "modules": modules}]
