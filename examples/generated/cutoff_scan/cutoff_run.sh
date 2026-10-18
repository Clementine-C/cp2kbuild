#!/bin/sh
# 跑完 CUTOFF 扫描（2 个参数点）
#
# 用法：  sh cutoff_run.sh [序号 ...]
#   不带参数就跑全部参数点；也可以只跑其中几个（序号从 1 开始，按下面的清单）。
#
# 并行：每个算例用 NPROC_PER_CALC 个 MPI 进程，总共用 NPROC_TOTAL 个。
# 两个都是 1 → 纯串行，不需要 mpirun。比如 8 核机器上想同时跑 4 个算例：
#   NPROC_PER_CALC=2 NPROC_TOTAL=8 sh cutoff_run.sh
# 想挑几个点先试：
#   sh cutoff_run.sh 1 5 9
set -u

CP2K="${CP2K:-cp2k.psmp}"
NPROC_PER_CALC="${NPROC_PER_CALC:-1}"
NPROC_TOTAL="${NPROC_TOTAL:-1}"
OUTPUT="${OUTPUT:-system.out}"

# a relative CP2K path stops working as soon as we cd into a job directory
case "$CP2K" in
    /*) ;;
    */*) CP2K="$PWD/$CP2K" ;;
esac

if ! command -v "$CP2K" >/dev/null 2>&1 && [ ! -x "$CP2K" ]; then
    echo "找不到 CP2K 可执行文件：$CP2K" >&2
    echo "指定一个：CP2K=/path/to/cp2k.psmp sh cutoff_run.sh" >&2
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
run_one() {
    job="$1"
    if [ ! -f "$job" ]; then
        echo "!! 跳过 $job（不存在）" >&2
        echo "$job" >> "$failed_file"
        return 1
    fi
    echo "== $job  ($(date '+%H:%M:%S'))"
    if $CMD -i "$job" -o "${job%.inp}.out"; then
        return 0
    fi
    echo "!! $job 失败（非 0 退出码，见上面的报错）" >&2
    echo "$job" >> "$failed_file"
    return 1
}

JOBS="cp2k-cutoff_400-rel_cutoff_60.inp cp2k-cutoff_600-rel_cutoff_60.inp"

if [ "$#" -gt 0 ]; then
    picked=
    for index in "$@"; do
        found=$(printf '%s\n' $JOBS | awk -v n="$index" 'NR == n { print; exit }')
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
