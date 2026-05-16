"""state 层使用 schema.jsonpath 的桥接模块（避免循环依赖）。

state/ 不能直接 import schema/（schema 依赖 domain，state 是同级）；
通过此模块重导出 jsonpath 函数，保持分层清晰。
"""

from ..schema.jsonpath import get_value, set_value, parse_path

__all__ = ["get_value", "set_value", "parse_path"]
