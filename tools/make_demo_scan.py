"""Regenerate ``examples/convergence_demo``.

The inputs are produced by ``cp2kbuild scan`` itself (so they are a real example
of what the tool writes).  The ``.out`` files are **not** real CP2K results: they
are written here, in the exact format CP2K uses, so that ``converge`` and the
analysis tests have something deterministic to read without a CP2K binary.

The format follows real output:

* ``QS| Density cutoff [a.u.]:`` / ``QS| Relative density cutoff [a.u.]:``
* ``count for grid N: <points> cutoff [a.u.] <cutoff>``
* ``ENERGY| Total FORCE_EVAL ( QS ) energy [a.u.]``   (``force_env_methods.F``)

Usage::

    python tools/make_demo_scan.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cp2kbuild.paths import write_text_lf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "convergence_demo"
STAGE = ROOT / "examples" / "_demo_stage"

CUTOFFS = (300, 400, 500, 600, 700)
REL_CUTOFF = 60
REFERENCE = -17.159629584718102
#: energy offset per cutoff, and the number of points on each grid level
TABLE = {
    300: (0.0200, (0, 412, 3180, 26050)),
    400: (0.00080, (18, 1180, 6420, 21540)),
    500: (0.000050, (152, 2420, 8100, 18200)),
    600: (0.0000040, (430, 3860, 9200, 16100)),
    700: (0.0, (760, 5240, 10120, 14880)),
}


def energy_line(value: float) -> str:
    label = "ENERGY| Total FORCE_EVAL ( QS ) energy [a.u.]"
    # FMT = (/,T2,A,T55,F26.15)
    return " " + label.ljust(52) + f"{value:26.15f}"


def synthetic_output(cutoff: int) -> str:
    offset, counts = TABLE[cutoff]
    lines = [
        " CP2K| version string:                                     CP2K",
        "  ****  ****  ****  ****  ****  ****  ****  ****  ****  ****  ***",
        " DBCSR| CPU Multiplication driver  :BLAS",
        " QS| Method:                                                                 GPW",
        " QS| Number of grid levels:                                                    4",
        f" QS| Density cutoff [a.u.]:                                          {cutoff / 2:11.1f}",
        f" QS| Relative density cutoff [a.u.]:                                 {REL_CUTOFF / 2:11.1f}",
    ]
    for level in range(1, 5):
        lines.append(f" QS| Multi grid cutoff [a.u.]: {level}) grid level"
                     f"                    {cutoff / 2 / 3 ** (level - 1):13.1f}")
    lines.append("")
    for level, count in enumerate(counts, start=1):
        lines.append(f" count for grid {level:8d}:  {count:11d}          "
                     f"cutoff [a.u.]  {cutoff / 2 / 3 ** (level - 1):13.2f}")
    lines += [
        "",
        "  Total energy:                                              "
        f"{REFERENCE + offset:26.14f}",
        "",
        energy_line(REFERENCE + offset),
        "  ****  ****  ****  ****  ****  ****  ****  ****  ****  ****  ***",
        " PROGRAM ENDED AT 2026-09-13 22:00:00",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    for path in (DEMO, STAGE):
        if path.exists():
            shutil.rmtree(path)

    scan = subprocess.run(
        [sys.executable, "-m", "cp2kbuild", "scan",
         "--structure", str(ROOT / "examples" / "silicon.xyz"),
         "--cutoffs", ",".join(str(value) for value in CUTOFFS),
         "--rel-cutoffs", str(REL_CUTOFF),
         "--property", "energy",
         "--run-script",
         "--outdir", str(STAGE)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if scan.returncode != 0:
        print(scan.stdout)
        print(scan.stderr, file=sys.stderr)
        return scan.returncode

    DEMO.mkdir(parents=True)
    for item in sorted(STAGE.iterdir()):
        shutil.copy2(item, DEMO / item.name)
    shutil.rmtree(STAGE)

    for cutoff in CUTOFFS:
        inp = next(DEMO.glob(f"*cutoff_{cutoff}-*.inp"))
        write_text_lf(DEMO / f"{inp.stem}.out", synthetic_output(cutoff))

    write_text_lf(DEMO / "README.md",
        "# 合成的 CUTOFF 收敛演示\n"
        "\n"
        "`.inp` 是 `cp2kbuild scan` **真实生成**的（可以直接当模板用），\n"
        "`.inp`/`run.sh`/`run.ps1` 都是工具的原样输出。\n"
        "\n"
        "`.out` **不是真实计算结果**：它们由 `tools/make_demo_scan.py` 按 CP2K 的\n"
        "输出格式手写（列位置照抄源码 `force_env_methods.F` 的 `FMT`，网格那几行\n"
        "照抄真实输出），能量随 cutoff 变大而收敛，纯粹用来演示分析命令：\n"
        "\n"
        "```bat\n"
        "python -m cp2kbuild converge examples\\convergence_demo\n"
        "python -m cp2kbuild converge examples\\convergence_demo --ssv examples\\convergence_demo\n"
        "```\n"
        "\n"
        "真实用法是：先用 `cp2kbuild scan` 生成输入，在集群上跑完，\n"
        "再对存放输出的目录执行 `cp2kbuild converge <目录>`。\n")

    for path in sorted(DEMO.iterdir()):
        print("  ", path.name)
    print()
    subprocess.run([sys.executable, "-m", "cp2kbuild", "converge", str(DEMO)],
                   cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
