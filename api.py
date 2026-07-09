"""
JS Bridge API — methods exposed to the frontend via pywebview.
All methods return JSON-serializable dicts.
"""

import os
import sys
import json
import logging
from datetime import datetime

def _app_dir() -> str:
    """Return writable directory for app data (works under PyInstaller)."""
    if getattr(sys, 'frozen', False):
        # Running as exe — use AppData
        base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'RelicPicker')
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base, exist_ok=True)
    return base

log = logging.getLogger("relicpicker")

def _pkg_path(filename: str) -> str:
    """Return path to a packaged resource file."""
    if getattr(sys, 'frozen', False):
        # PyInstaller extracts to sys._MEIPASS
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

from client import SmithboxClient
from loader import Loader, SHOPS, SHOP_POOLS
from matcher import Matcher
from models import EffectVariant, BoxItem


class RelicPickerAPI:
    """API class exposed to JavaScript as `pywebview.api`."""

    def __init__(self):
        self._client: SmithboxClient | None = None
        self._loader: Loader | None = None
        self._matcher: Matcher | None = None

        # Session state
        self.shop: str = "normal-old"
        self.color: int = 0
        self.effects: list[dict] = []   # [{eff_id, curse_id}, ...]
        self.selected_relic_id: int | None = None
        self.favorites: set[int] = set()
        self.box: list[BoxItem] = []

        # O(1) lookup indexes (built after data load)
        self._eff_by_id: dict[int, list] = {}     # aeId → list of Effects
        self._curse_by_id: dict[int, object] = {}  # curseId → CurseEffect
        self._eff_in_pool: dict[tuple, bool] = {}   # (aeId, pool_id) → exists
        self._loaded_pools: set[int] = set()         # pool_ids already loaded
        self._name_cache: dict[int, str] = {}        # eff_id → resolved name
        # Illegal check cache: (relic_id, eff_ids_tuple, curse_ids_tuple) → bool
        self._illegal_cache: dict[tuple, bool] = {}

        self._box_file = os.path.join(_app_dir(), "relic_box.json")
        self._box_cache: list[dict] | None = None  # serialized box, invalidated on change
        self._load_box()

    # ── Indexes ──────────────────────────────────────────────────────

    def _build_indexes(self):
        """Build O(1) lookup indexes from loader data."""
        self._eff_by_id.clear()
        self._curse_by_id.clear()
        self._eff_in_pool.clear()
        self._loaded_pools.clear()
        self._illegal_cache.clear()
        self._name_cache.clear()

        if not self._loader:
            return
        for eff in self._loader.effects:
            self._eff_by_id.setdefault(eff.id, []).append(eff)
            self._eff_in_pool[(eff.id, eff.pool_id)] = True
            self._loaded_pools.add(eff.pool_id)
        for c in self._loader.curses:
            self._curse_by_id[c.id] = c
            self._loaded_pools.add(c.pool_id)

    # ── Connection ───────────────────────────────────────────────────

    def connect(self) -> dict:
        """Connect to Smithbox and load data. Called once on app start."""
        try:
            self._client = SmithboxClient()
            self._loader = Loader(_pkg_path("ae_names.json"))
            self._loader.load_all(self._client)
            self._build_indexes()
            self._matcher = Matcher(
                self._loader.relics,
                self._loader.effects,
                self._loader.curses,
            )
            msg = f"已连接 — {len(self._loader.relics)} 遗物, {len(self._loader.effects)} 效果, {len(self._loader.curses)} 诅咒"
            log.info(msg)
            return {
                "success": True,
                "message": msg,
                "relics": len(self._loader.relics),
                "effects": len(self._loader.effects),
                "curses": len(self._loader.curses),
            }
        except ConnectionError as e:
            log.error("连接失败: %s", e)
            return {"success": False, "message": str(e)}
        except Exception as e:
            log.error("加载数据失败: %s", e)
            return {"success": False, "message": f"加载数据失败: {e}"}

    def reconnect(self) -> dict:
        """Force reload from Smithbox (e.g. after project change)."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        self._loader = Loader(_pkg_path("ae_names.json"))
        self.effects = []

        try:
            self._client = SmithboxClient()
            self._loader.load_all_force(self._client)
            self._build_indexes()
            self._matcher = Matcher(
                self._loader.relics,
                self._loader.effects,
                self._loader.curses,
            )
            return {
                "success": True,
                "message": f"已连接 — {len(self._loader.relics)} 遗物, {len(self._loader.effects)} 效果, {len(self._loader.curses)} 诅咒",
                "relics": len(self._loader.relics),
                "effects": len(self._loader.effects),
                "curses": len(self._loader.curses),
            }
        except ConnectionError as e:
            self._client = None
            self._matcher = None
            return {"success": False, "message": str(e)}
        except Exception as e:
            self._client = None
            self._matcher = None
            return {"success": False, "message": f"加载数据失败: {e}"}
        self.effects = []
        return result

    # ── State snapshot ───────────────────────────────────────────────

    def get_state(self) -> dict:
        """Return full current state for UI rendering."""
        matches = self._match()
        status, status_msg = self._compute_status(matches)

        # Enrich effects with names from loader
        allowed_pools = SHOP_POOLS.get(self.shop, set())
        enriched = []
        for e in self.effects:
            eff = self._loader.get_effect(e["eff_id"]) if self._loader else None
            curse = None
            if e.get("curse_id") and self._loader:
                curse = self._loader.get_curse(e["curse_id"])
            # Check if this aeId exists in current shop's pools
            shop_valid = any(
                x.pool_id in allowed_pools for x in self._loader.effects
                if x.id == e["eff_id"]
            ) if self._loader else False
            enriched.append({
                "eff_id": e["eff_id"],
                "name": eff.name if eff else f"?{e['eff_id']}",
                "pool_id": eff.pool_id if eff else 0,
                "compat_id": eff.compat_id if eff else "",
                "variant": eff.variant.value if eff else "normal",
                "dlc_only": eff.dlc_only if eff else False,
                "curse_id": e.get("curse_id"),
                "curse_name": curse.name if curse else None,
                "shop_valid": shop_valid,
                "fav": (e["eff_id"] in self.favorites),
            })

        return {
            "shop": self.shop,
            "color": self.color,
            "selected_relic_id": self.selected_relic_id,
            "effects": enriched,
            "favorites": list(self.favorites),
            "matches": self._serialize_matches(matches),
            "box_count": len(self.box),
            "status": status,
            "status_message": status_msg,
            "connected": self._client is not None,
            "loaded_relics": len(self._loader.relics) if self._loader else 0,
            "loaded_effects": len(self._loader.effects) if self._loader else 0,
            "loaded_curses": len(self._loader.curses) if self._loader else 0,
        }

    # ── Filters ──────────────────────────────────────────────────────

    def set_shop(self, shop: str) -> dict:
        if shop in SHOPS:
            self.shop = shop
            self.selected_relic_id = None
        return self.get_state()

    def set_color(self, color: int) -> dict:
        self.color = color
        self.selected_relic_id = None  # reset on color change
        return self.get_state()

    def set_relic(self, relic_id: int) -> dict:
        """User selects a specific relic from the match list."""
        self.selected_relic_id = relic_id
        return self.get_state()

    # ── Effects ──────────────────────────────────────────────────────

    def get_available_effects(self, query: str = "") -> list[dict]:
        """Get effects filterable by the current shop mode."""
        if not self._loader:
            return []

        effects = self._loader.effects

        # Filter by shop: only effects from this shop's pools, dedupe by aeId
        allowed = SHOP_POOLS.get(self.shop, set())
        seen = set()
        effect_list = []
        for e in effects:
            if e.pool_id in allowed and e.id not in seen:
                seen.add(e.id)
                effect_list.append(self._serialize_effect(e))

        if query:
            q = query.lower()
            effect_list = [e for e in effect_list if q in e["name"].lower()]

        return effect_list

    def get_available_curses(self, query: str = "") -> list[dict]:
        """Get all available curse effects."""
        if not self._loader:
            return []
        curses = [
            {"id": c.id, "name": c.name, "compat_id": c.compat_id}
            for c in self._loader.curses
        ]
        if query:
            q = query.lower()
            curses = [c for c in curses if q in c["name"].lower()]
        return curses

    def add_effect(self, eff_id: int) -> dict:
        """Add an effect to the selection (replaces same compat_id)."""
        self.selected_relic_id = None  # reset on effect change
        if len(self.effects) >= 3:
            return self.get_state()
        effect = self._loader.get_effect(eff_id) if self._loader else None
        if not effect:
            return self.get_state()

        # Remove effects that share compat_id with the new one (variant swap)
        self.effects = [
            e for e in self.effects
            if self._loader.get_effect(e["eff_id"]).compat_id != effect.compat_id
        ]
        self.effects.append({"eff_id": eff_id, "curse_id": None})
        return self.get_state()

    def remove_effect(self, index: int) -> dict:
        self.selected_relic_id = None  # reset on effect change
        if 0 <= index < len(self.effects):
            self.effects.pop(index)
        return self.get_state()

    def set_curse(self, index: int, curse_id: int) -> dict:
        """Assign a curse to the effect at index."""
        self.selected_relic_id = None  # reset on curse change
        if 0 <= index < len(self.effects):
            # Remove this curse from any other effect
            for e in self.effects:
                if e.get("curse_id") == curse_id:
                    e["curse_id"] = None
            self.effects[index]["curse_id"] = curse_id
        return self.get_state()

    def toggle_favorite(self, eff_id: int) -> dict:
        if eff_id in self.favorites:
            self.favorites.discard(eff_id)
        else:
            self.favorites.add(eff_id)
        return self.get_state()

    # ── Random Roll ───────────────────────────────────────────────────

    def _pick_weighted_aet(self, pool_id: int, used_compats: set) -> tuple | None:
        """Pick a random effect from an AET pool, recursively expanding
        nested AETs. Returns (ae_id, compat_id, name, weight) or None."""
        import random
        if not self._client:
            return None

        rows = self._client.get_param_rows("AttachEffectTableParam", pool_id, vanilla=True)
        if not rows:
            return None

        # Collect entries with their weights, filtering out used compats
        entries = []
        for r in rows:
            f = r["fields"]
            ae_id = int(f.get("attachEffectId", -1))
            if ae_id <= 0:
                continue
            wt = int(f.get("chanceWeight_dlc", -1))
            if wt == -1:
                wt = int(f.get("chanceWeight", 0))
            if wt <= 0:
                continue
            # Check if this is a leaf AEP or nested AET
            compat_id = str(ae_id)
            name = ""
            aep_rows = self._client.get_param_rows("AttachEffectParam", ae_id, vanilla=True)
            if aep_rows and aep_rows[0]["fields"]:
                compat_id = str(aep_rows[0]["fields"].get("compatibilityId", ae_id))
                if compat_id in used_compats:
                    continue
                text_id = int(aep_rows[0]["fields"].get("attachTextId", ae_id))
                name = self._loader.lookup_effect_name(text_id) if self._loader else ""
            entries.append({"ae_id": ae_id, "weight": wt, "compat_id": compat_id,
                           "name": name, "is_nested": not (aep_rows and aep_rows[0]["fields"])})

        if not entries:
            return None

        chosen = random.choices(entries, weights=[e["weight"] for e in entries], k=1)[0]

        if chosen["is_nested"]:
            # Recurse into nested AET
            result = self._pick_weighted_aet(chosen["ae_id"], used_compats)
            if result is not None:
                return result
            # Fall through: if nested resolution fails, use as-is
            return (chosen["ae_id"], chosen["compat_id"], chosen["name"], chosen["weight"])

        return (chosen["ae_id"], chosen["compat_id"], chosen["name"], chosen["weight"])

    def _pick_weighted_relic(self, nodes: list, client) -> int:
        """Randomly pick a relic from the shop tree, weighted by chanceWeight."""
        import random
        if not nodes:
            return 0

        entries = []
        for n in nodes:
            wt = n.get("weight", 1)
            if wt <= 0:
                continue
            entries.append(n)

        if not entries:
            return 0

        chosen = random.choices(entries, weights=[n.get("weight", 1) for n in entries], k=1)[0]

        if chosen["type"] == "relic":
            return chosen["antique_id"]
        elif chosen["type"] == "branch":
            return self._pick_weighted_relic(chosen.get("children", []), client)
        return 0

    def roll(self) -> dict:
        """Randomly roll a relic + effects based on in-game weights."""
        if not self._client or not self._loader:
            return {"success": False, "message": "未连接到 Smithbox"}

        import random

        # 1) Add weights to shop tree nodes (no color filter — color is random too)
        tree = self._loader.get_shop_tree(self.shop, self._client)
        self._add_tree_weights(tree, self._client)

        # 2) Pick a relic randomly
        relic_id = self._pick_weighted_relic(tree, self._client)
        if not relic_id:
            return {"success": False, "message": "没有匹配的遗物可抽取"}

        relic = self._loader.get_relic(relic_id)
        if relic is None:
            relic = self._load_relic_on_demand(relic_id)
        if relic is None:
            return {"success": False, "message": f"遗物 {relic_id} 未找到"}

        # 3) For each pool slot, pick a random effect
        effects = []
        used_compats = set()
        curse_index = 0
        curse_pools = [p for p in relic.curse_pool_ids if p != -1]

        for pid in relic.pool_ids:
            if pid == -1:
                continue
            result = self._pick_weighted_aet(pid, used_compats)
            if result is None:
                return {"success": False, "message": f"池 {pid} 中无可用词条"}

            ae_id, compat_id, name, weight = result
            used_compats.add(compat_id)

            curse_id = None
            # If deep shop and cursed-strong, roll a curse
            deep = SHOPS[self.shop]["deep"]
            if deep and curse_index < len(curse_pools):
                # Determine variant for this effect
                curse_result = self._pick_weighted_aet(curse_pools[curse_index], used_compats)
                if curse_result:
                    curse_id = curse_result[0]
                    used_compats.add(curse_result[1])
                curse_index += 1

            effects.append({"eff_id": ae_id, "curse_id": curse_id})

        # 4) Apply to current state
        self.effects = effects
        self.color = relic.color
        self.selected_relic_id = relic.id

        return self.get_state()

    def _add_tree_weights(self, nodes: list, client):
        """Add weight info to shop tree nodes from ItemTableParam."""
        for n in nodes:
            tid = n.get("table_id", 0)
            ridx = n.get("row_index", 0)
            if tid and ridx >= 0:
                rows = client.get_param_rows("ItemTableParam", tid, vanilla=True)
                for r in rows:
                    if r["row_index"] == ridx:
                        dlc_wt = int(r["fields"].get("chanceWeight_dlc", -1))
                        base_wt = int(r["fields"].get("chanceWeight", 0))
                        n["weight"] = dlc_wt if dlc_wt != -1 else base_wt
                        break
            if n["type"] == "branch":
                self._add_tree_weights(n.get("children", []), client)

    # ── Apply ────────────────────────────────────────────────────────

    def preview(self) -> dict:
        """Return a preview of what will be applied."""
        match = self._get_selected_match()
        if not match:
            return {"success": False, "message": "没有遗物能容纳这些效果"}

        # Check relic legality (effects vs AET pools)
        relic = match.relic
        if relic.id not in self._loader._relics:
            self._load_relic_on_demand(relic.id)
        if self._check_effects_vs_relic(relic, self.effects):
            return {"success": False, "message": "遗物与词条不匹配，该配置为非法遗物"}

        # Find ItemTable path
        tree = self._loader.get_shop_tree(self.shop, self._client)
        path = self._find_path(tree, relic.id)

        pool_mods = []
        for a in match.assignments:
            pool_mods.append({
                "pool_id": a.pool_id,
                "row_index": a.effect.row_index,
                "effect_name": a.effect.name,
                "curse_name": a.curse.name if a.curse else None,
            })

        return {
            "success": True,
            "relic_id": relic.id,
            "relic_name": relic.name,
            "color": relic.color,
            "pool_mods": pool_mods,
            "path_nodes": path,
        }

    def apply(self) -> dict:
        """Apply the current selection to game data."""
        if not self._client:
            return {"success": False, "message": "未连接到 Smithbox"}

        match = self._get_selected_match()
        if not match:
            return {"success": False, "message": "没有遗物能容纳这些效果"}

        try:
            self._do_apply(match)
            return {"success": True, "message": f"已应用 — {match.relic.name}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _find_path(self, nodes: list, target_id: int) -> list:
        """Find the ItemTable path to a relic in the shop tree."""
        for n in nodes:
            if n["type"] == "relic" and n["antique_id"] == target_id:
                return [n]
            if n["type"] == "branch":
                sub = self._find_path(n.get("children", []), target_id)
                if sub:
                    return [n] + sub
        return []

    def _do_apply(self, match):
        """Execute param modifications (mirrors v4 _apply_one)."""
        relic = match.relic
        log.info("Apply: relic=%s (%d)", relic.name, relic.id)

        # 1) ItemTableParam chain
        tree = self._loader.get_shop_tree(self.shop, self._client)
        path = self._find_path(tree, relic.id)
        if not path:
            raise Exception("找不到 ItemTable 路径")

        for n in path:
            tid = n["table_id"]
            iid = n["item_id"]
            script = (
                f"param ItemTableParam: id {tid}: chanceWeight: = 0;"
                f"param ItemTableParam: id {tid}: chanceWeight_dlc: = -1;"
                f"param ItemTableParam: id {tid}: itemId: = {iid};"
                f"param ItemTableParam: id {tid}: chanceWeight: = 1;"
            )
            result = self._client.execute_mass_edit(script)
            if not result["success"]:
                raise Exception(f"ItemTable[{tid}] FAILED: {result['result']}")
            log.info("  ItemTable[%d] -> itemId=%d OK", tid, iid)

        # 2) AETable pools
        pool_targets: dict[int, list[tuple[int, str]]] = {}  # pool_id -> [(row_index, effect_name)]
        for a in match.assignments:
            pool_targets.setdefault(a.pool_id, []).append(
                (a.effect.row_index, a.effect.name)
            )

        for pid, targets in pool_targets.items():
            # Zero all entries in this pool
            script = (
                f"param AttachEffectTableParam: id {pid}: chanceWeight: = 0;"
                f"param AttachEffectTableParam: id {pid}: chanceWeight_dlc: = -1;"
            )
            result = self._client.execute_mass_edit(script)
            if not result["success"]:
                raise Exception(f"Pool[{pid}] zero FAILED: {result['result']}")

            for ridx, ename in targets:
                cr = self._client.set_param_cell(
                    "AttachEffectTableParam", ridx, "chanceWeight", 1
                )
                if not cr["success"]:
                    raise Exception(
                        f"Pool[{pid}] row[{ridx}] ({ename}) FAILED: {cr['message']}"
                    )
                log.info("  Pool[%d] row[%d] %s = 1 OK", pid, ridx, ename)

        # 2b) Curse pools — group curses by pool_id, zero each pool, set targets
        curse_targets: dict[int, list[tuple[int, str]]] = {}
        for a in match.assignments:
            if a.curse:
                curse_targets.setdefault(a.curse.pool_id, []).append(
                    (a.curse.row_index, a.curse.name)
                )

        for pid, targets in curse_targets.items():
            script = (
                f"param AttachEffectTableParam: id {pid}: chanceWeight: = 0;"
                f"param AttachEffectTableParam: id {pid}: chanceWeight_dlc: = -1;"
            )
            result = self._client.execute_mass_edit(script)
            if not result["success"]:
                raise Exception(f"CursePool[{pid}] zero FAILED: {result['result']}")

            for ridx, cname in targets:
                cr = self._client.set_param_cell(
                    "AttachEffectTableParam", ridx, "chanceWeight", 1
                )
                if not cr["success"]:
                    raise Exception(
                        f"CursePool[{pid}] row[{ridx}] ({cname}) FAILED: {cr['message']}"
                    )
                log.info("  CursePool[%d] row[%d] %s = 1 OK", pid, ridx, cname)

        # 3) Reload — check failed AND whether anything was actually reloaded
        r1 = self._client.reload_param("ItemTableParam")
        r2 = self._client.reload_param("AttachEffectTableParam")
        failed = r1.get("failed", []) + r2.get("failed", [])
        reloaded = r1.get("reloaded", []) + r2.get("reloaded", [])
        if failed or not reloaded:
            detail = ", ".join(failed) if failed else "reloaded 列表为空"
            raise Exception(f"Reload 失败: {detail}。请确认游戏已启动。")
        log.info("  Reload OK")

    # ── Relic Box ────────────────────────────────────────────────────

    def _compute_box_cache(self):
        """Build the serialized box cache (called after connect or batch import)."""
        self._box_cache = [self._serialize_box_item(b) for b in self.box]

    def get_box(self) -> list[dict]:
        if self._box_cache is None:
            # self._preload_box_relics()  # disabled with illegal check
            self._compute_box_cache()
        return self._box_cache

    def _preload_box_relics(self):
        """Batch-load all non-cached relic data needed by box items."""
        if not self._client or not self._loader:
            return
        missing = set()
        for b in self.box:
            if b.relic_id != 0 and self._loader.get_relic(b.relic_id) is None:
                missing.add(b.relic_id)
        if not missing:
            return
        log.info("预加载 %d 个遗物数据...", len(missing))
        for relic_id in missing:
            # Load without per-relic index rebuild (batched)
            self._load_relic_on_demand(relic_id, rebuild_index=False)
        # Single index rebuild after all relics loaded
        self._build_indexes()
        log.info("预加载完成")

    def add_to_box(self) -> dict:
        """Save current effects to relic box."""
        dup = any(
            b.effects == self.effects and b.shop == self.shop
            for b in self.box
        )
        if dup:
            return {"success": False, "message": "已在遗物盒中"}

        match = self._get_selected_match()
        relic_id = match.relic.id if match else 0

        item = BoxItem(
            effects=self.effects.copy(),
            shop=self.shop,
            color=self.color,
            added_at=datetime.now().strftime("%Y/%m/%d"),
            relic_id=relic_id,
        )
        self.box.append(item)
        self._save_box()

        # Incremental cache update
        if self._box_cache is not None:
            self._box_cache.append(self._serialize_box_item(item))

        state = self.get_state()
        state["message"] = "已加入遗物盒"
        return state

    def remove_from_box(self, index: int) -> dict:
        if 0 <= index < len(self.box):
            self.box.pop(index)
            self._save_box()
            if self._box_cache is not None and index < len(self._box_cache):
                self._box_cache.pop(index)
        return self.get_state()

    def import_box(self, text: str) -> dict:
        """Import from v4 share-format: RELIC_ID:ae1,ae2:cae1,cae2."""
        count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) < 2:
                continue
            try:
                imported_relic_id = int(parts[0].strip()) if parts[0].strip() else 0
                eff_ids = [int(x.strip()) for x in parts[1].split(",") if x.strip()]
                curse_ids = []
                if len(parts) >= 3:
                    curse_ids = [int(x.strip()) for x in parts[2].split(",") if x.strip()]
            except ValueError:
                continue

            effects = [{"eff_id": eid, "curse_id": None} for eid in eff_ids]
            for ci, cid in enumerate(curse_ids):
                if ci < len(effects):
                    effects[ci]["curse_id"] = cid

            # Auto-detect shop: try each shop, pick the first that matches
            detected_shop = self.shop  # fallback
            detected_color = self.color  # fallback
            detected_relic_id = imported_relic_id
            if self._matcher and self._loader:
                for sk in SHOPS:
                    matches = self._matcher.match(
                        effects,
                        shop_is_deep=SHOPS[sk]["deep"],
                        relics=self._loader.relics_for_shop(sk),
                    )
                    if matches:
                        detected_shop = sk
                        detected_color = matches[0].relic.color
                        if not detected_relic_id:
                            detected_relic_id = matches[0].relic.id
                        break

            # If relic_id was specified, try to get its actual color
            if imported_relic_id and self._loader:
                r = self._loader.get_relic(imported_relic_id)
                if r is not None:
                    detected_color = r.color
                elif self._client:
                    r = self._load_relic_on_demand(imported_relic_id)
                    if r is not None:
                        detected_color = r.color

            self.box.append(BoxItem(
                effects=effects,
                shop=detected_shop,
                color=detected_color,
                added_at=datetime.now().strftime("%Y/%m/%d"),
                relic_id=detected_relic_id,
            ))
            count += 1

        self._save_box()
        self._box_cache = None  # invalidate — full rebuild on next get_box
        state = self.get_state()
        state["message"] = f"已导入 {count} 个"
        return state

    def export_box(self) -> dict:
        """Export in v4 share-format: # comments + RELIC_ID:aeIds:caeIds."""
        lines = []
        lines.append("# RelicBox")
        lines.append(f"# {datetime.now().strftime('%Y/%m/%d')}")
        lines.append("#")

        # Save current state
        saved_effects = list(self.effects)
        saved_shop = self.shop
        saved_color = self.color

        for b in self.box:
            # Use stored relic_id, fallback to matching
            relic_id = b.relic_id if b.relic_id else 0
            if not relic_id:
                # Fallback: try to match
                self.shop = b.shop
                self.color = b.color
                self.effects = b.effects.copy()
                matches = self._match()
                relic_id = matches[0].relic.id if matches else 0

            # Annotate with relic name
            if self._loader:
                r = self._loader.get_relic(relic_id)
                if r:
                    lines.append(f"# 遗物: [{relic_id}] {r.name}")

            for e in b.effects:
                eff = self._loader.get_effect(e["eff_id"]) if self._loader else None
                name = eff.name if eff else f"aeId={e['eff_id']}"
                if e.get("curse_id"):
                    curse = self._loader.get_curse(e["curse_id"]) if self._loader else None
                    cname = curse.name if curse else f"?{e['curse_id']}"
                    lines.append(f"# {name} | {cname}")
                else:
                    lines.append(f"# {name}")

            eff_ids = ",".join(str(e["eff_id"]) for e in b.effects)
            curse_ids = ",".join(
                str(e["curse_id"]) for e in b.effects if e.get("curse_id")
            )
            if curse_ids:
                lines.append(f"{relic_id}:{eff_ids}:{curse_ids}")
            else:
                lines.append(f"{relic_id}:{eff_ids}")
            lines.append("#")

        # Restore state
        self.effects = saved_effects
        self.shop = saved_shop
        self.color = saved_color

        return {"text": "\n".join(lines)}

    def open_box_file(self) -> dict:
        """Open a file dialog and return the file contents."""
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                title="导入遗物盒",
            )
            if not path:
                return {"ok": False}
            with open(path, "r", encoding="utf-8") as f:
                return {"ok": True, "text": f.read(), "filename": os.path.basename(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_clipboard(self) -> dict:
        """Read text from the system clipboard."""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return {"ok": True, "text": text} if text.strip() else {"ok": False, "error": "剪贴板为空"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_box_to_file(self, text: str = "") -> dict:
        """Save exported text to a file chosen by the user."""
        if not text:
            result = self.export_box()
            text = result.get("text", "")
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                initialfile="relic_export.txt",
                title="导出遗物盒",
            )
            if not path:
                return {"saved": False, "message": "已取消"}
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return {"saved": True, "message": f"已保存到 {os.path.basename(path)}"}
        except Exception as e:
            return {"saved": False, "message": str(e)}

    def get_settings(self) -> dict:
        """Return user settings from disk."""
        path = os.path.join(_app_dir(), "settings.json")
        try:
            if os.path.exists(path):
                return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
        return {"theme": "dark"}

    def save_settings(self, settings: dict) -> dict:
        """Save user settings to disk (merges with existing)."""
        path = os.path.join(_app_dir(), "settings.json")
        try:
            current = {}
            if os.path.exists(path):
                try:
                    current = json.load(open(path, encoding="utf-8"))
                except Exception:
                    pass
            current.update(settings)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url: str) -> dict:
        """Open a URL in the default system browser."""
        import webbrowser
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def batch_apply(self, indices: list[int] = None) -> dict:
        """Apply box items by index. If indices is None, apply all.
        Each item uses its own shop from the box data."""
        if not self._client:
            return {"success": False, "message": "未连接到 Smithbox"}

        items = []
        if indices:
            for i in indices:
                if 0 <= i < len(self.box):
                    items.append((i, self.box[i]))
        else:
            items = list(enumerate(self.box))

        SHOP_NAMES = {"normal-old":"旧版普通","normal-new":"新版普通",
                       "deep-old":"旧版深夜","deep-new":"新版深夜"}

        saved_effects = list(self.effects)
        saved_shop = self.shop

        # Pre-load relic data for all items in the batch
        for _, item in items:
            if item.relic_id and self._loader.get_relic(item.relic_id) is None:
                self._load_relic_on_demand(item.relic_id)
        self._build_indexes()

        results = []
        try:
            for idx, item in items:
                shop = item.shop
                shop_name = SHOP_NAMES.get(shop, shop)
                self.shop = shop
                self.effects = item.effects.copy()
                try:
                    matches = self._match()
                    if matches:
                        relic = matches[0].relic
                        if self._check_effects_vs_relic(relic, self.effects):
                            results.append({"ok": False, "name": relic.name,
                                "error": "非法遗物：词条与AET池不匹配",
                                "shop": shop_name, "idx": idx})
                        else:
                            self._do_apply(matches[0])
                            results.append({"ok": True, "name": relic.name,
                                "shop": shop_name, "idx": idx})
                    else:
                        results.append({"ok": False, "name": "?",
                            "error": "不匹配", "shop": shop_name, "idx": idx})
                except Exception as e:
                    results.append({"ok": False, "name": "?",
                        "error": str(e), "shop": shop_name, "idx": idx})
        finally:
            self.effects = saved_effects
            self.shop = saved_shop

        return {"results": results}

    # ── Helpers ──────────────────────────────────────────────────────

    def _match(self) -> list:
        if not self._matcher or not self._loader:
            return []
        deep = SHOPS[self.shop]["deep"]
        relics = self._loader.relics_for_shop(self.shop)
        matches = self._matcher.match(
            self.effects,
            color=self.color,
            shop_is_deep=deep,
            relics=relics,
        )
        # No effects selected — don't show any matches
        if not self.effects:
            return []
        # Only show relics with exactly the right number of slots
        n = len(self.effects)
        matches = [m for m in matches if m.relic.pool_count == n]
        return matches

    def _get_selected_match(self):
        """Return the user-selected match, or the first match as fallback."""
        matches = self._match()
        if not matches:
            return None
        if self.selected_relic_id is not None:
            for m in matches:
                if m.relic.id == self.selected_relic_id:
                    return m
            # Selected relic no longer valid, clear selection
            self.selected_relic_id = None
        return matches[0]

    def _compute_status(self, matches: list) -> tuple[str, str]:
        if not self.effects:
            return "incomplete", "添加至少 1 个效果以开始"

        # Check for effects not available in current shop
        allowed_pools = SHOP_POOLS.get(self.shop, set())
        for e in self.effects:
            eff = self._loader.get_effect(e["eff_id"]) if self._loader else None
            if eff:
                exists_in_shop = any(
                    x.pool_id in allowed_pools for x in self._loader.effects
                    if x.id == e["eff_id"]
                )
                if not exists_in_shop:
                    return "error", f'"{eff.name}" 在当前商店不可用'

        # Check all cursed-strong have curse
        for i, e in enumerate(self.effects):
            eff = self._loader.get_effect(e["eff_id"]) if self._loader else None
            if eff and eff.variant == EffectVariant.CURSED_STRONG and not e.get("curse_id"):
                return "error", f'请为"{eff.name}"选择诅咒'

        if not matches:
            return "error", "没有遗物能同时容纳这些效果"

        # Check legality (only if relic data is already cached — no gRPC here)
        match = self._get_selected_match()
        if match and match.relic and match.relic.id in self._loader._relics:
            if self._check_effects_vs_relic(match.relic, self.effects):
                return "error", "⚠ 非法遗物：词条与遗物AET池不匹配"

        return "ready", "就绪"


    def _serialize_effect(self, e) -> dict:
        return {
            "id": e.id,
            "name": e.name,
            "compat_id": e.compat_id,
            "variant": e.variant.value,
            "pool_id": e.pool_id,
            "dlc_only": e.dlc_only,
            "is_fav": e.id in self.favorites,
        }

    def _serialize_matches(self, matches: list) -> list[dict]:
        return [
            {
                "relic_id": m.relic.id,
                "relic_name": m.relic.name,
                "color": m.relic.color,
                "pool_count": m.relic.pool_count,
                "curse_count": m.relic.curse_pool_count,
                "is_complete": True,
            }
            for m in matches
        ]

    def _check_effects_vs_relic(self, relic, effects: list[dict]) -> bool:
        """Check if effects match a specific relic. Returns True if ILLEGAL."""
        valid_pools = [p for p in relic.pool_ids if p != -1]
        valid_curse_pools = [p for p in relic.curse_pool_ids if p != -1]

        curse_count = sum(1 for e in effects if e.get("curse_id"))

        # 1) Count check
        if len(effects) != len(valid_pools):
            return True
        if curse_count != len(valid_curse_pools):
            return True

        # 2) For each effect, find eligible relic pools (O(1) per lookup)
        candidates = []
        for e in effects:
            eff_id = e["eff_id"]
            eligible = [
                rp for rp in valid_pools
                if self._eff_in_pool.get((eff_id, rp))
            ]
            if not eligible:
                return True
            candidates.append(eligible)

        # 3) Bipartite matching (greedy, most-constrained first)
        candidates.sort(key=len)
        used = [False] * len(valid_pools)
        for eligible in candidates:
            matched = False
            for idx, rp in enumerate(valid_pools):
                if not used[idx] and rp in eligible:
                    used[idx] = True
                    matched = True
                    break
            if not matched:
                return True

        # 4) Check curses (O(1) per lookup)
        curse_pool_set = set(valid_curse_pools)
        for e in effects:
            if e.get("curse_id"):
                curse_id = e["curse_id"]
                curse = self._curse_by_id.get(curse_id)
                if not curse or curse.pool_id not in curse_pool_set:
                    return True

        return False

    def _load_relic_on_demand(self, relic_id: int, rebuild_index: bool = True):
        """Load a single relic + its pool effects on demand (for non-shop relics)."""
        if not self._client or not self._loader:
            return None

        # Check if already loaded
        r = self._loader.get_relic(relic_id)
        if r is not None:
            return r

        try:
            rows = self._client.get_param_rows("EquipParamAntique", relic_id, vanilla=True)
        except Exception:
            return None
        if not rows:
            return None

        row = rows[0]
        f = row["fields"]

        pools = []
        for key in ["attachEffectTableId_1", "attachEffectTableId_2", "attachEffectTableId_3"]:
            v = int(f.get(key, -1))
            if v > 0:
                pools.append(v)

        curse_pools = []
        for key in ["attachEffectTableId_curse1", "attachEffectTableId_curse2", "attachEffectTableId_curse3"]:
            v = int(f.get(key, -1))
            if v > 0:
                curse_pools.append(v)

        from models import Relic
        relic = Relic(
            id=relic_id,
            name=self._loader.lookup_relic_name(relic_id) or row.get("row_name", f"Relic{relic_id}"),
            color=int(f.get("relicColor", -1)),
            pool_ids=pools,
            curse_pool_ids=curse_pools,
            deep=int(f.get("isDeepRelic", 0)) == 1,
            sort_id=int(f.get("sortId", 0)),
        )

        # Store the relic so it's findable next time
        self._loader._relics[relic_id] = relic

        # Load effects for pools not yet loaded (O(1) check via _loaded_pools)
        from loader import is_curse_pool
        for pid in pools:
            if pid not in self._loaded_pools:
                try:
                    self._loader._load_pool(pid, self._client, is_curse=False)
                except Exception:
                    pass
        for pid in curse_pools:
            if pid not in self._loaded_pools:
                try:
                    self._loader._load_pool(pid, self._client, is_curse=True)
                except Exception:
                    pass

        if rebuild_index:
            self._build_indexes()

        return relic

    def _is_box_item_illegal(self, b: BoxItem) -> bool:
        # TODO: re-enable after optimizing on-demand relic loading
        if not self._loader:
            return False
        if not b.effects:
            return False
        return False  # ── disabled for performance ──

        # Build cache key: (relic_id, (eff_ids...), (curse_ids...))
        eff_ids = tuple(e["eff_id"] for e in b.effects)
        curse_ids = tuple(e.get("curse_id") for e in b.effects if e.get("curse_id"))
        cache_key = (b.relic_id, eff_ids, curse_ids)
        cached = self._illegal_cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._is_box_item_illegal_uncached(b, b.effects)
        self._illegal_cache[cache_key] = result
        return result

    def _is_box_item_illegal_uncached(self, b: BoxItem, effects: list[dict]) -> bool:
        """Uncached illegal check — walks shop relics for unspecified relic_id."""
        if b.relic_id != 0:
            relic = self._loader.get_relic(b.relic_id)
            if relic is None:
                # Relic not in shop lists — try loading on demand via client
                relic = self._load_relic_on_demand(b.relic_id)
            if relic is not None:
                return self._check_effects_vs_relic(relic, effects)
            # Can't load relic data (client unavailable or query failed)
            log.warning("无法验证遗物 %d 的合法性：client 不可用或查询失败", b.relic_id)
            return False

        # No relic specified:
        # check if any purchasable relic across all shops can hold these effects
        for sk in SHOPS:
            for relic in self._loader.relics_for_shop(sk):
                if not self._check_effects_vs_relic(relic, effects):
                    return False
        return True

    def _resolve_effect_name(self, eff_id: int) -> str:
        """Resolve an effect's display name (index → name cache → gRPC)."""
        # 1) Name cache
        cached = self._name_cache.get(eff_id)
        if cached is not None:
            return cached

        # 2) O(1) index lookup
        if self._eff_by_id:
            variants = self._eff_by_id.get(eff_id)
            if variants:
                name = variants[0].name
                self._name_cache[eff_id] = name
                return name

        # 3) Not in loaded data — resolve textId via AEP (gRPC, once per eff_id)
        if self._client:
            try:
                aep = self._client.get_param_rows(
                    "AttachEffectParam", eff_id, vanilla=True)
                if aep and aep[0]["fields"]:
                    text_id = int(aep[0]["fields"].get("attachTextId", eff_id))
                    if self._loader:
                        name = self._loader.lookup_effect_name(text_id)
                        if name:
                            self._name_cache[eff_id] = name
                            return name
            except Exception:
                pass

        name = f"未知词条{eff_id}"
        self._name_cache[eff_id] = name
        return name

    def _serialize_box_item(self, b: BoxItem) -> dict:
        effect_names = []
        curse_names = []
        for e in b.effects:
            effect_names.append(self._resolve_effect_name(e["eff_id"]))
            if e.get("curse_id"):
                curse_id = e["curse_id"]
                curse = self._loader.get_curse(curse_id) if self._loader else None
                if curse:
                    curse_names.append(curse.name)
                elif self._loader:
                    name = self._loader.lookup_effect_name(curse_id)
                    curse_names.append(name if name else f"未知诅咒{curse_id}")
                else:
                    curse_names.append(f"未知诅咒{curse_id}")
        relic_shop = True
        if b.relic_id == 0:
            relic_name = "未指定"
        elif self._loader:
            r = self._loader.get_relic(b.relic_id)
            if r:
                relic_name = r.name
            else:
                name = self._loader.lookup_relic_name(b.relic_id)
                relic_name = name if name else f"未知遗物{b.relic_id}"
                relic_shop = False
        else:
            relic_name = ""
        return {
            "effects": b.effects,
            "effect_names": effect_names,
            "curse_names": curse_names,
            "shop": b.shop,
            "color": b.color,
            "added_at": b.added_at,
            "relic_id": b.relic_id,
            "relic_name": relic_name,
            "relic_shop": relic_shop,
            "is_illegal": self._is_box_item_illegal(b),
        }

    def _load_box(self):
        if os.path.exists(self._box_file):
            try:
                raw = json.load(open(self._box_file, encoding="utf-8"))
                self.box = [
                    BoxItem(
                        effects=item["effects"],
                        shop=item.get("shop", "normal-old"),
                        color=item.get("color", -1),
                        added_at=item.get("added_at", ""),
                        relic_id=item.get("relic_id", 0),
                    )
                    for item in raw
                ]
            except (json.JSONDecodeError, KeyError):
                self.box = []

    def _save_box(self):
        data = [
            {
                "effects": b.effects,
                "shop": b.shop,
                "color": b.color,
                "added_at": b.added_at,
                "relic_id": b.relic_id,
            }
            for b in self.box
        ]
        with open(self._box_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
