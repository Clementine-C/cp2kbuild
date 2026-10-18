# ARCHIVED: this module is no longer part of cp2kbuild.
# It implemented the old scan/converge machinery (generating
# arbitrary sweeps and reading CP2K outputs back).  The tool now only
# writes the official CUTOFF/REL_CUTOFF script set - see
# cp2kbuild/cutoffkit.py - and the analysis happens on the cluster.
# Kept for reference; it is not imported, tested or bundled.
"""The two shell scripts that go next to a scan: the runner and the analysis.

They have to work on a cluster where nothing but ``sh`` and ``awk`` exists - no
Python, no cp2kbuild - so they are self contained, and the extraction rules are
the ones the Python side uses (``cp2kbuild.analysis``): every parameter is read
from the outputs themselves, never guessed from a directory or file name.  A
directory built by an older hand-written script therefore reads the same way.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# the analysis script
# --------------------------------------------------------------------------- #
#: walks the outputs once and prints ``x energy natoms grid1..gridN`` per point,
#: sorted by x.  Kept in one program so the shell half stays readable.
AWK_EXTRACT = r"""
function num(text,   parts, count) {
    # every value this needs is the last field of the line
    count = split(text, parts, /[ \t]+/)
    return parts[count] + 0
}
function flush(   x, other, key, i, n) {
    if (energy == "" || cutoff_au == "" || rel_au == "") return
    x = (KEY == "cutoff") ? 2 * cutoff_au : 2 * rel_au
    other = (KEY == "cutoff") ? 2 * rel_au : 2 * cutoff_au
    if (FIXED_VALUE != "" && other + 0 != FIXED_VALUE + 0) return
    key = sprintf("%.6f", x)
    if (!(key in seen)) { seen[key] = 1; order[++rows] = key }
    value[key] = x
    total[key] = energy
    n = 0
    for (i in kinds) n += kinds[i]
    natoms[key] = (n > 0) ? n : 1
    if (nlevels > maxlevel) maxlevel = nlevels
    for (i = 1; i <= nlevels; i++) grid[key, i] = counts[i] + 0
}
function reset(   i) {
    energy = ""; cutoff_au = ""; rel_au = ""; nlevels = 0
    for (i in counts) delete counts[i]
    for (i in kinds) delete kinds[i]
}
FNR == 1 { if (file != "") flush(); reset(); file = FILENAME }
/Relative density cutoff \[a\.u\.\]/ { rel_au = num($0); next }
/Density cutoff \[a\.u\.\]/ { cutoff_au = num($0); next }
/Number of grid levels/ { nlevels = num($0); next }
/^[ \t]*count for grid/ { counts[$4 + 0] = $5; if ($4 + 0 > nlevels) nlevels = $4 + 0; next }
/ENERGY\| Total FORCE_EVAL/ { energy = num($0); next }
/^[ \t]*Total energy:/ { if (energy == "") energy = num($0); next }
/Atomic kind:/ {
    kind = $0; sub(/.*Atomic kind:[ \t]*/, "", kind); sub(/[ \t].*/, "", kind)
    atoms = $0; sub(/.*Number of atoms:[ \t]*/, "", atoms)
    if (!(kind in kinds)) kinds[kind] = atoms + 0
    next
}
END {
    flush()
    for (i = 1; i <= rows; i++)
        for (j = i + 1; j <= rows; j++)
            if (value[order[i]] + 0 > value[order[j]] + 0) {
                swap = order[i]; order[i] = order[j]; order[j] = swap
            }
    for (i = 1; i <= rows; i++) {
        key = order[i]
        printf "%.6f %.15f %d", value[key], total[key], natoms[key]
        for (j = 1; j <= maxlevel; j++) printf " %d", grid[key, j] + 0
        printf "\n"
    }
}
"""

#: turns the extracted rows into the human table: the change against the finest
#: point, per atom.  Numbers only - deciding which value to use is not this
#: script's job.
AWK_TABLE = r"""
BEGIN { n = 0 }
{ x[++n] = $1; e[n] = $2; a[n] = $3 }
END {
    if (n == 0) { print "  （没有可用的数据）"; exit }
    ref = e[n]
    printf "  %-10s %22s %13s %13s\n", "value", "E (Ha)", "dE (meV)", "dE/atom"
    for (i = 1; i <= n; i++) {
        atoms = (a[i] > 0) ? a[i] : 1
        d = (e[i] - ref) * 27211.386245988
        printf "  %-10.2f %22.12f %13.3f %13.4f\n", x[i], e[i], d, d / atoms
    }
    printf "\n"
    printf "  参考点是最细的那次（%g Ry，最后一行）。dE/atom 一列就是全部依据：\n", x[n]
    printf "  取哪一档由你按接下来算什么决定——能量够用不代表力、应力、声子够用，\n"
    printf "  而且参考点自己是否已经接近极限，这张表判断不了。\n"
}
"""

ANALYSE_SCRIPT = r'''#!/bin/sh
# @@TITLE@@
#
# 用法：  sh @@NAME@@ [扫描目录]
#   默认读当前目录；子目录会递归找，所以「一档一个目录」的布局和
#   「所有 .out 堆在一起」的平铺布局都能用。
#
# 每个点的 CUTOFF / REL_CUTOFF / 能量 / 每档网格点数都是从**输出文件里**读的
# （QS| Density cutoff [a.u.]、QS| Relative density cutoff [a.u.]、
# ENERGY| Total FORCE_EVAL [a.u.]、count for grid N），不是从文件名猜的。
# 只看 @@FIXED_LABEL@@ = @@FIXED_VALUE@@ 的那一批。
#
# 输出：@@SSV@@（gnuplot 直接用），并把表打到屏幕上。
set -u

DIR="${1:-.}"
KEY=@@KEY@@
LABEL="@@LABEL@@"
FIXED_KEY="@@FIXED_LABEL@@"          # 表头里显示的名字
FIXED_VALUE="@@FIXED_VALUE@@"
SSV="@@SSV@@"

list=$(mktemp); rows=$(mktemp)
trap 'rm -f "$list" "$rows"' EXIT

find "$DIR" -name '*.out' -type f 2>/dev/null | LC_ALL=C sort > "$list"
if [ ! -s "$list" ]; then
    echo "没有找到 .out 文件：$DIR" >&2
    exit 1
fi

# shellcheck disable=SC2046
awk -v KEY="$KEY" -v FIXED_VALUE="$FIXED_VALUE" '@@AWK_EXTRACT@@' \
    $(cat "$list") > "$rows"

if [ ! -s "$rows" ]; then
    echo "读了 $(wc -l < "$list" | tr -d ' ') 个 .out，但一个都没用上。" >&2
    echo "要么里面没有能量行，要么这一批的 @@FIXED_LABEL@@ 不是 $FIXED_VALUE。" >&2
    exit 1
fi

levels=$(awk 'NR == 1 { print NF - 3 }' "$rows")

{
    echo "# $LABEL vs total energy"
    echo "# generated by cp2kbuild, $(date '+%Y-%m-%d %H:%M:%S')"
    echo "# $FIXED_KEY = $FIXED_VALUE"
    printf '# %s (Ry) | Total Energy (Ha)' "$LABEL"
    i=1
    while [ "$i" -le "$levels" ]; do
        printf ' | NG on grid %s' "$i"
        i=$((i + 1))
    done
    printf '\n'
    awk '{ printf "%10.2f  %15.10f", $1, $2; for (i = 4; i <= NF; i++) printf "  %6d", $i; printf "\n" }' "$rows"
} > "$SSV"

echo "== $LABEL 扫描（$FIXED_KEY = $FIXED_VALUE）=="
cat "$SSV"
echo
awk '@@AWK_TABLE@@' "$rows"
echo "写了 $SSV"
'''


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def analyse_script(keyword: str, fixed_value, crit: float,
                   title: str, name: str) -> str:
    """One self-contained analysis script for a single-parameter sweep."""
    key = "cutoff" if keyword.upper() == "CUTOFF" else "rel_cutoff"
    label = keyword.upper()
    fixed_label = "REL_CUTOFF" if key == "cutoff" else "CUTOFF"
    replacements = {
        "@@TITLE@@": title,
        "@@NAME@@": name,
        "@@KEY@@": key,
        "@@LABEL@@": label,
        "@@FIXED_KEY@@": fixed_label,
        "@@FIXED_LABEL@@": fixed_label,
        "@@FIXED_VALUE@@": "" if fixed_value is None else _fmt(fixed_value),
        "@@SSV@@": "rel_cutoff_data.ssv" if key == "rel_cutoff" else "cutoff_data.ssv",
        "@@AWK_EXTRACT@@": AWK_EXTRACT.strip("\n"),
        "@@AWK_TABLE@@": AWK_TABLE.strip("\n"),
    }
    text = ANALYSE_SCRIPT
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #
RUN_SCRIPT = r'''#!/bin/sh
# {title}
#
# 用法：  sh {name} [序号 ...]
#   不带参数就跑全部参数点；也可以只跑其中几个（序号从 1 开始，按下面的清单）。
#
# 并行：每个算例用 NPROC_PER_CALC 个 MPI 进程，总共用 NPROC_TOTAL 个。
# 两个都是 1 → 纯串行，不需要 mpirun。比如 8 核机器上想同时跑 4 个算例：
#   NPROC_PER_CALC=2 NPROC_TOTAL=8 sh {name}
# 想挑几个点先试：
#   sh {name} 1 5 9
set -u

CP2K="${{CP2K:-cp2k.psmp}}"
NPROC_PER_CALC="${{NPROC_PER_CALC:-1}}"
NPROC_TOTAL="${{NPROC_TOTAL:-1}}"
OUTPUT="${{OUTPUT:-system.out}}"

# a relative CP2K path stops working as soon as we cd into a job directory
case "$CP2K" in
    /*) ;;
    */*) CP2K="$PWD/$CP2K" ;;
esac

if ! command -v "$CP2K" >/dev/null 2>&1 && [ ! -x "$CP2K" ]; then
    echo "找不到 CP2K 可执行文件：$CP2K" >&2
    echo "指定一个：CP2K=/path/to/cp2k.psmp sh {name}" >&2
    exit 1
fi

if [ "$NPROC_PER_CALC" -gt 1 ]; then
    if ! command -v mpirun >/dev/null 2>&1; then
        echo "NPROC_PER_CALC=$NPROC_PER_CALC 需要 mpirun，但找不到。" >&2
        exit 1
    fi
    CMD="mpirun -n $NPROC_PER_CALC $CP2K"
else
    CMD="$CP2K"
fi

# 每个任务的结果，退出码非 0 时一定要看得见——批量跑最怕静默失败
failed_file=$(mktemp)
trap 'rm -f "$failed_file"' EXIT
run_one() {{
    job="$1"
    if [ ! {check} ]; then
        echo "!! 跳过 $job（不存在）" >&2
        echo "$job" >> "$failed_file"
        return 1
    fi
    echo "== $job  ($(date '+%H:%M:%S'))"
    if {run}; then
        return 0
    fi
    echo "!! $job 失败（非 0 退出码，见上面的报错）" >&2
    echo "$job" >> "$failed_file"
    return 1
}}

JOBS="{jobs}"

if [ "$#" -gt 0 ]; then
    picked=
    for index in "$@"; do
        found=$(printf '%s\n' $JOBS | awk -v n="$index" 'NR == n {{ print; exit }}')
        if [ -z "$found" ]; then
            echo "序号越界：$index" >&2
            exit 1
        fi
        picked="$picked $found"
    done
    JOBS=$picked
fi

total=0
for job in $JOBS; do total=$((total + 1)); done
SLOTS=$((NPROC_TOTAL / NPROC_PER_CALC))
[ "$SLOTS" -lt 1 ] && SLOTS=1

echo "共 $total 个参数点，最多 $SLOTS 个同时跑（每个 $NPROC_PER_CALC 个 MPI 进程）"
started=0
running=0
for job in $JOBS; do
    run_one "$job" &
    started=$((started + 1))
    running=$((running + 1))
    if [ "$running" -ge "$SLOTS" ]; then
        wait
        running=0
    fi
done
wait
bad=$(wc -l < "$failed_file" | tr -d ' ')
if [ "$bad" -gt 0 ]; then
    echo "结束：$started/$total 个参数点，其中 $bad 个失败：" >&2
    sed 's/^/  - /' "$failed_file" >&2
    exit 1
fi
echo "全部完成：$started/$total 个参数点"
'''

RUN_SCRIPT_PS1 = r'''# {title}
# 用法:  pwsh {name}
$cp2k = if ($env:CP2K) {{ $env:CP2K }} else {{ "cp2k.psmp" }}
$nproc = if ($env:NPROC_PER_CALC) {{ $env:NPROC_PER_CALC }} else {{ 1 }}
$watch = {{ param($p, $label) Write-Host "== $label"; $p.WaitForExit(); "  exit=$($p.ExitCode) ($label)" }}
'''

RUN_SCRIPT_PS1_BODY = r'''Write-Host "== {label}"
{invoke}
'''


def run_script(entries: list[str], layout: str, input_name: str,
               title: str, name: str) -> str:
    """The batch runner for one sweep.

    ``entries`` are the relative paths written into the job list: a directory per
    point (``cutoff_450Ry``) or the ``.inp`` itself in a flat scan.
    """
    if layout == "dirs":
        check = '-d "$job"'
        run = f'( cd "$job" && $CMD -i {input_name} -o "$OUTPUT" )'
    else:
        check = '-f "$job"'
        run = '$CMD -i "$job" -o "${job%.inp}.out"'
    jobs = " ".join(entries)
    return RUN_SCRIPT.format(title=title, name=name, check=check, run=run, jobs=jobs)


def run_script_ps1(entries: list[str], layout: str, input_name: str,
                   title: str, name: str) -> str:
    lines = [RUN_SCRIPT_PS1.format(title=title, name=name)]
    for entry in entries:
        if layout == "dirs":
            work = Path(entry).name
            lines.append(f'Write-Host "== {work}"')
            lines.append(f'Push-Location "{work}"')
            lines.append(f'& $cp2k -i {input_name} -o system.out')
            lines.append('Write-Host "  exit=$LASTEXITCODE"')
            lines.append('Pop-Location')
        else:
            stem = Path(entry).stem
            lines.append(f'Write-Host "== {stem}"')
            lines.append(f'& $cp2k -i {entry} -o {stem}.out')
            lines.append('Write-Host "  exit=$LASTEXITCODE"')
    return "\n".join(lines) + "\n"
