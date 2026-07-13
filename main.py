from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event.filter import EventMessageType
import astrbot.api.message_components as Comp
import random, os, time, json, asyncio
import aiohttp

# 内置舰种别名映射（value 为 WG API 字段名），用户可通过 ship_type_aliases 配置覆盖/追加
SHIP_TYPES = {
    "bb": "Battleship", "battleship": "Battleship", "战列舰": "Battleship",
    "ca": "Cruiser", "cruiser": "Cruiser", "cl": "Cruiser", "巡洋舰": "Cruiser",
    "dd": "Destroyer", "destroyer": "Destroyer", "驱逐舰": "Destroyer",
    "cv": "AirCarrier", "aircarrier": "AirCarrier", "空母": "AirCarrier",
    "ss": "Submarine", "submarine": "Submarine", "潜艇": "Submarine",
}

# 内置国家别名映射（value 为 WG API 字段名），用户可通过 nation_aliases 配置覆盖/追加
NATIONS = {
    "us": "usa", "usa": "usa", "美": "usa", "美系": "usa",
    "jp": "japan", "japan": "japan", "日": "japan", "日系": "japan",
    "de": "germany", "germany": "germany", "德": "germany", "德系": "germany",
    "uk": "uk", "英": "uk", "英系": "uk", "british": "uk",
    "fr": "france", "france": "france", "法": "france", "法系": "france",
    "it": "italy", "italy": "italy", "意": "italy", "意系": "italy",
    "ussr": "ussr", "俄": "ussr", "苏": "ussr", "苏系": "ussr", "russia": "ussr",
    "pan_asia": "pan_asia", "泛亚": "pan_asia",
    "pan_america": "pan_america", "泛美": "pan_america",
    "eu": "europe", "europe": "europe", "欧": "europe", "欧洲": "europe", "泛欧": "europe",
    "commonwealth": "commonwealth", "联邦": "commonwealth",
    "nl": "netherlands", "netherlands": "netherlands", "荷": "netherlands", "荷兰": "netherlands",
    "sp": "spain", "spain": "spain", "西": "spain", "西班牙": "spain",
}

# 罗马数字 → 等级映射表（大小写不敏感，token 已统一 lower），保留字禁止用作舰种/国家别名
ROMAN_TO_TIER = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11,
}


class WwsMeRecentPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._ships_cache: list[dict] | None = None
        self._ships_cache_lock = asyncio.Lock()
        self._ships_file_path = os.path.join("data", "temp", "wws_ships.json")
        self._build_nation_aliases()
        self._build_ship_type_aliases()
        asyncio.create_task(self._init_ship_cache())

    def _build_nation_aliases(self):
        self._alias_to_nation = {}
        for alias, nation in NATIONS.items():
            self._alias_to_nation[alias.lower()] = nation
        user_cfg = self.config.get("nation_aliases", {})
        if isinstance(user_cfg, str):
            user_cfg = user_cfg.strip()
            if user_cfg:
                try:
                    user_cfg = json.loads(user_cfg)
                except Exception as e:
                    logger.warning(f"[wws] 解析 nation_aliases JSON 失败: {e}")
                    user_cfg = {}
        self._user_nation_aliases = user_cfg if isinstance(user_cfg, dict) else {}
        if isinstance(user_cfg, dict):
            for nation_key, aliases_str in user_cfg.items():
                for a in str(aliases_str).split(","):
                    a = a.strip().lower()
                    if not a:
                        continue
                    if a in ROMAN_TO_TIER:
                        logger.warning(f"[wws] 国家别名「{a}」是罗马数字保留字，已跳过（不可用作别名）")
                        continue
                    self._alias_to_nation[a] = nation_key

    def _build_ship_type_aliases(self):
        """构建舰种别名映射表，合并内置默认与用户配置（用户配置覆盖同名 key）。"""
        self._alias_to_ship_type = {}
        for alias, ship_type in SHIP_TYPES.items():
            self._alias_to_ship_type[alias.lower()] = ship_type
        user_cfg = self.config.get("ship_type_aliases", {})
        if isinstance(user_cfg, str):
            user_cfg = user_cfg.strip()
            if user_cfg:
                try:
                    user_cfg = json.loads(user_cfg)
                except Exception as e:
                    logger.warning(f"[wws] 解析 ship_type_aliases JSON 失败: {e}")
                    user_cfg = {}
        if isinstance(user_cfg, dict):
            for ship_type_key, aliases_str in user_cfg.items():
                for a in str(aliases_str).split(","):
                    a = a.strip().lower()
                    if not a:
                        continue
                    if a in ROMAN_TO_TIER:
                        logger.warning(f"[wws] 舰种别名「{a}」是罗马数字保留字，已跳过（不可用作别名）")
                        continue
                    self._alias_to_ship_type[a] = ship_type_key

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        if not text:
            return

        random_trigger = self.config.get("random_trigger", "random")
        if random_trigger and (text == random_trigger or text.startswith(random_trigger + " ")):
            remainder = text[len(random_trigger):].strip()
            async for msg in self._handle_random(event, remainder):
                yield msg
            return

        keywords = self.config.get("trigger_keyword", ["wws me recent"])
        if isinstance(keywords, str):
            keywords = [keywords]
        if text not in keywords:
            return

        if self.config.get("enable_whitelist", False):
            group_id = event.get_group_id() if hasattr(event, "get_group_id") else None
            if not group_id:
                return
            if str(group_id) not in self.config.get("whitelist_groups", []):
                return

        prob = self.config.get("reply_probability", 100)
        if prob < 100 and random.randint(1, 100) > prob:
            logger.info(f"[wws] 概率回复未命中，跳过")
            return

        reply = await self._generate_tease()
        yield event.plain_result(reply)

    async def _handle_random(self, event: AstrMessageEvent, remainder: str):
        app_id = (self.config.get("wargaming_app_id") or "").strip()
        if not app_id:
            yield event.plain_result("没配 API 密钥，选不了船，窝批")
            return

        if remainder.lower() == "nations":
            async for msg in self._handle_nations(event):
                yield msg
            return

        filters = self._parse_ship_filters(remainder)
        logger.info(f"[wws] 随机选船筛选条件: {filters}")

        if filters.get("invalid"):
            bad = "、".join(filters["invalid"])
            tip = (
                f"⚠️ 不认识「{bad}」是啥，没法选，窝批\n"
                "💡 用法：random [舰种] [国家] [等级]\n"
                "   例：random bb 日 x  /  random dd us 6"
            )
            yield event.plain_result(tip)
            return

        ships = await self._get_ships(app_id)
        if not ships:
            yield event.plain_result("WG 服务器在摸鱼，选不了船，窝批")
            return

        matched = self._filter_ships(ships, filters)
        if not matched:
            # 只显示有效的筛选条件
            parts = []
            if filters.get("nation"):
                parts.append(f"国家={filters['nation']}")
            if filters.get("type"):
                parts.append(f"类型={filters['type']}")
            if filters.get("tier"):
                parts.append(f"等级={filters['tier']}级")
            cond_str = "、".join(parts) if parts else "无限制"
            empty_sp_template = self.config.get("ship_empty_reply_prompt", "")
            empty_sp = empty_sp_template.replace("{conditions}", cond_str) if "{conditions}" in empty_sp_template else empty_sp_template
            reply = await self._generate_tease_custom(
                f"按条件「{cond_str}」一艘船都没筛到，请用一句话调侃这个结果",
                system_prompt=empty_sp or None,
            )
            yield event.plain_result(reply or "条件太苛刻了，哪有这种船，窝批")
            return

        ship = random.choice(matched)
        name = ship.get("name", "未知战舰")
        image_url = ship.get("images", {}).get("large", "")
        logger.info(f"[wws] 随机选中: {name}")

        ship_sp = self.config.get("ship_reply_prompt", "")
        selected_prompt_template = self.config.get("ship_selected_prompt", "")
        selected_prompt = selected_prompt_template.replace("{ship}", name) if "{ship}" in selected_prompt_template else selected_prompt_template
        tease = await self._generate_tease_custom(
            selected_prompt,
            system_prompt=ship_sp or None,
        )

        text = f"{name}：{tease or name}"
        if image_url:
            yield event.chain_result([Comp.Plain(text), Comp.Image.fromURL(image_url)])
        else:
            yield event.plain_result(text)

    def _parse_ship_filters(self, text: str) -> dict:
        filters = {"type": None, "nation": None, "tier": None, "invalid": []}
        if not text:
            return filters
        for token in text.lower().split():
            if token.isdigit():
                t = int(token)
                if 1 <= t <= 11:
                    filters["tier"] = t
                    continue
            if token in ROMAN_TO_TIER:
                filters["tier"] = ROMAN_TO_TIER[token]
                continue
            if token in self._alias_to_ship_type:
                filters["type"] = self._alias_to_ship_type[token]
                continue
            if token in self._alias_to_nation:
                filters["nation"] = self._alias_to_nation[token]
                continue
            filters["invalid"].append(token)
        return filters

    async def _get_ships(self, app_id: str) -> list[dict]:
        async with self._ships_cache_lock:
            if self._ships_cache is not None:
                return self._ships_cache
        if os.path.exists(self._ships_file_path):
            try:
                with open(self._ships_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ships = data.get("ships", []) if isinstance(data, dict) else data
                if ships:
                    async with self._ships_cache_lock:
                        self._ships_cache = ships
                    logger.info(f"[wws] 从本地 JSON 加载船表: {len(ships)} 艘")
                    return ships
            except Exception as e:
                logger.warning(f"[wws] 本地 JSON 加载失败: {e}")
        logger.info("[wws] 本地无船表，实时拉取 API")
        ships = await self._fetch_ships_from_api(app_id)
        if ships:
            async with self._ships_cache_lock:
                self._ships_cache = ships
            asyncio.create_task(self._save_ships_to_file(ships))
        return ships or []

    async def _fetch_ships_from_api(self, app_id: str) -> list[dict]:
        lang = self.config.get("ship_name_language", "zh-cn")
        api_url = self.config.get("wg_api_url", "https://api.worldofwarships.com/wows/encyclopedia/ships/")
        limit, page_no, all_ships = 100, 1, []
        async with aiohttp.ClientSession() as session:
            while True:
                params = {"application_id": app_id, "language": lang, "limit": limit, "page_no": page_no}
                try:
                    async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            logger.error(f"[wws] WG API 返回 HTTP {resp.status}")
                            break
                        data = await resp.json()
                        if data.get("status") != "ok":
                            logger.error(f"[wws] WG API 错误: {data.get('error', {})}")
                            break
                        ships = list(data.get("data", {}).values())
                        all_ships.extend(ships)
                        meta = data.get("meta") or {}
                        page_total = meta.get("page_total", 0) or meta.get("pages", 0)
                        logger.info(f"[wws] WG API 第{page_no}页: {len(ships)} 艘{' (共约 ' + str(meta.get('total', '')) + ' 艘)' if meta.get('total') else ''}")
                        if (page_total and page_no >= page_total) or len(ships) < limit:
                            break
                        page_no += 1
                except Exception as e:
                    logger.error(f"[wws] WG API 请求失败 (page {page_no}): {e}")
                    break
        logger.info(f"[wws] WG API 全部拉取完成: 共 {len(all_ships)} 艘")
        return all_ships

    async def _save_ships_to_file(self, ships: list[dict]):
        try:
            os.makedirs(os.path.dirname(self._ships_file_path), exist_ok=True)
            simplified = [{"ship_id": s.get("ship_id"), "name": s.get("name"), "tier": s.get("tier"),
                           "type": s.get("type"), "nation": s.get("nation"),
                           "images": {"large": (s.get("images") or {}).get("large", "")}} for s in ships]
            payload = {"fetched_at": int(time.time()), "count": len(simplified), "ships": simplified}
            with open(self._ships_file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"[wws] 精简船表已保存 ({len(simplified)} 艘, {round(os.path.getsize(self._ships_file_path)/1024, 1)} KB)")
        except Exception as e:
            logger.error(f"[wws] 保存船表到文件失败: {e}")

    async def _init_ship_cache(self):
        if os.path.exists(self._ships_file_path):
            try:
                with open(self._ships_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ships = data.get("ships", []) if isinstance(data, dict) else data
                if ships:
                    self._ships_cache = ships
                    logger.info(f"[wws] 初始化: 从本地加载 {len(ships)} 艘船")
            except Exception as e:
                logger.warning(f"[wws] 初始化时本地文件加载失败: {e}")
        app_id = (self.config.get("wargaming_app_id") or "").strip()
        if not app_id:
            return
        need_refresh = False
        if os.path.exists(self._ships_file_path):
            mtime = os.path.getmtime(self._ships_file_path)
            age_days = (time.time() - mtime) / 86400
            ttl_days = self.config.get("ship_cache_ttl_days", 3)
            if age_days >= ttl_days:
                logger.info(f"[wws] 船表已过期 ({age_days:.1f}天 >= {ttl_days}天)，启动后台刷新")
                need_refresh = True
        else:
            logger.info("[wws] 无本地船表，启动后台拉取")
            need_refresh = True
        if need_refresh:
            asyncio.create_task(self._ensure_ship_cache())

    async def _ensure_ship_cache(self):
        app_id = (self.config.get("wargaming_app_id") or "").strip()
        if not app_id:
            return
        retry_interval = self.config.get("ship_cache_retry_interval_minutes", 5) * 60
        while True:
            logger.info("[wws] 后台开始拉取船表...")
            ships = await self._fetch_ships_from_api(app_id)
            if ships:
                await self._save_ships_to_file(ships)
                async with self._ships_cache_lock:
                    self._ships_cache = ships
                logger.info(f"[wws] 船表刷新成功 ✓")
                return
            logger.warning(f"[wws] 船表拉取失败，{retry_interval//60} 分钟后重试...")
            await asyncio.sleep(retry_interval)

    def _format_nation_list(self) -> str:
        if not self._user_nation_aliases:
            return "暂无别名配置，请在 WebUI 的 nation_aliases 中设置"
        lines = ["── 可用国家别名 ──", ""]
        for nation, aliases_str in sorted(self._user_nation_aliases.items()):
            aliases = "、".join(a.strip() for a in str(aliases_str).split(",") if a.strip())
            lines.append(f"  {nation}  →  {aliases}")
        lines.extend(["", "💡 random dd us / random bb 日 10"])
        return "\n".join(lines)

    async def _handle_nations(self, event: AstrMessageEvent):
        if not self._user_nation_aliases:
            yield event.plain_result("暂无别名配置，请在 WebUI 的 nation_aliases 中设置")
            return
        items = [{"nation": n, "aliases": "、".join(a.strip() for a in str(s).split(",") if a.strip())}
                 for n, s in sorted(self._user_nation_aliases.items())]
        html = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f172a;padding:20px;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.title{background:linear-gradient(135deg,#1e40af,#3b82f6);border-radius:10px;padding:14px 20px;text-align:center;margin-bottom:12px}
.title span{color:#fff;font-size:18px;font-weight:700;letter-spacing:1px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.card{background:#1e293b;border-radius:8px;padding:10px 14px;border:1px solid #334155}
.card .key{font-size:12px;color:#64748b;font-weight:600;margin-bottom:3px}
.card .val{font-size:15px;color:#f1f5f9;font-weight:500}
.footer{margin-top:12px;text-align:center;font-size:12px;color:#475569;padding-top:8px;border-top:1px solid #1e293b}
</style></head><body>
<div class="title"><span>📋 可用国家别名</span></div>
<div class="grid">{% for item in items %}
<div class="card"><div class="key">{{ item.nation }}</div><div class="val">{{ item.aliases }}</div></div>
{% endfor %}</div>
<div class="footer">💡 random dd us / random bb 日 10</div>
</body></html>"""
        try:
            img_url = await self.html_render(html, {"items": items}, options={"full_page": True, "type": "png"})
            if img_url:
                yield event.image_result(img_url)
                return
        except Exception as e:
            logger.error(f"[wws] html_render 失败: {e}")
        yield event.plain_result(self._format_nation_list())

    async def _generate_tease(self) -> str:
        return await self._generate_tease_custom(
            prompt="有人发了一句「wws me recent」（相当于自嘲窝批），请调侃他一句。", default="窝批")

    async def _generate_tease_custom(self, prompt: str, default: str = "", system_prompt: str | None = None) -> str:
        try:
            prov = self.context.get_using_provider()
            if not prov:
                return default or "窝批"
            sp = system_prompt or self.config.get("reply_prompt", "")
            logger.info(f"[wws] LLM prompt: {prompt[:60]}...")
            resp = await prov.text_chat(prompt=prompt, contexts=[], system_prompt=sp)
            if resp:
                reply = getattr(resp, "completion_text", None)
                if reply:
                    reply = reply.strip().strip('"').strip("'").strip("「」")
                    if reply:
                        return reply
            return default or "窝批"
        except Exception as e:
            logger.error(f"[wws] LLM 调用失败: {e}")
            return default or "窝批"

    @staticmethod
    def _filter_ships(ships: list[dict], filters: dict) -> list[dict]:
        result = ships[:]
        if filters.get("type"):
            result = [s for s in result if s.get("type") == filters["type"]]
        if filters.get("nation"):
            result = [s for s in result if s.get("nation") == filters["nation"]]
        if filters.get("tier") is not None:
            result = [s for s in result if s.get("tier") == filters["tier"]]
        return result
