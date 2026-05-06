"""Abstract data source interface and implementations.

Design principle: every data query goes through a fallback chain.
If tier 1 can't answer, it falls to tier 2, then tier 3, etc.
Results from higher tiers are cached back to tier 1 (SQLite).
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path
import json
import sqlite3
import time


@dataclass
class ComponentData:
    """Complete component data from any source."""

    # Identity
    mpn: str = ""  # Manufacturer Part Number
    manufacturer: str = ""
    category: str = ""  # resistor, capacitor, IC, connector, etc.
    description: str = ""

    # Electrical
    params: Dict[str, Any] = field(default_factory=dict)
    # e.g., {"voltage_rating": "50V", "tolerance": "5%", "power": "0.25W"}

    # Package
    package: str = ""  # e.g., "0603", "SOIC-8"
    footprint_name: str = ""  # canonical KiCad footprint
    pin_count: int = 0
    pin_functions: Dict[str, str] = field(default_factory=dict)  # pin_number -> function

    # Layout constraints
    decoupling: Dict[str, str] = field(default_factory=dict)  # pin -> capacitor value
    thermal_pad: bool = False
    keepout_zones: List[dict] = field(default_factory=list)

    # Supply chain
    stock: Dict[str, int] = field(default_factory=dict)  # supplier -> quantity
    unit_price: Dict[str, float] = field(default_factory=dict)  # supplier -> price
    lead_time_weeks: int = 0

    # Source tracking
    data_source: str = ""  # "local_kb", "octopart", "digikey", "manual"
    last_updated: float = 0.0


@dataclass
class FabRules:
    """Manufacturing design rules for a specific fab house."""

    name: str  # e.g., "JLCPCB", "PCBWay"
    min_trace_width: float  # mm
    min_trace_spacing: float  # mm
    min_via_outer: float  # mm
    min_via_drill: float  # mm
    min_annular_ring: float  # mm
    min_silkscreen_width: float  # mm
    copper_thickness_outer: float  # mm (typically 0.035 = 1oz)
    copper_thickness_inner: float  # mm (typically 0.017 = 0.5oz)

    # Impedance control (if supported)
    impedance_tolerance: float = 0.10  # ±10%
    impedance_layers: List[str] = field(default_factory=list)

    # Capabilities by layer count
    layer_configs: Dict[int, dict] = field(default_factory=dict)

    @staticmethod
    def jlcpcb_2layer() -> "FabRules":
        return FabRules(
            name="JLCPCB 2-layer",
            min_trace_width=0.127, min_trace_spacing=0.127,
            min_via_outer=0.45, min_via_drill=0.2, min_annular_ring=0.13,
            min_silkscreen_width=0.15,
            copper_thickness_outer=0.035, copper_thickness_inner=0.0,
            layer_configs={2: {"max_size": "400x500mm", "colors": ["green", "blue", "red", "black", "white"]}},
        )

    @staticmethod
    def jlcpcb_4layer() -> "FabRules":
        return FabRules(
            name="JLCPCB 4-layer",
            min_trace_width=0.09, min_trace_spacing=0.09,
            min_via_outer=0.45, min_via_drill=0.2, min_annular_ring=0.13,
            min_silkscreen_width=0.15,
            copper_thickness_outer=0.035, copper_thickness_inner=0.017,
            impedance_tolerance=0.10,
            layer_configs={
                4: {"stackup": "JLC2313", "max_size": "400x500mm",
                    "impedance": "50/90/100Ω available"},
            },
        )


class DataSource(ABC):
    """Abstract data source. Implementations handle caching internally."""

    @abstractmethod
    async def query_component(self, mpn_or_value: str) -> Optional[ComponentData]:
        """Look up a component by MPN or value string."""
        ...

    @abstractmethod
    async def search_components(self, category: str, **filters) -> List[ComponentData]:
        """Search components by category with optional filters."""
        ...

    @abstractmethod
    async def get_fab_rules(self, fab_name: str) -> Optional[FabRules]:
        """Get manufacturing rules for a fab."""
        ...


class LocalComponentDB(DataSource):
    """SQLite-backed local component database with schema suitable for extension.

    Can be populated from:
    - Bundled JSON knowledge base
    - Manual CSV/JSON import
    - External API results (cached)
    - Community data
    """

    def __init__(self, db_path: str | Path = None):
        if db_path is None:
            db_path = Path.home() / ".kcad_auto_pcb" / "components.db"
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS components (
                mpn TEXT PRIMARY KEY,
                manufacturer TEXT,
                category TEXT,
                description TEXT,
                package TEXT,
                footprint_name TEXT,
                pin_count INTEGER DEFAULT 0,
                params TEXT DEFAULT '{}',           -- JSON
                pin_functions TEXT DEFAULT '{}',    -- JSON
                decoupling TEXT DEFAULT '{}',       -- JSON
                thermal_pad INTEGER DEFAULT 0,
                stock TEXT DEFAULT '{}',
                unit_price TEXT DEFAULT '{}',
                data_source TEXT DEFAULT 'local_kb',
                last_updated REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_category ON components(category);
            CREATE INDEX IF NOT EXISTS idx_package ON components(package);
            CREATE INDEX IF NOT EXISTS idx_manufacturer ON components(manufacturer);

            CREATE TABLE IF NOT EXISTS fab_rules (
                name TEXT PRIMARY KEY,
                rules_json TEXT NOT NULL,
                last_updated REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS data_sources (
                name TEXT PRIMARY KEY,
                config TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1
            );
        """)
        self._conn.commit()

    async def query_component(self, mpn_or_value: str) -> Optional[ComponentData]:
        # Normalize search
        key = mpn_or_value.upper().strip()
        row = self._conn.execute(
            "SELECT * FROM components WHERE mpn = ?", (key,)
        ).fetchone()

        if not row:
            # Try fuzzy match on description
            row = self._conn.execute(
                "SELECT * FROM components WHERE mpn LIKE ? LIMIT 1",
                (f"%{key}%",)
            ).fetchone()

        if row:
            return self._row_to_component(row)
        return None

    async def search_components(self, category: str, **filters) -> List[ComponentData]:
        query = "SELECT * FROM components WHERE category = ?"
        params = [category]
        if "package" in filters:
            query += " AND package = ?"
            params.append(filters["package"])

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_component(r) for r in rows]

    async def get_fab_rules(self, fab_name: str) -> Optional[FabRules]:
        row = self._conn.execute(
            "SELECT rules_json FROM fab_rules WHERE name = ?", (fab_name,)
        ).fetchone()
        if row:
            return FabRules(**json.loads(row[0]))
        return None

    def import_from_json(self, path: str | Path):
        """Import components from a JSON file.

        Expected format: { "components": [ { "mpn": "...", ... }, ... ] }
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for comp in data.get("components", []):
            self._upsert_component(ComponentData(**comp))

    def _upsert_component(self, c: ComponentData):
        self._conn.execute("""
            INSERT OR REPLACE INTO components
            (mpn, manufacturer, category, description, package, footprint_name,
             pin_count, params, pin_functions, decoupling, thermal_pad,
             stock, unit_price, data_source, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c.mpn.upper(), c.manufacturer, c.category, c.description,
            c.package, c.footprint_name, c.pin_count,
            json.dumps(c.params), json.dumps(c.pin_functions),
            json.dumps(c.decoupling), int(c.thermal_pad),
            json.dumps(c.stock), json.dumps(c.unit_price),
            c.data_source, time.time(),
        ))
        self._conn.commit()

    def _row_to_component(self, row) -> ComponentData:
        cols = ["mpn", "manufacturer", "category", "description", "package",
                "footprint_name", "pin_count", "params", "pin_functions",
                "decoupling", "thermal_pad", "stock", "unit_price",
                "data_source", "last_updated"]
        d = dict(zip(cols, row))
        for f in ("params", "pin_functions", "decoupling", "stock", "unit_price"):
            d[f] = json.loads(d[f]) if isinstance(d[f], str) else d[f]
        d["thermal_pad"] = bool(d["thermal_pad"])
        return ComponentData(**d)


class FallbackDataSource(DataSource):
    """Chain multiple data sources with tiered fallback.

    Tier 1 results are cached to avoid re-querying.
    """

    def __init__(self, *sources: DataSource):
        self.sources = sources

    async def query_component(self, mpn_or_value: str) -> Optional[ComponentData]:
        for i, source in enumerate(self.sources):
            result = await source.query_component(mpn_or_value)
            if result:
                # Cache back to tier 1 if found in higher tier
                if i > 0 and hasattr(self.sources[0], '_upsert_component'):
                    self.sources[0]._upsert_component(result)
                return result
        return None

    async def search_components(self, category: str, **filters) -> List[ComponentData]:
        seen = set()
        results = []
        for source in self.sources:
            items = await source.search_components(category, **filters)
            for item in items:
                if item.mpn not in seen:
                    seen.add(item.mpn)
                    results.append(item)
        return results

    async def get_fab_rules(self, fab_name: str) -> Optional[FabRules]:
        for source in self.sources:
            result = await source.get_fab_rules(fab_name)
            if result:
                return result
        return None
