"""
Data loader — fetches param data from Smithbox, transforms to models.
Caches results locally as JSON for fast restarts.
"""

import json
import os
from collections import defaultdict

from client import SmithboxClient
from models import Effect, EffectVariant, CurseEffect, Relic

CACHE_FILE = "loader_cache.json"

# Shop configuration
SHOPS = {
    "normal-old": {"root": 49000, "deep": False},
    "normal-new": {"root": 49020, "deep": False},
    "deep-old":  {"root": 49100, "deep": True},
    "deep-new":  {"root": 49300, "deep": True},
}

# Normal pools: same effects across all 3 slots, no curses
NORMAL_POOLS = {100, 200, 300, 110, 210, 310}

# Deep pool types
POOL_A = 2000000      # strong effects, mandatory curse
POOL_B_OLD = 2100000  # weak effects, no curse (old deep shop)
POOL_B_NEW = 2200000  # weak effects, no curse (new deep shop)
CURSE_POOL = 3000000  # curse effects

def is_curse_pool(pool_id: int) -> bool:
    return pool_id == CURSE_POOL

# Which pools belong to each shop
SHOP_POOLS = {
    "normal-old": {100, 200, 300},
    "normal-new": {110, 210, 310},
    "deep-old":   {POOL_A, POOL_B_OLD},
    "deep-new":   {POOL_A, POOL_B_NEW},
}

# Color names
COLORS = {0: "火燃", 1: "水滴", 2: "光耀", 3: "幽静"}


