# 战舰世界随机选船插件 🚢

> 一个 AstrBot 插件，为战舰世界（World of Warships）群聊提供随机选船和自动对窝批作出反应。

***

## ✨ 功能

### 🚢 随机选船

从 Wargaming 官方 API 拉取全量船表，按类型、国家、等级筛选后随机选一艘，附带：

- 战舰大图
- AI 锐评

支持灵活的筛选语法：

```
random                 → 全随机
random bb              → 随机战列舰
random dd jp           → 随机日驱
random 10              → 随机 X 级
random bb us 10        → 随机美系 X 级战列舰
random 10 us bb        → 乱序也能识别
random nations         → 查看可用国家及别名
```

所有筛选条件可选、可乱序，不填则忽略。

### 💬 自动回复窝批

群友发送配置的关键词（默认 `wws me recent`）时，触发 AI 生成的「窝批」变体回复。

***

## 📦 安装

1. 将 `astrbot_plugin_wws_me_recent/` 文件夹放入 AstrBot 的 `data/plugins/` 目录
2. 在 WebUI 插件管理页重载插件
3. 在 WebUI 配置以下必填项：

### 必填配置

| 字段                 | 说明                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------- |
| `wargaming_app_id` | Wargaming API 密钥，前往 [developers.wargaming.net](https://developers.wargaming.net) 注册免费获取 |

### 可选配置

| 字段                                  | 默认值                 | 说明                   |
| ----------------------------------- | ------------------- | -------------------- |
| `random_trigger`                    | `random`            | 随机选船触发词              |
| `trigger_keyword`                   | `["wws me recent"]` | 窝批调侃触发关键词列表          |
| `enable_whitelist`                  | `false`             | 群聊白名单开关              |
| `whitelist_groups`                  | `[]`                | 白名单群号列表              |
| `reply_probability`                 | `100`               | 回复概率（%）              |
| `ship_name_language`                | `zh-cn`             | 船名语言（`en` / `zh-cn`） |
| `ship_cache_ttl_days`               | `3`                 | 船表缓存有效期（天）           |
| `ship_cache_retry_interval_minutes` | `5`                 | API 拉取失败重试间隔（分钟）     |
| `nation_aliases`                    | 见默认值                | 各国别名配置（JSON 格式）      |
| `reply_prompt`                      | 见默认值                | 窝批调教版 AI 提示词         |
| `ship_reply_prompt`                 | 见默认值                | 选船调教版 AI 提示词         |

***

## 🗺️ 国家别名配置

`nation_aliases` 是一个 JSON 字符串，定义每个国家可用的触发别名。默认值：

```json
{
  "usa": "美,美国",
  "japan": "日,日本",
  "germany": "德,德国",
  "uk": "英,英国",
  "france": "法,法国",
  "italy": "意,意大利",
  "ussr": "俄,苏,苏联",
  "pan_asia": "泛亚",
  "europe": "欧,泛欧",
  "commonwealth": "联邦",
  "netherlands": "荷,荷兰",
  "spain": "西,西班牙",
  "pan_america": "泛美"
}
```

你可以在 WebUI 中修改，增删别名。使用 `random nations` 可查看当前生效的别名列表。

***

## 🧠 数据源

- **船表数据**：通过 [Wargaming 官方 API](https://developers.wargaming.net) 获取
- **首次加载**：插件启动时自动拉取全量船表（分页处理，约 600+ 艘）
- **本地缓存**：保存为 `data/temp/wws_ships.json`，仅含需要的字段
- **自动刷新**：超过 `ship_cache_ttl_days` 天后重载插件时自动后台更新
- **断网重试**：拉取失败按 `ship_cache_retry_interval_minutes` 间隔重试，直到成功
- **运行时降级**：刷新期间仍可使用旧缓存数据

***

## 🤝 参与贡献

欢迎提交 Issue 反馈 bug 或提出新功能建议，也欢迎 Pull Request 参与改进！

***

## 📄 许可证

MIT
