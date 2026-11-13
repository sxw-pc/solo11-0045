"""Registry of dimensions and ratio units loaded from bundled YAML."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from .temperature import TemperatureError, convert_temperature


class UnitRegistryError(ValueError):
    """Base error for registry lookups."""


class UnknownDimensionError(UnitRegistryError):
    pass


class UnknownUnitError(UnitRegistryError):
    pass


@dataclass(frozen=True)
class UnitSpec:
    name: str
    dimension: str
    factor: Fraction | None
    affine: bool = False


@dataclass(frozen=True)
class DimensionSpec:
    name: str
    base_unit: str
    units: dict[str, UnitSpec]


def _parse_factor(raw: Any, dimension: str, unit: str) -> Fraction:
    try:
        factor = Fraction(str(raw))
    except (ValueError, ZeroDivisionError) as exc:
        raise UnitRegistryError(
            f"invalid factor for {dimension}.{unit}: {raw!r}"
        ) from exc
    if factor <= 0:
        raise UnitRegistryError(f"factor must be positive for {dimension}.{unit}")
    return factor


class UnitRegistry:
    """Immutable-ish lookup table with exact rational conversion factors."""

    def __init__(self, dimensions: dict[str, DimensionSpec]) -> None:
        if not dimensions:
            raise UnitRegistryError("registry has no dimensions")
        self._dimensions = dict(dimensions)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "UnitRegistry":
        defaults_path = Path(path)
        try:
            payload = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise UnitRegistryError(f"cannot load unit catalogue: {exc}") from exc
        raw_dimensions = (payload or {}).get("dimensions")
        if not isinstance(raw_dimensions, dict) or not raw_dimensions:
            raise UnitRegistryError("unit catalogue must contain a dimensions mapping")

        dimensions: dict[str, DimensionSpec] = {}
        for dimension_name, raw_dimension in raw_dimensions.items():
            if not isinstance(raw_dimension, dict):
                raise UnitRegistryError(f"dimension {dimension_name} must be a mapping")
            base_unit = raw_dimension.get("base_unit")
            raw_units = raw_dimension.get("units")
            if not isinstance(base_unit, str) or not isinstance(raw_units, dict):
                raise UnitRegistryError(f"dimension {dimension_name} is malformed")
            units: dict[str, UnitSpec] = {}
            for unit_name, raw_spec in raw_units.items():
                if not isinstance(unit_name, str) or not unit_name:
                    raise UnitRegistryError("unit names must be non-empty strings")
                if not isinstance(raw_spec, dict):
                    raise UnitRegistryError(
                        f"unit {dimension_name}.{unit_name} must be a mapping"
                    )
                affine = bool(raw_spec.get("affine", False))
                factor = None if affine else _parse_factor(
                    raw_spec.get("factor"), str(dimension_name), unit_name
                )
                units[unit_name] = UnitSpec(
                    name=unit_name,
                    dimension=str(dimension_name),
                    factor=factor,
                    affine=affine,
                )
            if base_unit not in units:
                raise UnitRegistryError(
                    f"base unit {base_unit} missing from dimension {dimension_name}"
                )
            dimensions[str(dimension_name)] = DimensionSpec(
                name=str(dimension_name),
                base_unit=base_unit,
                units=units,
            )
        return cls(dimensions)

    @classmethod
    def bundled(cls) -> "UnitRegistry":
        path = Path(__file__).resolve().parents[1] / "contract" / "defaults.yaml"
        return cls.from_yaml(path)

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(self._dimensions)

    def has_dimension(self, dimension: str) -> bool:
        return dimension in self._dimensions

    def get_dimension(self, dimension: str) -> DimensionSpec:
        try:
            return self._dimensions[dimension]
        except KeyError as exc:
            raise UnknownDimensionError(f"unknown dimension: {dimension}") from exc

    def units_for(self, dimension: str) -> tuple[str, ...]:
        return tuple(self.get_dimension(dimension).units)

    def base_unit(self, dimension: str) -> str:
        return self.get_dimension(dimension).base_unit

    def get_unit(self, dimension: str, unit: str) -> UnitSpec:
        spec = self.get_dimension(dimension).units.get(unit)
        if spec is None:
            raise UnknownUnitError(f"unit {unit!r} is not registered for {dimension}")
        return spec

    def has_unit(self, dimension: str, unit: str) -> bool:
        return self.has_dimension(dimension) and unit in self._dimensions[dimension].units

    def known_dimensions_for(self, unit: str) -> tuple[str, ...]:
        return tuple(
            name
            for name, dimension in self._dimensions.items()
            if unit in dimension.units
        )

    def is_affine(self, dimension: str) -> bool:
        return all(spec.affine for spec in self.get_dimension(dimension).units.values())

    def convert(
        self,
        value: Fraction,
        dimension: str,
        from_unit: str,
        to_unit: str,
    ) -> Fraction:
        """Convert exactly, dispatching affine temperatures separately."""
        if self.is_affine(dimension):
            try:
                return convert_temperature(value, from_unit, to_unit)
            except TemperatureError:
                raise
        source = self.get_unit(dimension, from_unit)
        target = self.get_unit(dimension, to_unit)
        if source.factor is None or target.factor is None:
            raise UnitRegistryError(f"ratio unit lacks a factor in {dimension}")
        return value * source.factor / target.factor

    def ratio(self, dimension: str, from_unit: str, to_unit: str) -> Fraction:
        source = self.get_unit(dimension, from_unit)
        target = self.get_unit(dimension, to_unit)
        if source.factor is None or target.factor is None:
            raise UnitRegistryError("ratio lookup called for affine units")
        return source.factor / target.factor
