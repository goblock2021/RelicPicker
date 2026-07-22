"""
Relic Picker v5 — data models.
Pure data classes with no external dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EffectVariant(Enum):
    NORMAL = "normal"             # universal effect
    CURSED_STRONG = "cursed-strong"   # deep-only, stronger, needs curse
    CURSED_WEAK = "cursed-weak"       # deep-only, weaker, no curse


@dataclass
class Effect:
    """A selectable affix effect."""
    id: int                       # aeId
    name: str                     # Chinese name from names.json
    compat_id: str                # compatibility group — effects sharing a compat_id conflict
    variant: EffectVariant        # normal / cursed-strong / cursed-weak
    pool_id: int = 0              # which AETable pool this effect belongs to
    row_index: int = 0            # row index in AttachEffectTableParam
    dlc_only: bool = False
    weight: int = 0               # chanceWeight from AET pool entry

    def __hash__(self):
        return hash(self.id)


@dataclass
class CurseEffect:
    """A curse that can be paired with a cursed-strong effect."""
    id: int                       # curse aeId
    name: str
    compat_id: str
    pool_id: int = 0              # which AETable curse pool this belongs to
    row_index: int = 0            # row index in AttachEffectTableParam
    weight: int = 0               # chanceWeight from AET pool entry


@dataclass
class Relic:
    """A relic (antique) that carries effects."""
    id: int                       # antique ID
    name: str
    color: int                    # 0=Burning 1=Drizzly 2=Luminous 3=Tranquil
    pool_ids: list[int] = field(default_factory=list)       # main effect pool IDs
    curse_pool_ids: list[int] = field(default_factory=list) # curse pool IDs
    deep: bool = False
    sort_id: int = 0

    @property
    def pool_count(self) -> int:
        return len(self.pool_ids)

    @property
    def curse_pool_count(self) -> int:
        return len(self.curse_pool_ids)

    @property
    def total_slots(self) -> int:
        return len(self.pool_ids) + len(self.curse_pool_ids)


@dataclass
class SlotAssignment:
    """One selected effect assigned to a relic slot."""
    slot_index: int
    pool_id: int
    effect: Effect
    curse: Optional[CurseEffect] = None


@dataclass
class RelicMatch:
    """A relic that matches the current effect selection."""
    relic: Relic
    assignments: list[SlotAssignment]

    @property
    def is_complete(self) -> bool:
        return len(self.assignments) == self.relic.total_slots


@dataclass
class BoxItem:
    """A saved relic configuration in the relic box."""
    id: int                      # unique ID, never reused
    effects: list[dict]          # [{eff_id, curse_id}, ...]
    shop: str
    color: int
    added_at: str
    relic_id: int = 0


@dataclass
class BoxFolder:
    """A named collection — references item IDs."""
    name: str
    item_ids: list[int]
    added_at: str


@dataclass
class AppState:
    """Complete application state, serialized to frontend."""
    shop: str = "normal-old"
    color: int = 0
    effects: list[dict] = field(default_factory=list)   # [{eff_id, curse_id}]
    favorites: list[int] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)    # serialized RelicMatch
    box_count: int = 0
    status: str = "incomplete"  # incomplete | error | ready
    status_message: str = ""
