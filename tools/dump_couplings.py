"""Render the coupling table to markdown (``packaging/couplings.md``).

The table is the single source of truth for "which parameter only applies
under which condition": the GUI greys those controls out, the command line
warns about them and ``tools/selftest.py`` walks every entry.  This dump keeps
the documentation in step with the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cp2kbuild.couplings import RULES  # noqa: E402
from cp2kbuild.recipes import Spec  # noqa: E402


def main() -> int:
    lines = [
        "# 参数耦合表（反应式启用）",
        "",
        "`cp2kbuild/couplings.py` 里的 `RULES` 是唯一来源：界面按它灰掉用不上的控件"
        "（原因写在该行的「?」上）、命令行按它对「给了却不会生效」的参数发警告、"
        "`tools/selftest.py` 逐条验证（每条都要能找到一个让它失效的输入、给出对应标志时"
        "会警告、默认值不误报）。",
        "",
        f"共 {len(RULES)} 条（其中 {sum(1 for r in RULES if r.gui_only)} 条是"
        "**界面/库独有**：命令行已冻结，没有对应开关，表里写「—」）。"
        "每一行都附了依据（手册页面或 CP2K 源码文件）。",
        "",
        "| 参数（Spec 字段） | 命令行 | 只在……时生效 | 依据 |",
        "|---|---|---|---|",
    ]
    for rule in RULES:
        print_note = rule.why if isinstance(rule.why, str) else "（随任务/取值变化，见实现）"
        command = "—（界面独有）" if rule.gui_only else f"`{rule.flag()}`"
        lines.append(
            f"| `{rule.field}` | {command} | {print_note} | `{rule.evidence}` |")
    lines += [
        "",
        "## 说明",
        "",
        "- 「用不上」不等于「写错了」：这些参数在别的上下文里很有用，只是当前这套输入"
        "不会把它们写进去（或者写了也被 CP2K 忽略）。",
        "- 界面上每个被灰掉的控件，它那一行的「?」里会写出**当前**为什么用不上，"
        "以及依据在哪；换任务 / 换路线 / 换周期性时会实时重算。",
        "- 命令行只对**你确实要求过**的参数发警告（`--temperature 500` 配单点会警告，"
        "`--no-intensities` 不会——明确关掉的东西不需要提醒）。",
        "",
    ]
    target = ROOT / "packaging" / "couplings.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {target} ({len(RULES)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
