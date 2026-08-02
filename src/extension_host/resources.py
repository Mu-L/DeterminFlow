"""Layered JSON resources with extension-owned copy-on-write overrides."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from src.extension_api.registrar import OwnedPath


class ResourceConflictError(ValueError):
    pass


@dataclass(frozen=True)
class _OwnedItem:
    owner: str
    value: Any


class LayeredJsonConfig:
    """Resolve core and extension JSON documents while preserving ownership.

    Extension values are immutable defaults. Changes made through existing CRUD
    APIs are persisted as owner-scoped overrides instead of modifying extension
    source files.
    """

    def __init__(
        self,
        base_file: Path,
        layers: Iterable[OwnedPath] = (),
        *,
        dict_sections: Iterable[str] = (),
        list_sections: Iterable[str] = (),
        override_file: Path | None = None,
        owner_enabled: Callable[[str], bool] | None = None,
    ):
        self.base_file = Path(base_file)
        self.layers = list(layers)
        self.dict_sections = tuple(dict_sections)
        self.list_sections = tuple(list_sections)
        self.override_file = override_file or (
            self.base_file.parent / "extension-overrides" / self.base_file.name
        )
        self.owner_enabled = owner_enabled or (lambda owner: True)

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件顶层必须是对象: {path}")
        return data

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    @staticmethod
    def _list_by_id(values: Any, source: Path | None = None) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for value in values or []:
            if not isinstance(value, dict) or not value.get("id"):
                label = f" in {source}" if source else ""
                raise ValueError(f"列表资源必须包含 id{label}")
            result[str(value["id"])] = value
        return result

    def _load_sources(
        self,
        *,
        include_inactive: bool = False,
    ) -> tuple[dict, dict[tuple[str, str], _OwnedItem]]:
        base = self._read(self.base_file)
        owned: dict[tuple[str, str], _OwnedItem] = {}

        for layer in self.layers:
            if not include_inactive and not self.owner_enabled(layer.owner):
                continue
            document = self._read(layer.path)
            for section in self.dict_sections:
                values = document.get(section, {})
                if not isinstance(values, dict):
                    raise ValueError(f"{layer.path}: {section} 必须是对象")
                for item_id, value in values.items():
                    key = (section, str(item_id))
                    if item_id in base.get(section, {}) or key in owned:
                        raise ResourceConflictError(
                            f"扩展资源冲突: {section}.{item_id} ({layer.owner})"
                        )
                    owned[key] = _OwnedItem(layer.owner, copy.deepcopy(value))

            for section in self.list_sections:
                base_values = self._list_by_id(base.get(section, []), self.base_file)
                for item_id, value in self._list_by_id(document.get(section, []), layer.path).items():
                    key = (section, item_id)
                    if item_id in base_values or key in owned:
                        raise ResourceConflictError(
                            f"扩展资源冲突: {section}.{item_id} ({layer.owner})"
                        )
                    owned[key] = _OwnedItem(layer.owner, copy.deepcopy(value))
        return base, owned

    def validate_sources(self) -> None:
        """Parse all declared layers and validate section shape and ownership."""
        self._load_sources(include_inactive=True)

    def load(self) -> dict:
        base, owned = self._load_sources()
        _, all_owned = self._load_sources(include_inactive=True)
        result = copy.deepcopy(base)

        for (section, item_id), item in owned.items():
            if section in self.dict_sections:
                result.setdefault(section, {})[item_id] = copy.deepcopy(item.value)
            else:
                values = self._list_by_id(result.get(section, []))
                values[item_id] = copy.deepcopy(item.value)
                result[section] = list(values.values())

        overrides = copy.deepcopy(
            self._read(self.override_file).get("extensions", {})
        )
        overrides = self._sanitize_overrides(overrides, all_owned)
        for owner, owner_data in overrides.items():
            if not self.owner_enabled(owner):
                continue
            values_by_section = owner_data.get("values", {})
            deleted_by_section = owner_data.get("deleted", {})
            for section in self.dict_sections:
                target = result.setdefault(section, {})
                target.update(copy.deepcopy(values_by_section.get(section, {})))
                for item_id in deleted_by_section.get(section, []):
                    target.pop(item_id, None)
            for section in self.list_sections:
                target = self._list_by_id(result.get(section, []))
                target.update(copy.deepcopy(values_by_section.get(section, {})))
                for item_id in deleted_by_section.get(section, []):
                    target.pop(item_id, None)
                result[section] = list(target.values())

        return result

    def save(self, resolved: dict) -> None:
        base, owned = self._load_sources()
        _, all_owned = self._load_sources(include_inactive=True)
        override_root = self._read(self.override_file)
        extension_overrides = self._sanitize_overrides(
            override_root.get("extensions", {}),
            all_owned,
        )
        override_root["extensions"] = extension_overrides

        for section in self.dict_sections:
            resolved_values = copy.deepcopy(resolved.get(section, {}))
            extension_ids = {
                item_id for (item_section, item_id), _ in all_owned.items()
                if item_section == section
            }
            base[section] = {
                item_id: value
                for item_id, value in resolved_values.items()
                if item_id not in extension_ids
            }
            self._update_overrides(section, resolved_values, owned, extension_overrides)

        for section in self.list_sections:
            resolved_values = self._list_by_id(resolved.get(section, []))
            extension_ids = {
                item_id for (item_section, item_id), _ in all_owned.items()
                if item_section == section
            }
            base[section] = [
                value for item_id, value in resolved_values.items()
                if item_id not in extension_ids
            ]
            self._update_overrides(section, resolved_values, owned, extension_overrides)

        extension_overrides = self._sanitize_overrides(
            extension_overrides,
            all_owned,
        )
        override_root["extensions"] = extension_overrides

        managed_sections = set(self.dict_sections) | set(self.list_sections)
        for key, value in resolved.items():
            if key not in managed_sections:
                base[key] = copy.deepcopy(value)

        self._atomic_write(self.base_file, base)
        self._atomic_write(self.override_file, override_root)

    def _sanitize_overrides(
        self,
        overrides: Any,
        owned: dict[tuple[str, str], _OwnedItem],
    ) -> dict:
        """Drop overrides that no longer belong to their declared extension."""
        if not isinstance(overrides, dict):
            return {}

        allowed: dict[str, dict[str, set[str]]] = {}
        for (section, item_id), item in owned.items():
            allowed.setdefault(item.owner, {}).setdefault(section, set()).add(item_id)

        sanitized: dict[str, dict[str, Any]] = {}
        for owner, owner_data in overrides.items():
            if owner not in allowed or not isinstance(owner_data, dict):
                continue
            owner_values: dict[str, dict] = {}
            owner_deleted: dict[str, list[str]] = {}
            raw_values = owner_data.get("values", {})
            raw_deleted = owner_data.get("deleted", {})

            for section, item_ids in allowed[owner].items():
                section_values = (
                    raw_values.get(section, {})
                    if isinstance(raw_values, dict)
                    else {}
                )
                if isinstance(section_values, dict):
                    filtered_values = {}
                    for raw_item_id, value in section_values.items():
                        item_id = str(raw_item_id)
                        if item_id not in item_ids:
                            continue
                        if section in self.list_sections and (
                            not isinstance(value, dict)
                            or str(value.get("id", "")) != item_id
                        ):
                            continue
                        filtered_values[item_id] = copy.deepcopy(value)
                    if filtered_values:
                        owner_values[section] = filtered_values

                section_deleted = (
                    raw_deleted.get(section, [])
                    if isinstance(raw_deleted, dict)
                    else []
                )
                if isinstance(section_deleted, list):
                    filtered_deleted = sorted({
                        str(item_id)
                        for item_id in section_deleted
                        if str(item_id) in item_ids
                    })
                    if filtered_deleted:
                        owner_deleted[section] = filtered_deleted

            if owner_values or owner_deleted:
                sanitized[owner] = {
                    "values": owner_values,
                    "deleted": owner_deleted,
                }
        return sanitized

    @staticmethod
    def _update_overrides(
        section: str,
        resolved_values: dict[str, Any],
        owned: dict[tuple[str, str], _OwnedItem],
        extension_overrides: dict,
    ) -> None:
        section_items = {
            item_id: item
            for (item_section, item_id), item in owned.items()
            if item_section == section
        }
        for item_id, item in section_items.items():
            owner_data = extension_overrides.setdefault(
                item.owner,
                {"values": {}, "deleted": {}},
            )
            values = owner_data.setdefault("values", {}).setdefault(section, {})
            deleted = owner_data.setdefault("deleted", {}).setdefault(section, [])
            if item_id not in resolved_values:
                values.pop(item_id, None)
                if item_id not in deleted:
                    deleted.append(item_id)
            elif resolved_values[item_id] != item.value:
                values[item_id] = copy.deepcopy(resolved_values[item_id])
                if item_id in deleted:
                    deleted.remove(item_id)
            else:
                values.pop(item_id, None)
                if item_id in deleted:
                    deleted.remove(item_id)

        for owner in list(extension_overrides):
            owner_data = extension_overrides[owner]
            owner_data["values"] = {
                key: value for key, value in owner_data.get("values", {}).items() if value
            }
            owner_data["deleted"] = {
                key: value for key, value in owner_data.get("deleted", {}).items() if value
            }
            if not owner_data["values"] and not owner_data["deleted"]:
                extension_overrides.pop(owner)
