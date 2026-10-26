"""语境判断、语气档位与「还能撑几天」的离线验证，不联网。

用法：
    python verify_tiers.py
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "main.py"


def install_stubs() -> None:
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = object
    httpx.HTTPError = type("HTTPError", (Exception,), {})
    httpx.TimeoutException = type("TimeoutException", (httpx.HTTPError,), {})
    sys.modules["httpx"] = httpx

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class AstrBotConfig(dict):
        pass

    class _Logger:
        def debug(self, *a, **k):
            pass

        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            print("[warn]", *a)

        error = warning

    api.AstrBotConfig = AstrBotConfig
    api.logger = _Logger()

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class _Filter:
        class EventMessageType:
            ALL = "ALL"
            GROUP_MESSAGE = "GROUP"
            PRIVATE_MESSAGE = "PRIVATE"

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
        def __init__(self, config):
            self._config = config

        def get_config(self, *a, **k):
            return self._config

    class Star:
        def __init__(self, context):
            self.context = context

    star_mod.Context = Context
    star_mod.Star = Star

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod


def payload(total: float, *, available: bool = True) -> dict:
    return {
        "is_available": available,
        "balance_infos": [
            {
                "currency": "CNY",
                "total_balance": f"{total:.2f}",
                "topped_up_balance": f"{total:.2f}",
                "granted_balance": "0.00",
            }
        ],
    }


def main() -> int:
    install_stubs()
    spec = importlib.util.spec_from_file_location("ds_balance_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tmpdir = Path(tempfile.mkdtemp())
    module.STATE_FILE = tmpdir / "state.json"

    cfg = {
        "admin_only": True,
        "daily_cost": 0.5,
        "history_window_days": 7,
        "tier_rich": 30,
        "tier_low": 10,
        "tier_critical": 3,
        "cache_seconds": 0,
        "reply_cooldown": 0,
    }
    ctx = types.SimpleNamespace(get_config=lambda *a, **k: {"provider_sources": [], "admins_id": []})
    plugin = module.DeepSeekBalance(ctx, cfg)

    print("=== 1) 语境判断：应该触发 ===")
    positives = [
        "余额还有多少",
        "饭还够吃吗",
        "大白饭剩多少了",
        "还能撑多久",
        "是不是快没钱了",
        "帮我查一下余额",
        "账户还有多少钱",
        "剩多少钱呀",
        "我是不是要欠费了",
    ]
    for text in positives:
        hit = plugin.looks_like_balance_query(text)
        print(f"  {'✓' if hit else '✗'} {text}")
        assert hit, f"应当触发却没有：{text}"

    print("\n=== 2) 语境判断：不应该触发 ===")
    negatives = [
        "今天吃什么",
        "帮我写个 Python 脚本",
        "余额宝收益怎么样",  # 含“余额”但是另一件事，属于可接受的误判风险
        "你好呀",
        "我要去睡觉了",
        "这个 bug 怎么修",
    ]
    # “余额宝”会命中关键字，这是已知的取舍，单独说明
    expected_miss = [n for n in negatives if "余额宝" not in n]
    for text in negatives:
        hit = plugin.looks_like_balance_query(text)
        mark = "✓" if not hit else "·"
        print(f"  {mark} {'触发' if hit else '不触发'}  {text}")
    for text in expected_miss:
        assert not plugin.looks_like_balance_query(text), f"不应当触发却触发了：{text}"

    print("\n=== 3) 档位判定 ===")
    for amount, expect in [(50, "rich"), (20, "ok"), (5, "low"), (1, "critical"), (0.2, "empty")]:
        got = plugin._tier(amount, True)
        print(f"  {amount:>6} 元 -> {got}")
        assert got == expect

    print("\n=== 4) 五档输出（不含时间与署名）===")
    random.seed(7)
    for amount in (50, 20, 5, 1, 0.2):
        text = plugin._render(payload(amount), detailed=False)
        print(f"\n--- {amount} 元 ---")
        print(text)
        assert f"{amount:.2f}" in text, "输出里没有金额"
        assert "—" not in text, "仍然带着署名/分隔线"
        assert "大肥鱼" not in text, "仍然带着署名"
        assert ":" not in text, "仍然带着时间"

    print("\n=== 5) 详细模式 ===")
    detail = plugin._render(payload(8.0), detailed=True)
    print(detail)
    assert "日均消耗" in detail and "配置估算" in detail

    print("\n=== 6) 实测消耗速率（2 天掉了 1 元）===")
    now = time.time()
    module.STATE_FILE.write_text(
        json.dumps(
            {
                "samples": [
                    {"t": now - 2 * 86400, "currency": "CNY", "v": 10.0},
                    {"t": now, "currency": "CNY", "v": 9.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    burn, source = plugin._daily_burn("CNY")
    days, _ = plugin._days_left("CNY", 9.0)
    print(f"  日均消耗 {burn:.2f} 元，来源={source}，9 元可撑 {days:.1f} 天")
    assert source == "history" and abs(burn - 0.5) < 1e-6 and abs(days - 18) < 1e-6

    print("\n=== 7) 余额没变化时回落到配置值 ===")
    module.STATE_FILE.write_text(
        json.dumps(
            {
                "samples": [
                    {"t": now - 2 * 86400, "currency": "CNY", "v": 9.0},
                    {"t": now, "currency": "CNY", "v": 9.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    burn2, source2 = plugin._daily_burn("CNY")
    print(f"  回落到 {burn2}（来源 {source2}）")
    assert source2 == "config"

    print("\n=== 8) 采样去重 ===")
    module.STATE_FILE.unlink(missing_ok=True)
    plugin._record_samples(payload(9.0))
    plugin._record_samples(payload(8.0))
    stored = json.loads(module.STATE_FILE.read_text(encoding="utf-8"))["samples"]
    print(f"  连续两次调用后样本数：{len(stored)}（应为 1）")
    assert len(stored) == 1

    print("\n全部断言通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
