"""Export the "?" help texts to markdown so they can be reviewed without hovering.

Usage::

    python packaging/dump_help.py [output.md]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cp2kbuild.helptext import HELP  # noqa: E402

TITLES = {
    "structure": "结构", "kind_table": "基组表",
    "preview": "输入预览", "diagnostics": "校验结果怎么读",
    "task": "任务",
    "functional": "泛函", "dispersion": "色散", "cutoff": "CUTOFF",
    "rel_cutoff": "REL_CUTOFF", "basis_target": "基组偏好", "scf": "SCF 路线",
    "basis": "强制基组", "basis_file": "基组文件", "potential_file": "赝势文件",
    "charge": "电荷", "multiplicity": "多重度", "force_uks": "UKS",
    "vacuum": "真空层", "periodic": "周期性", "cell": "晶胞覆盖",
    "project": "项目名", "print_level": "打印级别", "walltime": "WALLTIME",
    "out": "输出文件",
    "print_forces": "打印受力", "print_mulliken": "Mulliken", "print_dipole": "偶极矩",
    "geo_optimizer": "优化器（几何）", "geo_max_iter": "最大步数（几何）",
    "cell_optimizer": "优化器（晶胞）", "cell_max_iter": "最大步数（晶胞）",
    "cell_pressure": "外压", "keep_angles": "固定角度",
    "ensemble": "系综", "thermostat": "恒温器", "temperature": "温度",
    "timestep": "时间步长", "steps": "步数", "md_pressure": "压力", "seed": "随机种子",
    "intensities": "红外强度", "thermochemistry": "热化学",
    "lattice": "晶格类型", "kpath": "k 路径", "npoints": "路径点数", "kgrid": "SCF k 网格",
    "pdos_nlumo": "NLUMO", "pdos_ldos": "LDOS",
    "band_type": "NEB 类型", "k_spring": "弹簧常数", "replicas": "镜像文件",
    "validate_files": "待校验文件", "validate_strict": "严格模式",
    "keyword_search": "搜索", "keyword_section": "限定 section",
    "data_dir": "数据目录", "data_elements": "元素", "data_target": "基组偏好",
}


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "packaging" / "help.md"
    lines = [
        "# cp2kbuild 界面里的「?」帮助文本",
        "",
        "鼠标悬浮在界面上任意「?」上就会显示下面这些内容；"
        "带手册链接的「?」还可以点击直接打开对应的手册页。",
        "",
        f"共 {len(HELP)} 条。",
        "",
    ]
    for key, entry in HELP.items():
        title = TITLES.get(key, key)
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"*标识*：`{key}`")
        lines.append("")
        for raw in entry.text.splitlines():
            lines.append(raw)
        lines.append("")
        if entry.url:
            lines.append(f"手册：<{entry.url}>")
            lines.append("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(HELP)} entries, "
          f"{sum(1 for e in HELP.values() if e.url)} with manual links)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
