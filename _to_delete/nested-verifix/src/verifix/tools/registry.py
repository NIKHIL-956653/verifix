"""Which tools each task kind gets."""

from verifix.tools.base import ToolRegistry
from verifix.tools.edit_tools import EDIT_TOOLS
from verifix.tools.read_tools import READ_TOOLS, SHOW_CHANGES
from verifix.tools.report_tools import FINDING_TOOL, FINISH_TOOL, PLAN_TOOL
from verifix.tools.test_tools import TEST_TOOLS


def tools_for(kind: str) -> ToolRegistry:
    if kind == "ask":
        return ToolRegistry([*READ_TOOLS, FINISH_TOOL])
    if kind == "investigate":
        return ToolRegistry(
            [*READ_TOOLS, PLAN_TOOL, *EDIT_TOOLS, SHOW_CHANGES, *TEST_TOOLS, FINISH_TOOL]
        )
    if kind == "fix":
        return ToolRegistry(
            [*READ_TOOLS, PLAN_TOOL, *EDIT_TOOLS, SHOW_CHANGES, *TEST_TOOLS, FINISH_TOOL]
        )
    if kind == "review":
        return ToolRegistry([*READ_TOOLS, *TEST_TOOLS, FINDING_TOOL, FINISH_TOOL])
    raise ValueError(f"unknown task kind {kind!r}")


def needs_workspace(kind: str) -> bool:
    return kind in ("investigate", "fix", "review")
