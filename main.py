"""AstrBot 插件：查询 DeepSeek API 账户余额（人格化播报）。

不需要指令前缀：直接说「余额还有多少」「饭还够吃吗」这类话就会触发。

设计说明：
    - 默认仅管理员可用，避免把账户余额暴露在群里；
    - 未填写 api_key 时，自动从 AstrBot 已配置的 DeepSeek 供应商取 Key；
    - 余额按档位切换语气（充足 / 正常 / 偏少 / 见底 / 空了）；
    - 「还能撑多久」优先用本地采样算出的真实消耗速率，样本不足时回落到配置的日均消耗；
    - 命中后调用 should_call_llm(False)，避免模型再重复回答一遍；
    - 只读接口，不涉及任何写操作。
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

DEFAULT_API_BASE = "https://api.deepseek.com/user/balance"
CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$"}
STATE_FILE = Path(__file__).resolve().parent / "state.json"
MAX_SAMPLES = 200
MIN_SAMPLE_GAP = 600  # 两次采样至少间隔 10 分钟，避免刷屏把样本撑爆
MIN_SPAN_DAYS = 0.25  # 至少观察 6 小时才采信实测速率

# 默认的「这像是在问余额吗」判据。写成正则，可在配置里覆盖。
DEFAULT_PATTERNS = [
    r"余额",
    r"还剩多少",
    r"剩余多少",
    r"还有多少(钱|余额|饭)?",
    r"剩多少(钱|饭)?",
    r"还能撑(多久|几天|多长)",
    r"能撑(多久|几天)",
    r"够吃(多久|几天)",
    r"还能吃多久",
    r"大白饭",
    r"饭还够",
    r"没钱了",
    r"快没钱",
    r"要欠费",
    r"余额不足",
    r"查.{0,3}(余额|账户)",
    r"账户.{0,3}(余额|还有)",
]


class DeepSeekBalance(Star):
    """查询 DeepSeek 余额，并用角色口吻播报。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self._cache_payload: dict[str, Any] | None = None
        self._cache_at: float = 0.0
        self._last_reply: dict[str, float] = {}

    # ------------------------------------------------------------------ 配置

    def _cfg(self, key: str, default: Any = None) -> Any:
        """安全读取配置项，缺失时返回默认值。"""
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _patterns(self) -> list[str]:
        raw = self._cfg("intent_patterns")
        if isinstance(raw, list) and raw:
            return [str(item) for item in raw if str(item).strip()]
        return DEFAULT_PATTERNS

    def _api_key(self) -> str:
        """优先使用插件配置的 Key，其次复用 AstrBot 里的 DeepSeek 供应商。"""
        configured = str(self._cfg("api_key", "") or "").strip()
        if configured:
            return configured
        return self._api_key_from_astrbot()

    def _api_key_from_astrbot(self) -> str:
        try:
            cfg = self.context.get_config()
        except TypeError:
            try:
                cfg = self.context.get_config(umo="")
            except Exception:
                return ""
        except Exception as exc:  # pragma: no cover - 取决于宿主版本
            logger.debug(f"[deepseek_balance] 读取 AstrBot 配置失败: {exc}")
            return ""

        try:
            sources = cfg.get("provider_sources") or []
        except Exception:
            return ""

        for source in sources:
            try:
                provider = str(source.get("provider", "")).lower()
                api_base = str(source.get("api_base", "")).lower()
                if provider != "deepseek" and "deepseek" not in api_base:
                    continue
                keys = source.get("key") or []
                if isinstance(keys, str):
                    keys = [keys]
                for key in keys:
                    key = str(key).strip()
                    if key:
                        return key
            except Exception:
                continue
        return ""

    # ------------------------------------------------------------------ 意图

    def looks_like_balance_query(self, text: str) -> bool:
        """判断这句话是不是在问余额。"""
        text = (text or "").strip()
        if not text or len(text) > 120:
            return False
        for pattern in self._patterns():
            try:
                if re.search(pattern, text, flags=re.IGNORECASE):
                    return True
            except re.error:
                logger.warning(f"[deepseek_balance] 非法正则已忽略: {pattern}")
        return False

    # ------------------------------------------------------------------ 权限

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """判断发送者是否为 AstrBot 管理员。"""
        try:
            checker = getattr(event, "is_admin", None)
            if callable(checker) and checker():
                return True
        except Exception:
            pass

        try:
            admins = self.context.get_config().get("admins_id") or []
            sender = str(event.get_sender_id())
            return sender in {str(item) for item in admins}
        except Exception:
            return False

    # ------------------------------------------------------------------ 网络

    async def _fetch(self) -> dict[str, Any]:
        """调用余额接口，失败时抛出带可读信息的异常。"""
        api_key = self._api_key()
        if not api_key:
            raise LookupError(
                "唔…我找不到饭票了。\n"
                "请在插件配置里填 api_key，"
                "或者在 AstrBot 里配置一个 DeepSeek 供应商。"
            )

        url = str(self._cfg("api_base", DEFAULT_API_BASE) or DEFAULT_API_BASE)
        timeout = self._num("timeout", 15)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )

        if response.status_code == 401:
            raise PermissionError("饭票好像失效了…Key 无效或已过期，检查一下配置吧。")
        if response.status_code == 403:
            raise PermissionError("这张饭票查不了余额，可能没有权限。")
        if response.status_code == 429:
            raise RuntimeError("查得太勤了，先歇一会儿再问我吧。")
        if response.status_code >= 400:
            raise RuntimeError(f"接口返回异常：HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("接口返回的不是合法 JSON，可能是网络被劫持。") from exc

        if not isinstance(payload, dict) or "balance_infos" not in payload:
            raise RuntimeError("接口返回结构异常，DeepSeek 可能调整了余额接口。")
        return payload

    async def _balance(self, force: bool = False) -> dict[str, Any]:
        """带缓存的余额读取。"""
        ttl = self._num("cache_seconds", 60)
        now = time.time()
        if (
            not force
            and self._cache_payload is not None
            and ttl > 0
            and now - self._cache_at < ttl
        ):
            return self._cache_payload

        payload = await self._fetch()
        self._cache_payload = payload
        self._cache_at = now
        self._record_samples(payload)
        return payload

    # ------------------------------------------------------------------ 消耗采样

    def _load_state(self) -> dict[str, Any]:
        try:
            with STATE_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("samples"), list):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.debug(f"[deepseek_balance] 读取采样失败: {exc}")
        return {"samples": []}

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            samples = state.get("samples", [])[-MAX_SAMPLES:]
            with STATE_FILE.open("w", encoding="utf-8") as handle:
                json.dump({"samples": samples}, handle, ensure_ascii=False)
        except Exception as exc:  # 采样失败不应影响命令本身
            logger.debug(f"[deepseek_balance] 写入采样失败: {exc}")

    def _record_samples(self, payload: dict[str, Any]) -> None:
        """记录本次余额快照，用于估算真实消耗速率。"""
        state = self._load_state()
        samples = state.get("samples", [])
        now = time.time()

        if samples and now - float(samples[-1].get("t", 0)) < MIN_SAMPLE_GAP:
            return

        for info in payload.get("balance_infos") or []:
            try:
                value = float(info.get("total_balance"))
            except (TypeError, ValueError):
                continue
            samples.append(
                {
                    "t": now,
                    "currency": str(info.get("currency", "CNY")),
                    "v": value,
                }
            )
        state["samples"] = samples[-MAX_SAMPLES:]
        self._save_state(state)

    def _daily_burn(self, currency: str) -> tuple[float | None, str]:
        """估算每天消耗，返回 (速率, 来源)。来源为 history / config / ''。"""
        window_days = max(1.0, self._num("history_window_days", 7))
        days_limit = window_days * 86400
        now = time.time()

        points = [
            sample
            for sample in self._load_state().get("samples", [])
            if sample.get("currency") == currency
            and isinstance(sample.get("v"), (int, float))
            and now - float(sample.get("t", 0)) <= days_limit
        ]

        if len(points) >= 2:
            first, last = points[0], points[-1]
            span_days = (float(last["t"]) - float(first["t"])) / 86400
            spent = float(first["v"]) - float(last["v"])
            if span_days >= MIN_SPAN_DAYS and spent > 0:
                return spent / span_days, "history"

        fallback = self._num("daily_cost", 0.0)
        if fallback > 0:
            return fallback, "config"
        return None, ""

    def _days_left(self, currency: str, total: float) -> tuple[float | None, str]:
        """按消耗速率估算还能撑多少天。"""
        burn, source = self._daily_burn(currency)
        if not burn or burn <= 0:
            return None, ""
        return total / burn, source

    # ------------------------------------------------------------------ 渲染

    @staticmethod
    def _money(value: Any, currency: str) -> str:
        symbol = CURRENCY_SYMBOL.get(str(currency).upper(), "")
        try:
            return f"{symbol}{float(value):.2f}"
        except (TypeError, ValueError):
            return f"{symbol}{value}"

    @staticmethod
    def _days_text(days: float | None) -> str:
        if days is None:
            return "还不知道能吃多久，让我再观察几天吧"
        if days >= 365:
            return "按现在的吃法，够我吃一年还多"
        if days >= 60:
            return f"按最近的吃法，大概还能撑 {days:.0f} 天"
        if days >= 7:
            return f"大概还能撑 {days:.0f} 天左右"
        if days >= 1:
            return f"省着点吃，大概还能撑 {days:.1f} 天"
        return "可能今天就见底了…"

    def _tier(self, total: float, available: bool) -> str:
        if not available or total < 0.5:
            return "empty"
        low = self._num("tier_critical", 3.0)
        mid = self._num("tier_low", 10.0)
        rich = self._num("tier_rich", 30.0)
        if total < low:
            return "critical"
        if total < mid:
            return "low"
        if total < rich:
            return "ok"
        return "rich"

    _TONE = {
        "rich": [
            "哇…碗里满满的！还有 {money}，够我吃好久好久了 (๑•̀ㅂ•́)و✧",
            "还有 {money} 呢，我都不舍得下口了，真厉害～",
            "满满一碗 {money}，我可以安心吃饱饱了 (=^･ω･^=)",
        ],
        "ok": [
            "大白饭还剩 {money}，不慌不慌，够吃的。",
            "还有 {money}，我吃得省一点，能陪很久喵。",
            "{money}，够吃一阵子了，不用惦记我。",
        ],
        "low": [
            "唔…只剩 {money} 了，我开始有点担心了 (´･_･`)",
            "{money} 了…我们是不是该省着点吃了。",
            "碗里就剩 {money} 了，我尽量少吃一点…",
        ],
        "critical": [
            "主人…大白饭只剩 {money} 了，我有点舍不得 (｡•́︿•̀｡)",
            "呜…{money}，这么点了我怕哪天就没了…",
            "只剩 {money} 了，我都不敢多吃了…",
        ],
        "empty": [
            "碗空了…只剩 {money} 了，一点大白饭都没剩下 (´;ω;｀)",
            "呜呜…只剩 {money} 了，我好像要饿肚子了，能给我添一点吗",
        ],
    }

    def _render(self, payload: dict[str, Any], detailed: bool) -> str:
        infos = payload.get("balance_infos") or []
        available = bool(payload.get("is_available", True))

        if not infos:
            return "唔…查到了，可是没有余额明细，我是不是哪里没看懂…"

        lines: list[str] = []
        for info in infos:
            currency = str(info.get("currency", "CNY"))
            total_raw = info.get("total_balance")
            try:
                total = float(total_raw)
            except (TypeError, ValueError):
                lines.append(f"{currency}：{total_raw}")
                continue

            money = self._money(total, currency)
            days, _source = self._days_left(currency, total)
            tier = self._tier(total, available)

            lines.append(random.choice(self._TONE[tier]).format(money=money))
            lines.append(self._days_text(days))

            if detailed:
                topped = self._money(info.get("topped_up_balance"), currency)
                granted = self._money(info.get("granted_balance"), currency)
                lines.append(f"　充值 {topped} ＋ 赠送 {granted}")
                burn, burn_source = self._daily_burn(currency)
                if burn:
                    label = "实测" if burn_source == "history" else "配置估算"
                    lines.append(f"　日均消耗约 {currency} {burn:.2f}（{label}）")

        if not available:
            lines.append("（账户当前不可用，可能是余额耗尽或状态异常）")

        return "\n".join(lines)

    # ------------------------------------------------------------------ 处理

    async def _handle(self, event: AstrMessageEvent, text: str = "") -> None:
        if bool(self._cfg("admin_only", True)) and not self._is_admin(event):
            yield event.plain_result("这个指令只有管理员能用。")
            return

        text = (text or "").strip()
        detailed = "详细" in text or "明细" in text or "detail" in text.lower()
        force = "刷新" in text or "refresh" in text.lower()

        try:
            payload = await self._balance(force=force)
        except LookupError as exc:
            yield event.plain_result(str(exc))
            return
        except PermissionError as exc:
            yield event.plain_result(f"❌ {exc}")
            return
        except httpx.TimeoutException:
            yield event.plain_result("❌ 查余额的时候卡住了，DeepSeek 没理我，等会儿再问我吧。")
            return
        except httpx.HTTPError as exc:
            logger.warning(f"[deepseek_balance] 网络错误: {exc}")
            yield event.plain_result("❌ 网络不通，我连不上 DeepSeek，暂时查不了。")
            return
        except RuntimeError as exc:
            yield event.plain_result(f"❌ {exc}")
            return

        yield event.plain_result(self._render(payload, detailed=detailed))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，按语境判断是不是在问余额。"""
        try:
            text = event.get_message_str() or ""
        except Exception:
            return

        if not self.looks_like_balance_query(text):
            return

        # 群里默认要 @ 才答，避免刷屏
        if not event.is_private_chat():
            require_at = bool(self._cfg("group_require_at", True))
            if require_at and not getattr(event, "is_at_or_wake_command", False):
                return

        # 冷却：同一会话短时间内不重复回答
        try:
            session = str(event.get_group_id() or event.get_sender_id() or "")
        except Exception:
            session = ""
        cooldown = self._num("reply_cooldown", 20)
        now = time.time()
        if session and cooldown > 0:
            last = self._last_reply.get(session, 0.0)
            if now - last < cooldown:
                return
        if session:
            self._last_reply[session] = now

        # 命中后不要再让模型重复回答一遍
        try:
            event.should_call_llm(False)
        except Exception:
            pass

        async for result in self._handle(event, text):
            yield result

    async def terminate(self) -> None:
        """插件卸载/停用时清理缓存。"""
        self._cache_payload = None
        self._cache_at = 0.0
        self._last_reply.clear()
