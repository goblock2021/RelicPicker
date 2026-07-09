"""
Relic matcher — given a set of effects + filters, find matching relics.
"""

from collections import defaultdict
from typing import Optional
from models import (
    Effect, EffectVariant, CurseEffect, Relic,
    SlotAssignment, RelicMatch,
)


class Matcher:
    """Stateless matcher — takes loader data, effects, and filters."""

    def __init__(self, relics: list[Relic], effects: list[Effect],
                 curses: list[CurseEffect]):
        self._relics = relics
        self._effects_by_id = defaultdict(list)
        for e in effects:
            self._effects_by_id[e.id].append(e)
        self._curses_by_id = {c.id: c for c in curses}

    def match(self,
              selected_effects: list[dict],  # [{eff_id, curse_id?}, ...]
              color: int = -1,
              shop_is_deep: bool = False,
              relics: Optional[list[Relic]] = None,
              ) -> list[RelicMatch]:
        """
        Find relics that can hold all selected effects.
        If `relics` is provided, only match against those relics.
        """
        candidate_relics = relics if relics is not None else self._relics

        if not selected_effects:
            return self._all_relics(color, candidate_relics)

        # Resolve effect objects
        resolved = self._resolve_effects(selected_effects)
        if not resolved:
            return []

        matches = []
        for relic in candidate_relics:
            if color != -1 and relic.color != color:
                continue

            # Deep shop: only deep relics
            if shop_is_deep and not relic.deep:
                continue

            # Non-deep shop: exclude deep relics
            if not shop_is_deep and relic.deep:
                continue

            assignment = self._try_assign(resolved, relic, shop_is_deep)
            if assignment is not None:
                matches.append(RelicMatch(relic=relic, assignments=assignment))

        # Sort: fewer total slots first (simpler relics first)
        matches.sort(key=lambda m: (m.relic.total_slots, m.relic.sort_id))
        return matches

    # ── Internals ────────────────────────────────────────────────────

    def _all_relics(self, color: int, relics: list[Relic]) -> list[RelicMatch]:
        """Return all candidate relics (when no effects selected)."""
        if color != -1:
            relics = [r for r in relics if r.color == color]
        return [RelicMatch(relic=r, assignments=[]) for r in relics]

    def _resolve_effects(self, selected: list[dict]) -> list[dict]:
        """Resolve effect IDs to Effect objects (all pool variants).
        Returns enriched list with 'variants' key.
        """
        result = []
        for item in selected:
            variants = self._effects_by_id.get(item["eff_id"], [])
            if not variants:
                return []
            curse = None
            if item.get("curse_id"):
                curse = self._curses_by_id.get(item["curse_id"])
            result.append({"variants": variants, "curse": curse})
        return result

    def _try_assign(self, resolved: list[dict], relic: Relic,
                     shop_is_deep: bool) -> Optional[list[SlotAssignment]]:
        """Try to assign each resolved effect to a relic slot.
        Uses backtracking to find a valid assignment.
        Each effect may have multiple pool variants; the correct variant
        is selected based on which relic slot (pool_id) it lands in.
        """
        n_effects = len(resolved)

        # Determine available slots
        if shop_is_deep and relic.deep:
            # All pool slots + curse slots from deep relic
            all_pool_ids = list(relic.pool_ids) + list(relic.curse_pool_ids)
        else:
            all_pool_ids = list(relic.pool_ids)

        total_slots = len(all_pool_ids)
        if n_effects > total_slots:
            return None

        # Precompute: for each effect, which slots could hold it
        # A slot is eligible only if the effect has a pool variant
        # matching the slot's pool_id.
        candidates = []
        for i, item in enumerate(resolved):
            variants = item["variants"]

            # Build a map: pool_id -> Effect variant
            pool_variants = {}
            for v in variants:
                if v.pool_id not in pool_variants:
                    pool_variants[v.pool_id] = v

            # Pre-filter: a slot is eligible only if this effect
            # has a variant matching the slot's pool_id.
            eligible = []
            for si, pid in enumerate(all_pool_ids):
                is_curse_slot = si >= relic.pool_count

                # Must have a variant for this slot's pool
                matching_variant = pool_variants.get(pid)
                if matching_variant is None:
                    continue

                needs_curse = matching_variant.variant == EffectVariant.CURSED_STRONG

                if needs_curse:
                    if is_curse_slot:
                        continue
                    if not relic.curse_pool_ids:
                        continue
                else:
                    if is_curse_slot:
                        continue

                eligible.append(si)

            if not eligible:
                return None
            candidates.append((i, eligible, pool_variants))

        # Sort by fewest candidates (most constrained first)
        candidates.sort(key=lambda x: len(x[1]))

        used = [False] * total_slots
        result = [None] * n_effects
        compat_ids_used = set()

        def backtrack(idx):
            if idx == n_effects:
                return True

            eff_idx, eligible, pool_variants = candidates[idx]
            item = resolved[eff_idx]
            curse = item["curse"]

            for si in eligible:
                if used[si]:
                    continue

                pid = all_pool_ids[si]
                eff = pool_variants[pid]

                # Compat ID conflict check
                if eff.compat_id in compat_ids_used:
                    continue

                # Curse compat check (each curse has unique compatId)
                if curse and curse.compat_id in compat_ids_used:
                    continue

                used[si] = True
                compat_ids_used.add(eff.compat_id)
                if curse:
                    compat_ids_used.add(curse.compat_id)

                result[eff_idx] = SlotAssignment(
                    slot_index=si,
                    pool_id=pid,
                    effect=eff,
                    curse=curse if eff.variant == EffectVariant.CURSED_STRONG else None,
                )

                if backtrack(idx + 1):
                    return True

                used[si] = False
                compat_ids_used.discard(eff.compat_id)
                if curse:
                    compat_ids_used.discard(curse.compat_id)
                result[eff_idx] = None

            return False

        if backtrack(0):
            return result
        return None
