from __future__ import annotations

from enum import Enum
from typing import Any


def dump(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return pythonize(model.model_dump(mode="json"))
    return pythonize(model)


def pythonize(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.value
    type_name = type(obj).__name__
    if type_name.startswith("int") and hasattr(obj, "item"):
        return int(obj.item())
    if type_name.startswith("float") and hasattr(obj, "item"):
        return float(obj.item())
    if hasattr(obj, "item") and type(obj).__module__ == "numpy":
        return pythonize(obj.item())
    if isinstance(obj, dict):
        return {str(k): pythonize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [pythonize(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return dump(obj)
    return obj
