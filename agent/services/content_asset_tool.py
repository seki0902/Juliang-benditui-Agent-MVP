"""Content-asset Tool with an explicit, injectable source boundary.

The current reporting pipeline stores material IDs and performance metrics, but
does not provide a verified title, script, transcript, or video asset source.
This module therefore keeps the Agent-facing contract stable while making the
content source an adapter.  With no adapter configured the Tool returns
``unavailable``; it never derives content from a material name or a metric.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from .metrics_tool_utils import normalise_id
from .tool_protocol import tool_protocol


_TEXT_FIELDS = ("title", "script", "transcript", "caption", "hook", "cta", "visual_notes")
_LIST_FIELDS = ("selling_points", "tags")
_CONTENT_FIELDS = (*_TEXT_FIELDS, *_LIST_FIELDS)
_FIELD_ALIASES = {
    "title": ("title", "标题"),
    "script": ("script", "口播稿"),
    "transcript": ("transcript", "video_transcript", "视频转写", "转写"),
    "caption": ("caption", "文案", "广告文案"),
    "hook": ("hook", "开头", "开场", "开头钩子"),
    "selling_points": ("selling_points", "卖点", "sellingPoints"),
    "cta": ("cta", "行动指引", "行动号召"),
    "tags": ("tags", "标签", "内容标签"),
    "visual_notes": ("visual_notes", "画面要点", "画面"),
}


class ContentAssetAdapter(Protocol):
    """Minimal contract for a future content-asset source."""

    source: str

    def get(self, material_id: str) -> Mapping[str, Any] | None:
        """Return one canonical or mappable content record, if it exists."""


@dataclass
class MappingContentAssetAdapter:
    """Small adapter useful for tests and for a future imported asset mapping."""

    assets: Mapping[str, Mapping[str, Any]]
    source: str = "content_asset_mapping"

    def get(self, material_id: str) -> Mapping[str, Any] | None:
        direct = self.assets.get(material_id)
        if direct is not None:
            return direct
        # Keep ID matching consistent with the metrics Tools (e.g. 1001.0).
        for raw_id, asset in self.assets.items():
            if normalise_id(raw_id) == material_id:
                return asset
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _clean_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return [text for item in value if (text := _clean_text(item)) is not None]
    text = _clean_text(value)
    return [text] if text is not None else None


def _first_value(asset: Mapping[str, Any], field: str) -> Any:
    for name in _FIELD_ALIASES[field]:
        if name in asset and asset[name] is not None:
            return asset[name]
    return None


def _normalise_asset(asset: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    content: dict[str, Any] = {}
    for field in _TEXT_FIELDS:
        content[field] = _clean_text(_first_value(asset, field))
    for field in _LIST_FIELDS:
        content[field] = _clean_list(_first_value(asset, field))
    source = _clean_text(asset.get("source") or asset.get("来源"))
    return content, source


def _has_content(content: Mapping[str, Any]) -> bool:
    return any(
        value is not None and (not isinstance(value, list) or bool(value))
        for value in content.values()
    )


def _empty_content() -> dict[str, Any]:
    return {field: None for field in _CONTENT_FIELDS}


def _adapter_source(adapter: Any, fallback: str) -> str | None:
    return _clean_text(getattr(adapter, "source", None)) or fallback


def _lookup(adapter: Any, material_id: str) -> Mapping[str, Any] | None:
    getter = getattr(adapter, "get", None)
    if callable(getter):
        return getter(material_id)
    getter = getattr(adapter, "get_content_asset", None)
    if callable(getter):
        return getter(material_id)
    if callable(adapter):
        return adapter(material_id)
    raise TypeError("content asset adapter must expose get(material_id)")


def _result(
    material_id: str | None,
    *,
    status: str,
    asset_status: str,
    source: str | None,
    reason: str | None = None,
    content: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "found": status == "ok",
        "material_id": material_id,
        **(_empty_content() if content is None else dict(content)),
        "source": source,
        "asset_status": asset_status,
        "warnings": [reason] if reason else [],
    }
    if reason:
        result["reason"] = reason
    return result


@dataclass
class ContentAssetTool:
    """Read one material's verified content asset through an optional adapter."""

    adapter: ContentAssetAdapter | Mapping[str, Mapping[str, Any]] | None = None
    default_source: str = "content_asset_adapter"

    def __post_init__(self) -> None:
        if isinstance(self.adapter, Mapping):
            self.adapter = MappingContentAssetAdapter(
                assets=self.adapter, source="content_asset_mapping"
            )

    @classmethod
    def from_mapping(
        cls,
        assets: Mapping[str, Mapping[str, Any]],
        source: str = "content_asset_mapping",
    ) -> "ContentAssetTool":
        return cls(MappingContentAssetAdapter(assets=assets, source=source))

    @tool_protocol(tool_name="get_content_asset", time_window_arg=None)
    def get_content_asset(self, material_id: str | None) -> dict[str, Any]:
        normalized_id = normalise_id(material_id)
        if not normalized_id:
            return _result(
                None,
                status="unavailable",
                asset_status="missing_material_id",
                source=None,
                reason="必须提供 material_id 才能读取素材内容",
            )

        if self.adapter is None:
            return _result(
                normalized_id,
                status="unavailable",
                asset_status="missing_adapter",
                source=None,
                reason="当前没有配置可靠的素材内容资产来源；不能用素材名称或投放指标代替内容",
            )

        source = _adapter_source(self.adapter, self.default_source)
        try:
            asset = _lookup(self.adapter, normalized_id)
        except Exception as exc:  # Adapter errors remain structured for the Agent.
            return _result(
                normalized_id,
                status="unavailable",
                asset_status="adapter_error",
                source=source,
                reason=f"素材内容 adapter 读取失败：{exc}",
            )

        if asset is None:
            return _result(
                normalized_id,
                status="unavailable",
                asset_status="not_found",
                source=source,
                reason=f"内容资产来源中没有 material_id={normalized_id!r} 的记录",
            )
        if not isinstance(asset, Mapping):
            return _result(
                normalized_id,
                status="unavailable",
                asset_status="invalid_record",
                source=source,
                reason="内容资产 adapter 返回的记录不是结构化对象",
            )

        content, asset_source = _normalise_asset(asset)
        if not _has_content(content):
            return _result(
                normalized_id,
                status="unavailable",
                asset_status="empty_record",
                source=asset_source or source,
                reason="内容资产记录为空；不能将素材名称或指标当作正文",
            )

        return _result(
            normalized_id,
            status="ok",
            asset_status="available",
            source=asset_source or source,
            content=content,
        )


@tool_protocol(tool_name="get_content_asset", time_window_arg=None)
def get_content_asset(
    material_id: str | None,
    adapter: ContentAssetAdapter | Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a structured content asset or an explicit unavailable result."""
    return ContentAssetTool(adapter=adapter).get_content_asset(material_id)
