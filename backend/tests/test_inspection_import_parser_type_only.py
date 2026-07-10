from backend.services.import_parser import parse_imports


def _import_by_module(parsed, module):
    for item in parsed.imports:
        if item.module == module:
            return item
    raise AssertionError(f"missing import for module {module!r}: {parsed.imports!r}")


def test_typescript_import_type_is_marked_type_only():
    parsed = parse_imports(
        "src/types.ts",
        """
import type { User, Config as Cfg } from "./types";
import React from "react";
""",
    )

    type_import = _import_by_module(parsed, "./types")
    runtime_import = _import_by_module(parsed, "react")

    assert type_import.is_type_only is True
    assert type_import.names == ["User", "Config"]
    assert runtime_import.is_type_only is False


def test_typescript_named_type_specifier_is_marked_type_only():
    parsed = parse_imports(
        "src/types.ts",
        """
import { type UserModel } from "./models";
import { createClient } from "./client";
""",
    )

    type_import = _import_by_module(parsed, "./models")
    runtime_import = _import_by_module(parsed, "./client")

    assert type_import.is_type_only is True
    assert type_import.names == ["UserModel"]
    assert runtime_import.is_type_only is False
    assert runtime_import.names == ["createClient"]


def test_python_type_checking_block_import_is_marked_type_only():
    parsed = parse_imports(
        "src/module.py",
        """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interfaces import RuntimeProtocol

import os
""",
    )

    type_import = _import_by_module(parsed, "interfaces")
    runtime_import = _import_by_module(parsed, "os")

    assert type_import.is_type_only is True
    assert type_import.names == ["RuntimeProtocol"]
    assert runtime_import.is_type_only is False


def test_python_typing_type_checking_attribute_is_marked_type_only():
    parsed = parse_imports(
        "src/module.py",
        """
import typing

if typing.TYPE_CHECKING:
    import torch

from pathlib import Path
""",
    )

    type_import = _import_by_module(parsed, "torch")
    runtime_import = _import_by_module(parsed, "pathlib")

    assert type_import.is_type_only is True
    assert type_import.names == ["torch"]
    assert runtime_import.is_type_only is False
