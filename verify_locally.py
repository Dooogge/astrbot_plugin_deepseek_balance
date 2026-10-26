"""离线验证脚本：不启动 AstrBot，用桩模块加载插件并跑通命令逻辑。

用法：
    python verify_locally.py [--key sk-xxx]

做的事：
1. 用假的 astrbot.* 模块满足插件的 import；
2. 用 httpx 的替身真正请求 DeepSeek 余额接口；
3. 模拟一条消息事件，跑一遍 /余额 的完整处理链路；
4. 断言输出里包含余额信息。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "main.py"


# --------------------------------------------------------------------- httpx 替身
class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status_code = status
        self._body = body
        self.text = body

    def json(self):
        return json.loads(self._body)


class HTTPError(Exception):
    pass


class TimeoutException(HTTPError):
    pass


class _AsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str, headers: dict | None = None):
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout or 15) as resp:
                return _Response(resp.status, resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return _Response(exc.code, exc.read().decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise HTTPError(str(exc)) from exc


def install_stubs() -> None:
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = _AsyncClient
    httpx.HTTPError = HTTPError
    httpx.TimeoutException = TimeoutException
    sys.modules["httpx"] = httpx

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class AstrBotConfig(dict):
        pass

    class _Logger:
        def debug(self, msg, *a, **k):
            pass

        def info(self, msg, *a, **k):
            pass

        def warning(self, msg, *a, **k):
            print(f"[warn] {msg}")

        error = warning

    api.AstrBotConfig = AstrBotConfig
    api.logger = _Logger()

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class _Filter:
        class EventMessageType:
            ALL = "ALL"

        @staticmethod
        def command(name):
            def deco(func):
                func._command = name
                return func

            return deco

        @staticmethod
        def event_message_type(kind):
            def deco(func):
                func._event_type = kind
                return func

            return deco

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = _Filter()

    star_mod = types.ModuleType("astrbot.api.star")

    class Context:
        def __init__(self, config: dict) -> None:
            self._config = config

        def get_config(self, *a, **k):
            return self._config

    class Star:
        def __init__(self, context) -> None:
            self.context = context

    star_mod.Context = Context
    star_mod.Star = Star

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod


# --------------------------------------------------------------------- 假事件
class FakeEvent:
    def __init__(self, text: str, sender: str = "3037706217", admin: bool = True) -> None:
        self.message_str = text
        self._sender = sender
        self._admin = admin
        self.replies: list[str] = []

    def plain_result(self, text: str):
        self.replies.append(text)
        return text

    def get_sender_id(self):
        return self._sender

    def is_admin(self):
        return self._admin


async def run_command(plugin, event: FakeEvent) -> None:
    arg = event.message_str.split(maxsplit=1)
    arg = arg[1] if len(arg) > 1 else ""
    async for _ in plugin._handle(event, arg):
        pass


def main() -> int:
    key = ""
    if "--key" in sys.argv:
        key = sys.argv[sys.argv.index("--key") + 1]

    install_stubs()
    spec = importlib.util.spec_from_file_location("ds_balance_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # 同时验证 import 与语法

    astrbot_cfg = {"provider_sources": [], "admins_id": ["3037706217"]}
    if key:
        astrbot_cfg["provider_sources"] = [
            {"provider": "deepseek", "api_base": "https://api.deepseek.com/v1", "key": [key]}
        ]

    plugin = module.DeepSeekBalance(
        types.SimpleNamespace(get_config=lambda *a, **k: astrbot_cfg),
        {"admin_only": True, "daily_cost": 0.5, "cache_seconds": 0, "timeout": 15},
    )

    print("=== 1) 普通查询 ===")
    ev = FakeEvent("/余额")
    asyncio.run(run_command(plugin, ev))
    print(ev.replies[-1])

    print("\n=== 2) 详细模式 ===")
    ev2 = FakeEvent("/余额 详细")
    asyncio.run(run_command(plugin, ev2))
    print(ev2.replies[-1])

    print("\n=== 3) 非管理员 ===")
    ev3 = FakeEvent("/余额", sender="10000", admin=False)
    asyncio.run(run_command(plugin, ev3))
    print(ev3.replies[-1])

    print("\n=== 4) 没有 Key ===")
    plugin_nokey = module.DeepSeekBalance(
        types.SimpleNamespace(get_config=lambda *a, **k: {"provider_sources": []}),
        {"admin_only": False},
    )
    ev4 = FakeEvent("/余额")
    asyncio.run(run_command(plugin_nokey, ev4))
    print(ev4.replies[-1])

    assert "¥" in ev.replies[-1], "普通查询没有返回金额"
    assert "天" in ev.replies[-1] or "见底" in ev.replies[-1], "普通查询没有返回可撑时间"
    assert "充值" in ev2.replies[-1], "详细模式缺少充值明细"
    assert "管理员" in ev3.replies[-1], "非管理员没有被拦截"
    assert "饭票" in ev4.replies[-1], "无 Key 时没有给出提示"
    print("\n全部断言通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