class Loader:
    """Loads all data from Smithbox once, transforms to model objects."""

    def __init__(self, ae_names_path: str = None):
        self._effects: list[Effect] = []                 # all effects (may have dup aeIds across pools)
        self._curses: list[CurseEffect] = []             # all curse effects
        self._relics: dict[int, Relic] = {}             # antiqueId -> Relic
        self._ae_names: dict[str, str] = {}             # textId -> name
        self._relic_names: dict[str, str] = {}          # relicId -> name
        self._shop_relics: dict[str, set[int]] = {}      # shop_key -> set of relic IDs

        if ae_names_path is None:
            ae_names_path = os.path.join(
                os.path.dirname(__file__), "names.json")
        self._load_ae_names(ae_names_path)

    # ── Public API ───────────────────────────────────────────────────

    def _cache_path(self) -> str:
        import sys
        if getattr(sys, 'frozen', False):
            base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'RelicPicker')
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, CACHE_FILE)

    def load_all(self, client: SmithboxClient):
        """Load everything from Smithbox (or cache)."""
        cache_path = self._cache_path()

        # Try cache first
        if os.path.exists(cache_path):
            try:
                cached = json.load(open(cache_path, encoding="utf-8"))
                if cached.get("relics") and cached.get("effects"):
                    self._from_cache(cached)
                    return
            except (json.JSONDecodeError, KeyError):
                pass
            os.remove(cache_path)

        self._load_relics(client)
        self._filter_relics_by_shops(client)
        self._load_effects(client)
        self._save_cache(cache_path)

    def load_all_force(self, client: SmithboxClient):
        """Force reload from Smithbox, ignore cache."""
        cache_path = self._cache_path()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        self._load_relics(client)
        self._filter_relics_by_shops(client)
        self._load_effects(client)
        self._save_cache(cache_path)

    def _filter_relics_by_shops(self, client: SmithboxClient):
        """Keep only relics that appear in at least one shop ItemTable chain."""
        all_shop_ids = set()
        self._shop_relics.clear()

        def collect(nodes):
            ids = set()
            for n in nodes:
                if n["type"] == "relic":
                    ids.add(n["antique_id"])
                elif n["type"] == "branch":
                    ids.update(collect(n.get("children", [])))
            return ids

        for shop_key in SHOPS:
            chain = self._unfold_chain(SHOPS[shop_key]["root"], client, set())
            ids = collect(chain)
            self._shop_relics[shop_key] = ids
            all_shop_ids.update(ids)

        self._relics = {rid: r for rid, r in self._relics.items() if rid in all_shop_ids}

    def relics_for_shop(self, shop_key: str) -> list[Relic]:
        """Return relics that appear in a specific shop."""
        ids = self._shop_relics.get(shop_key, set())
        return [r for rid, r in self._relics.items() if rid in ids]

    # ── Accessors ────────────────────────────────────────────────────

    @property
    def effects(self) -> list[Effect]:
        return self._effects

    @property
    def curses(self) -> list[CurseEffect]:
        return self._curses

    @property
    def relics(self) -> list[Relic]:
        return list(self._relics.values())

    def get_effect(self, ae_id: int, pool_id: int = 0) -> Effect | None:
        for e in self._effects:
            if e.id == ae_id and (pool_id == 0 or e.pool_id == pool_id):
                return e
        return None

    def get_curse(self, ae_id: int) -> CurseEffect | None:
        for c in self._curses:
            if c.id == ae_id:
                return c
        return None

    def get_relic(self, antique_id: int) -> Relic | None:
        return self._relics.get(antique_id)

    def lookup_effect_name(self, ae_id: int) -> str:
        """Look up effect name from ae_names dict directly (no shop filter)."""
        return self._ae_names.get(str(ae_id), "")

    def lookup_relic_name(self, relic_id: int) -> str:
        """Look up relic name from relic_names dict directly (no shop filter)."""
        return self._relic_names.get(str(relic_id), "")

    def get_shop_tree(self, shop_key: str, client: SmithboxClient) -> list[dict]:
        """Get the ItemTable chain for a shop (not cached — live only)."""
        cfg = SHOPS[shop_key]
        return self._unfold_chain(cfg["root"], client, set())

    # ── Loading internals ───────────────────────────────────────────

    def _load_ae_names(self, path: str):
        raw = {}
        if os.path.exists(path):
            raw = json.load(open(path, encoding="utf-8"))
        if isinstance(raw, dict):
            self._ae_names = raw.get("attachEffects", {})
            self._relic_names = raw.get("Relics", {})
        else:
            self._ae_names = raw

    def _eff_name(self, text_id: int, fallback: str = "") -> str:
        return self._ae_names.get(str(text_id), fallback or f"效果{text_id}")

    def _load_relics(self, client: SmithboxClient):
        """Load all relics from EquipParamAntique."""
        rows = client.list_param_rows("EquipParamAntique")
        for r in rows:
            row = client.get_param_row("EquipParamAntique", r["index"], vanilla=True)
            f = row["fields"]
            rid = row["row_id"]

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

            cn_name = self._relic_names.get(str(rid), "")
            base_name = row["row_name"] or f"Relic{rid}"

            self._relics[rid] = Relic(
                id=rid,
                name=cn_name if cn_name else base_name,
                color=int(f.get("relicColor", -1)),
                pool_ids=pools,
                curse_pool_ids=curse_pools,
                deep=int(f.get("isDeepRelic", 0)) == 1,
                sort_id=int(f.get("sortId", 0)),
            )

    def _load_effects(self, client: SmithboxClient):
        """Load all effects from AttachEffectTableParam pools."""
        needed_pools = set()
        for relic in self._relics.values():
            needed_pools.update(relic.pool_ids)
            needed_pools.update(relic.curse_pool_ids)

        for pool_id in needed_pools:
            self._load_pool(pool_id, client, is_curse=is_curse_pool(pool_id))

    def _resolve_aet_chain(self, ae_id: int, client: SmithboxClient,
                           visited: set | None = None) -> tuple | None:
        """Follow nested AET references to the leaf AttachEffectParam.

        Returns (chain_ids, compat_id, text_id) where chain_ids is the list
        of all ae_ids encountered along the chain (including the starting
        ae_id). Returns None if the chain can't be resolved.
        """
        if visited is None:
            visited = set()
        if ae_id in visited:
            return None  # cycle detected
        visited.add(ae_id)

        # Try AttachEffectParam — if found, this is the leaf
        aep_rows = client.get_param_rows("AttachEffectParam", ae_id, vanilla=True)
        if aep_rows and aep_rows[0]["fields"]:
            aep_f = aep_rows[0]["fields"]
            compat_id = str(aep_f.get("compatibilityId", ae_id))
            text_id = int(aep_f.get("attachTextId", ae_id))
            return ([ae_id], compat_id, text_id)

        # Not an AEP — try as a nested AET
        aet_rows = client.get_param_rows("AttachEffectTableParam", ae_id, vanilla=True)
        if aet_rows:
            for r in aet_rows:
                f = r["fields"]
                next_id = int(f.get("attachEffectId", -1))
                if next_id <= 0:
                    continue
                wt = int(f.get("chanceWeight_dlc", -1))
                if wt == -1:
                    wt = int(f.get("chanceWeight", 0))
                if wt <= 0:
                    continue
                result = self._resolve_aet_chain(next_id, client, visited)
                if result is not None:
                    chain_ids, compat_id, text_id = result
                    return ([ae_id] + chain_ids, compat_id, text_id)

        return None  # can't resolve chain

    def _create_effect_entry(self, ae_id: int, pool_id: int, name: str,
                              compat_id: str, variant, dlc_only: bool,
                              row_index: int, is_curse: bool, weight: int = 0):
        """Create and store an Effect or CurseEffect entry."""
        effect = Effect(
            id=ae_id,
            name=name,
            compat_id=compat_id,
            variant=variant,
            pool_id=pool_id,
            row_index=row_index,
            dlc_only=dlc_only,
            weight=weight,
        )
        if is_curse:
            if not any(c.id == effect.id for c in self._curses):
                self._curses.append(CurseEffect(
                    id=effect.id,
                    name=effect.name,
                    compat_id=effect.compat_id,
                    pool_id=pool_id,
                    row_index=row_index,
                    weight=weight,
                ))
        else:
            self._effects.append(effect)

    def _load_pool(self, pool_id: int, client: SmithboxClient, is_curse: bool = False):
        """Load one AETable pool, recursively expanding nested AET references."""
        rows = client.get_param_rows("AttachEffectTableParam", pool_id, vanilla=True)
        for r in rows:
            f = r["fields"]
            ae_id = int(f.get("attachEffectId", -1))
            if ae_id <= 0:
                continue

            base_wt = int(f.get("chanceWeight", 0))
            dlc_wt = int(f.get("chanceWeight_dlc", -1))
            wt = dlc_wt if dlc_wt != -1 else base_wt
            if wt <= 0:
                continue

            dlc_only = (base_wt == 0 and dlc_wt > 0)

            # Determine effect variant based on pool type (hardcoded)
            if pool_id == POOL_A:
                variant = EffectVariant.CURSED_STRONG
            elif pool_id in (POOL_B_OLD, POOL_B_NEW):
                variant = EffectVariant.CURSED_WEAK
            else:
                variant = EffectVariant.NORMAL

            # Resolve the AET chain to get proper name/compat from the leaf
            chain_result = self._resolve_aet_chain(ae_id, client)
            if chain_result is not None:
                chain_ids, compat_id, text_id = chain_result
                name = self._eff_name(text_id)

                # Create entries for ALL IDs in the chain, associated with
                # the original pool_id. This way any level of the chain
                # (e.g. 707060100 or 7060100) is discoverable in this pool.
                for chain_ae_id in chain_ids:
                    self._create_effect_entry(
                        chain_ae_id, pool_id, name, compat_id,
                        variant, dlc_only, r["row_index"], is_curse, wt,
                    )
            else:
                # Can't resolve chain — fallback to ae_id directly
                name = self._eff_name(ae_id)
                self._create_effect_entry(
                    ae_id, pool_id, name, str(ae_id),
                    variant, dlc_only, r["row_index"], is_curse, wt,
                )

    def _load_curse_pool(self, pool_id: int, client: SmithboxClient):
        """Load curse effects from a curse AETable pool, expanding nested AETs."""
        rows = client.get_param_rows("AttachEffectTableParam", pool_id, vanilla=True)
        for r in rows:
            f = r["fields"]
            ae_id = int(f.get("attachEffectId", -1))
            if ae_id <= 0:
                continue
            wt = int(f.get("chanceWeight", 0))
            if wt <= 0:
                continue

            # Resolve the AET chain to get proper name/compat from the leaf
            chain_result = self._resolve_aet_chain(ae_id, client)
            if chain_result is not None:
                chain_ids, compat_id, text_id = chain_result
                name = self._eff_name(text_id, f"诅咒{ae_id}")

                for chain_ae_id in chain_ids:
                    if not any(c.id == chain_ae_id for c in self._curses):
                        self._curses.append(CurseEffect(
                            id=chain_ae_id,
                            name=name,
                            compat_id=compat_id,
                            pool_id=pool_id,
                            row_index=r["row_index"],
                            weight=wt,
                        ))
            else:
                name = self._eff_name(ae_id, f"诅咒{ae_id}")
                if not any(c.id == ae_id for c in self._curses):
                    self._curses.append(CurseEffect(
                        id=ae_id,
                        name=name,
                        compat_id=str(ae_id),
                        pool_id=pool_id,
                        row_index=r["row_index"],
                        weight=wt,
                    ))

    def _unfold_chain(self, table_id: int, client: SmithboxClient,
                       seen: set) -> list[dict]:
        """Recursively unfold an ItemTableParam chain into leaf nodes."""
        rows = client.get_param_rows("ItemTableParam", table_id, vanilla=True)
        if not rows:
            return []
        result = []
        for r in rows:
            item_id = int(r["fields"]["itemId"])
            if item_id in seen:
                continue
            seen.add(item_id)

            dlc_wt = int(r["fields"].get("chanceWeight_dlc", -1))
            base_wt = int(r["fields"].get("chanceWeight", 0))
            wt = dlc_wt if dlc_wt != -1 else base_wt
            if wt <= 0:
                continue

            if item_id in self._relics:
                result.append({
                    "type": "relic",
                    "antique_id": item_id,
                    "table_id": table_id,
                    "item_id": item_id,
                    "row_index": r["row_index"],
                })
            else:
                children = self._unfold_chain(item_id, client, seen)
                if children:
                    result.append({
                        "type": "branch",
                        "item_id": item_id,
                        "table_id": table_id,
                        "row_index": r["row_index"],
                        "children": children,
                    })
        return result

    # ── Cache ────────────────────────────────────────────────────────

    def _save_cache(self, path: str):
        data = {
            "effects": [
                {
                    "id": e.id,
                    "name": e.name,
                    "compat_id": e.compat_id,
                    "variant": e.variant.value,
                    "pool_id": e.pool_id,
                    "row_index": e.row_index,
                    "dlc_only": e.dlc_only,
                    "weight": e.weight,
                }
                for e in self._effects
            ],
            "curses": [
                {"id": c.id, "name": c.name, "compat_id": c.compat_id,
                 "pool_id": c.pool_id, "row_index": c.row_index,
                 "weight": c.weight}
                for c in self._curses
            ],
            "relics": [
                {
                    "id": r.id,
                    "name": r.name,
                    "color": r.color,
                    "pool_ids": r.pool_ids,
                    "curse_pool_ids": r.curse_pool_ids,
                    "deep": r.deep,
                    "sort_id": r.sort_id,
                }
                for r in self._relics.values()
            ],
            "shop_relics": {
                sk: list(ids) for sk, ids in self._shop_relics.items()
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _from_cache(self, cached: dict):
        self._effects.clear()
        self._curses.clear()
        self._relics.clear()

        for e in cached.get("effects", []):
            self._effects.append(Effect(
                id=e["id"],
                name=e["name"],
                compat_id=str(e["compat_id"]),
                variant=EffectVariant(e.get("variant", "normal")),
                pool_id=e.get("pool_id", 0),
                row_index=e.get("row_index", 0),
                dlc_only=e.get("dlc_only", False),
                weight=e.get("weight", 0),
            ))
        for c in cached.get("curses", []):
            self._curses.append(CurseEffect(
                id=c["id"],
                name=c["name"],
                compat_id=str(c["compat_id"]),
                pool_id=c.get("pool_id", 0),
                row_index=c.get("row_index", 0),
                weight=c.get("weight", 0),
            ))
        for r in cached.get("relics", []):
            self._relics[r["id"]] = Relic(
                id=r["id"],
                name=r["name"],
                color=r["color"],
                pool_ids=r["pool_ids"],
                curse_pool_ids=r.get("curse_pool_ids", []),
                deep=r.get("deep", False),
                sort_id=r.get("sort_id", 0),
            )
        # Restore per-shop relic mapping
        self._shop_relics.clear()
        for sk, ids in cached.get("shop_relics", {}).items():
            self._shop_relics[sk] = set(ids)
