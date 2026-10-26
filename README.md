# 大白饭余额 · astrbot_plugin_deepseek_balance

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：**不用打指令**，随口问一句「余额还有多少」「饭还够吃吗」，机器人就用自己的人设播报 DeepSeek 账户余额，还会估算这些钱还能撑几天；余额越少，语气越难过。

> 插件市场里显示的名字是 **大白饭余额**（`metadata.yaml` 的 `display_name`）；仓库名和目录名保持 `astrbot_plugin_deepseek_balance`，因为 AstrBot 要求 `name` 必须是合法的 Python 模块名，不能写中文。

![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D%204.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

## 效果

```
你：余额还有多少
它：还有 ¥50.00 呢，我都不舍得下口了，真厉害～
    按最近的吃法，大概还能撑 100 天

你：饭还够吃吗
它：大白饭还剩 ¥20.00，不慌不慌，够吃的。
    大概还能撑 40 天左右

你：是不是快没钱了
它：唔…只剩 ¥5.00 了，我开始有点担心了 (´･_･`)
    大概还能撑 10 天左右

你：大白饭还剩多少
它：主人…大白饭只剩 ¥1.00 了，我有点舍不得 (｡•́︿•̀｡)
    省着点吃，大概还能撑 2.0 天
```

## 功能

- **无指令触发**：不依赖 `/` 前缀，按语境判断是不是在问余额
- **人格化播报**：五档语气（充足 / 正常 / 偏少 / 见底 / 空了），每档多套台词随机切换
- **还能撑几天**：优先用本地采样算出的**真实消耗速率**，样本不足时回落到配置的日均消耗
- **不重复回答**：命中后关闭本轮 LLM 调用，避免插件和模型各回一遍
- **群里防刷屏**：默认只有被 @ 才回答，同一会话还有冷却时间
- **多币种**：接口返回几种就播报几种（CNY / USD）
- **零配置复用**：没填 Key 时自动读取 AstrBot 已配置的 DeepSeek 供应商
- **只读**：只调用查询接口，不会改动账户任何状态

## 安装

### 方式一：WebUI 上传（推荐）

1. 下载本仓库的 zip 包（Code → Download ZIP），或从 Releases 下载
2. 打开 AstrBot 管理面板 → **插件** → **安装插件** → 上传 zip
3. 重载插件或重启 AstrBot

> zip 解压后目录名必须是 `astrbot_plugin_deepseek_balance`，与 `metadata.yaml` 的 `name` 一致。

### 方式二：手动放入目录

```bash
cd AstrBot/data/plugins
git clone https://github.com/Dooogge/astrbot_plugin_deepseek_balance.git
```

然后重启 AstrBot。

## 怎么用

直接说话就行，不需要任何前缀：

| 你说 | 结果 |
| --- | --- |
| 余额还有多少 | 播报余额 + 还能撑几天 |
| 饭还够吃吗 / 大白饭剩多少 | 同上 |
| 还能撑多久 / 够吃几天 | 同上 |
| 是不是快没钱了 / 要欠费了 | 同上 |
| **余额详细** | 追加充值 / 赠送余额与日均消耗 |
| **刷新余额** | 跳过缓存，实时查询 |

## 语境判断是怎么做的

插件监听所有消息，用一组正则判断「这句话是不是在问余额」：

```
命中任意一条 → 认为是余额提问
没命中 → 直接放行，不影响正常聊天
```

默认覆盖这些说法：`余额`、`还剩多少`、`还有多少(钱|余额|饭)`、`还能撑(多久|几天)`、`够吃几天`、`大白饭`、`饭还够`、`没钱了`、`余额不足`、`查余额` 等 17 条。

**已知的取舍**：这是规则匹配不是语义理解，所以「余额宝收益怎么样」也会触发（因为含「余额」）。想要真正按语义判断，有两种改法：

1. 在配置里把 `intent_patterns` 调窄，只留最常用的几种说法
2. 改成 LLM 工具调用模式（让模型自己决定要不要查），需要的话可以另开一版

## 配置

在 **管理面板 → 插件 → 本插件 → 配置** 中设置，或直接编辑 `data/config/astrbot_plugin_deepseek_balance_config.json`。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_key` | string（加密显示） | 空 | 留空时自动复用 AstrBot 的 DeepSeek 供应商 Key |
| `api_base` | string | `https://api.deepseek.com/user/balance` | 余额接口地址 |
| `admin_only` | bool | `true` | 仅管理员可用 |
| `intent_patterns` | list | 17 条正则 | 触发判断用的正则列表 |
| `group_require_at` | bool | `true` | 群里需要 @ 才回答 |
| `reply_cooldown` | int | `20` | 同一会话冷却秒数 |
| `cache_seconds` | int | `60` | 结果缓存秒数，0 表示不缓存 |
| `timeout` | int | `15` | 请求超时秒数 |
| `daily_cost` | float | `0.5` | 日均消耗兜底值 |
| `history_window_days` | int | `7` | 采样统计窗口（天） |
| `tier_rich` | float | `30.0` | 高于此值用「充足」语气 |
| `tier_low` | float | `10.0` | 低于此值用「偏少」语气 |
| `tier_critical` | float | `3.0` | 低于此值用「见底」语气；低于 0.5 元进入「空了」 |

## 「还能撑几天」是怎么算的

插件每次成功查询都会在插件目录写一个 `state.json`，记录 `(时间, 余额)` 快照：

- 同一币种至少 2 个样本、跨度超过 6 小时、余额确实下降过 → 用 **(首样本 − 末样本) ÷ 天数** 算真实日均消耗
- 条件不满足 → 回落到配置里的 `daily_cost`
- 10 分钟内的重复查询不记录；只保留最近 200 条；统计窗口默认 7 天

所以**装上的头一天是按 `daily_cost` 估的**，用两三天后自动切成实测值。

## 群里会不会刷屏

默认有三重限制：

1. **必须 @ 机器人**（`group_require_at: true`）
2. **同一会话 20 秒冷却**（`reply_cooldown`）
3. **命中后关闭本轮 LLM**，不会出现插件和模型各回一遍的情况

私聊不受 @ 限制，但仍然有冷却。

## 常见问题

**Q：说了「余额还有多少」却没反应？**
A：先确认发消息的号在 AstrBot 的 `admins_id` 里（默认仅管理员可用）；群里还要 @ 机器人。

**Q：提示“我找不到饭票了”？**
A：在插件配置里填 `api_key`，或在 AstrBot 里配好一个 DeepSeek 供应商。

**Q：提示“饭票好像失效了”？**
A：去 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 确认 Key 还能用。

**Q：天数和官网对不上？**
A：天数是插件按消耗速率估算的，不是官方数据。详细模式下能看到是「实测」还是「配置估算」。

**Q：想改口气？**
A：改 `main.py` 里的 `_TONE` 字典，五档各写几句，`{money}` 会被替换成金额。

## 开发

```bash
python -m py_compile main.py

# 语境判断、语气档位、消耗估算（离线，不联网）
python verify_tiers.py

# 真实接口联调（需要一把可用的 Key）
python verify_locally.py --key sk-xxxxxxxx
```

无额外第三方依赖：`httpx` 由 AstrBot 自带。

## 免责声明

本插件为第三方作品，与 DeepSeek 官方、AstrBot 官方均无隶属关系。余额接口由 DeepSeek 提供，若其变更或下线，插件可能失效。

## License

[MIT](LICENSE)
