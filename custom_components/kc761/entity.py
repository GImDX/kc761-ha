"""Shared KC761 entity helpers."""

from __future__ import annotations

from typing import Any

from .const import DOMAIN
from .protocol.models import DeviceInfo


def kc761_device_info(info: DeviceInfo) -> dict[str, Any]:
    """Return one consistent Home Assistant Device Registry description."""
    return {
        "identifiers": {(DOMAIN, info.device_id)},
        "name": f"{info.model_name} {info.device_id[-4:]}",
        "model": info.model_name,
        "model_id": str(info.device_model),
        "hw_version": info.hw_version_text,
        "sw_version": info.fw_version_text,
        "serial_number": info.device_id,
    }
