from __future__ import annotations

from types import UnionType
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, create_model


def relaxed(annotation):
    """Draft shape only; full domain validators are mandatory on publication."""
    if annotation is int or annotation is float:
        return StrictFloat
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return create_model(
            "Draft" + annotation.__name__,
            __config__=ConfigDict(extra="forbid", allow_inf_nan=False),
            **{
                k: (
                    relaxed(f.annotation),
                    Field(default_factory=f.default_factory) if f.default_factory else f.default,
                )
                for k, f in annotation.model_fields.items()
            },
        )
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return relaxed(args[0])
    if origin is dict:
        return dict[relaxed(args[0]), relaxed(args[1])]
    if origin is list:
        return list[relaxed(args[0])]
    if origin in (Union, UnionType):
        return Union[tuple(relaxed(a) for a in args)]
    return annotation
