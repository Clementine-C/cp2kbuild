"""A staged temperature program for MD: hold / heat / cool, in order.

CP2K has **no** time-dependent temperature input.  ``&MOTION/&MD`` knows a single
``TEMPERATURE`` plus the per-step annealing factors (``TEMPERATURE_ANNEALING`` for
the Langevin target, ``ANNEALING``/``ANNEALING_CELL`` for velocity rescaling), so
one run can only do *one* geometric ramp - it cannot hold, cannot change slope and
cannot turn around.  A program such as::

    hold  300 K for 5 ps
    ramp  300 -> 500 K over 10 ps
    hold  500 K for 5 ps
    ramp  500 -> 600 K over 8 ps
    hold  600 K for 10 ps
    ramp  600 -> 300 K over 20 ps

is therefore a **chain of runs**: one input per segment, each continuing the
previous one through ``&EXT_RESTART`` (positions, velocities, counters and - for
the thermostatted ensembles - the thermostat/barostat state).  That is exactly how
CP2K's own ``wat125_Tanneal.inp`` + ``wat125_Tanneal_restart.inp`` pair does it.

This module turns the program text into those stages, writes the inputs plus a
serial driver script, and reports what the schedule will do (total time, K/ps, the
planned temperature curve).  Everything it writes is plain CP2K input and POSIX
``sh``, so it can be read, edited and re-run by hand on the cluster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import __version__
from .model import render_document
from .paths import write_text_lf
from .recipes import (NPT_ENSEMBLES, THERMOSTATTED_ENSEMBLES,
                      build as build_inputs, plan_ramp, writes_kpoints)

#: Units a duration may carry.  ``ps`` is the default because that is what an MD
#: schedule is usually quoted in; ``steps`` is there for people who think in MD
#: steps (and is exact, while a duration has to be rounded to whole steps).
_DURATION_UNITS = {
    "ps": 1000.0, "皮秒": 1000.0,
    "fs": 1.0, "飞秒": 1.0,
    "ns": 1_000_000.0, "纳秒": 1_000_000.0,
}

_HOLD_WORDS = ("hold", "keep", "const", "保持", "恒定", "保温")
_RAMP_WORDS = ("ramp", "heat", "cool", "linear", "升温", "降温", "线性", "变化")

RE_NUMBER = re.compile(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class Segment:
    """One line of the program: hold at a temperature, or ramp between two."""

    start: float
    end: float
    duration_ps: float

    @property
    def kind(self) -> str:
        return "hold" if abs(self.end - self.start) < 1e-9 else "ramp"


@dataclass(frozen=True)
class Stage:
    """One CP2K run: a segment rounded to whole MD steps."""

    index: int                       # 1-based
    segment: Segment
    steps: int
    mechanism: str                   # "hold" | "temperature" | "velocity"
    factor: float | None             # None for a hold
    name: str                        # PROJECT_NAME and file stem

    @property
    def start(self) -> float:
        return self.segment.start

    @property
    def end(self) -> float:
        return self.segment.end

    @property
    def kind(self) -> str:
        return self.segment.kind

    @property
    def keyword(self) -> str | None:
        return {"temperature": "TEMPERATURE_ANNEALING",
                "velocity": "ANNEALING"}.get(self.mechanism)


@dataclass
class ProgramPlan:
    """What was written, plus the lines the CLI prints and the GUI shows."""

    outdir: Path
    stages: list[Stage] = field(default_factory=list)
    timestep: float = 0.5
    written: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_ps(self) -> float:
        return sum(stage.steps * self.timestep / 1000.0 for stage in self.stages)

    def table(self) -> list[list[str]]:
        """The planned schedule, as ``program.ssv`` rows."""
        rows = [["# stage", "kind", "T_start_K", "T_end_K", "duration_ps", "steps",
                 "keyword", "factor"]]
        for stage in self.stages:
            rows.append([
                stage.name, stage.kind, f"{stage.start:g}", f"{stage.end:g}",
                f"{stage.steps * self.timestep / 1000.0:.3f}", str(stage.steps),
                stage.keyword or "-",
                "-" if stage.factor is None else f"{stage.factor:.12f}",
            ])
        return rows

    def text(self) -> str:
        lines = [f"wrote {len(self.written)} file(s) to {self.outdir}"]
        lines += [f"  {path.name}" for path in self.written]
        lines.append("")
        lines.append(f"计划：{len(self.stages)} 段，共 {self.total_ps:.2f} ps"
                     f"（timestep {self.timestep:g} fs）")
        for stage in self.stages:
            what = ("保持" if stage.kind == "hold" else
                    ("升温" if stage.end > stage.start else "降温"))
            rate = ""
            duration = stage.steps * self.timestep / 1000.0
            if stage.kind == "ramp" and duration:
                rate = f"，{abs(stage.end - stage.start) / duration:.1f} K/ps"
            lines.append(
                f"  {stage.name:28s} {what} {stage.start:.0f}→{stage.end:.0f} K  "
                f"{stage.steps} 步 = {duration:.2f} ps{rate}"
                + ("" if stage.keyword is None
                   else f"  [{stage.keyword} {stage.factor:.8f}]"))
        lines.append("")
        lines.append("拷到服务器上（整个目录），然后：")
        lines.append("  sh run_program.sh      # 串行：后一段必须读前一段的 restart")
        for note in self.notes:
            lines.append("")
            lines.append("note: " + note)
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# the program text
# --------------------------------------------------------------------------- #
def _duration(token: str, timestep: float) -> float:
    """``"5ps"`` / ``"5000 steps"`` / ``"10"`` -> a duration in ps."""
    text = token.strip().lower().replace("步", "steps")
    match = re.match(r"^([-+]?[0-9.eE+]+)\s*([a-zµ]*)$", text)
    if not match:
        raise ValueError(f"看不懂的时长 {token!r}；用 `5ps`、`5000steps` 或者 `10`（=10 ps）")
    value = float(match.group(1))
    unit = match.group(2) or "ps"
    if unit in _DURATION_UNITS:
        return value * _DURATION_UNITS[unit] / 1000.0
    if unit in ("step", "steps"):
        return value * timestep / 1000.0
    raise ValueError(f"看不懂的时长单位 {unit!r}（{token!r}）；"
                     "可用 ps / fs / ns / steps")


def parse_program(text: str, *, timestep: float = 0.5) -> list[Segment]:
    """Read the program text into segments.

    One segment per line; ``#`` starts a comment.  Each line is either

    ``hold <T> <duration>``      - keep this temperature
    ``ramp <T1> <T2> <duration>`` - go from T1 to T2

    (``ramp`` may also be written ``heat``/``cool``/``升温``/``降温``, and a
    two-number line is a hold.)  The duration defaults to ps and may carry a unit
    (``5ps``, ``50000steps``); ``steps`` is exact, a duration is rounded to whole
    MD steps later.
    """
    segments: list[Segment] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        words = [word for word in re.split(r"[\s,;]+", line) if word]
        kind = None
        if words and words[0].lower() in _HOLD_WORDS + _RAMP_WORDS:
            kind = "hold" if words[0].lower() in _HOLD_WORDS else "ramp"
            words = words[1:]
        numbers = []
        for word in words:
            match = RE_NUMBER.match(word)
            if match and match.start() == 0:
                numbers.append(word[:match.end()] + word[match.end():])
            else:
                raise ValueError(f"第 {number} 行看不懂：{raw.strip()!r}")
        if kind is None:
            kind = "hold" if len(numbers) == 2 else "ramp"
        if kind == "hold" and len(numbers) == 2:
            start = end = float(numbers[0])
            duration = _duration(numbers[1], timestep)
        elif kind == "ramp" and len(numbers) == 3:
            start, end = float(numbers[0]), float(numbers[1])
            duration = _duration(numbers[2], timestep)
        elif kind == "hold" and len(numbers) == 3:
            # "hold 300 300 5ps" - a hold written with both temperatures
            start, end = float(numbers[0]), float(numbers[1])
            duration = _duration(numbers[2], timestep)
            if abs(start - end) > 1e-9:
                raise ValueError(f"第 {number} 行说 hold 却给了两个温度 {start:g}/{end:g}；"
                                 "要保持就用一个温度，要变化就用 ramp")
        else:
            raise ValueError(
                f"第 {number} 行参数个数不对：{raw.strip()!r}；"
                "写成 `hold 300 5ps` 或 `ramp 300 500 10ps`")
        if start <= 0 or end <= 0:
            raise ValueError(f"第 {number} 行温度必须为正（开氏）：{start:g}/{end:g}")
        if duration <= 0:
            raise ValueError(f"第 {number} 行时长必须为正：{duration:g} ps")
        segments.append(Segment(start=start, end=end, duration_ps=duration))
    if not segments:
        raise ValueError("温度程序是空的：每行写一段，例如 `hold 300 5ps`、`ramp 300 500 10ps`")
    return segments


def parse_compact(text: str, *, timestep: float = 0.5) -> list[Segment]:
    """``"300:5,300-500:10,500:5"`` -> segments (the one-liner CLI form).

    ``T:dur`` is a hold, ``T1-T2:dur`` a ramp.  It is only sugar: the result is
    the same list :func:`parse_program` builds.
    """
    lines = []
    for piece in re.split(r"[,;]+", text):
        piece = piece.strip()
        if not piece:
            continue
        if ":" not in piece:
            raise ValueError(f"{piece!r} 缺少时长：写成 `300:5`（保持 5 ps）"
                             "或 `300-500:10`（10 ps 内升到 500 K）")
        span, duration = piece.rsplit(":", 1)
        if "-" in span.strip().lstrip("-"):
            start, end = span.split("-", 1)
            lines.append(f"ramp {start.strip()} {end.strip()} {duration.strip()}")
        else:
            lines.append(f"hold {span.strip()} {duration.strip()}")
    return parse_program("\n".join(lines), timestep=timestep)


# --------------------------------------------------------------------------- #
# the stages
# --------------------------------------------------------------------------- #
def stage_name(index: int, segment: Segment) -> str:
    """``stage03_ramp_500to600K`` - also the CP2K ``PROJECT_NAME``.

    Letters, digits and underscores only: the project name ends up in the restart,
    `.ener` and trajectory file names, and the driver script copies those.
    """
    if segment.kind == "hold":
        what = f"hold_{segment.start:.0f}K"
    else:
        what = (f"heat_{segment.start:.0f}to{segment.end:.0f}K"
                if segment.end > segment.start else
                f"cool_{segment.start:.0f}to{segment.end:.0f}K")
    return f"stage{index:02d}_{what}"


def plan_program(segments, *, timestep: float, ensemble: str) -> list[Stage]:
    """Turn segments into stages: whole MD steps, and the right CP2K keyword."""
    if timestep <= 0:
        raise ValueError("timestep 必须为正")
    stages: list[Stage] = []
    for index, segment in enumerate(segments, start=1):
        steps = max(1, int(round(segment.duration_ps * 1000.0 / timestep)))
        if segment.kind == "hold":
            mechanism, factor = "hold", None
        else:
            ramp = plan_ramp(segment.start, segment.end, steps, timestep, ensemble)
            mechanism, factor = ramp.mechanism, ramp.factor
        stages.append(Stage(index=index, segment=segment, steps=steps,
                            mechanism=mechanism, factor=factor,
                            name=stage_name(index, segment)))
    return stages


def restart_takes(ensemble: str) -> tuple[str, ...]:
    """What every stage after the first has to take from the previous one.

    Positions matter even though every input carries ``&COORD``: they are the
    *initial* coordinates of the whole program, so without ``RESTART_POS`` each
    stage would jump back to them.  ``COUNTERS`` keeps the step numbering
    continuous, which makes the per-stage ``-1.ener`` files line up into one
    temperature curve.  The thermostat/barostat state is what keeps the control
    loop smooth across a stage boundary; Langevin has no such state (its friction
    and noise are memoryless).
    """
    name = (ensemble or "").upper()
    take = ["pos", "vel", "counters"]
    if name in THERMOSTATTED_ENSEMBLES:
        take.append("thermostat")
    if name in NPT_ENSEMBLES:
        take += ["cell", "barostat", "barostat_thermostat"]
    return tuple(take)

def program_notes(stages: list[Stage], *, timestep: float, ensemble: str) -> list[str]:
    """What the user has to know about this schedule before running it."""
    notes: list[str] = []
    name = (ensemble or "").upper()
    for before, after in zip(stages, stages[1:]):
        if abs(after.start - before.end) > 1e-6:
            notes.append(
                f"{after.name} 从 {after.start:g} K 开始，而上一段结束在 "
                f"{before.end:g} K：这是一次温度跳变（恒温器会很快把体系拉过去）。"
                "想要连续就把这一段写成 ramp。")
    holds = [stage for stage in stages if stage.kind == "hold"]
    if holds and name not in THERMOSTATTED_ENSEMBLES and name != "LANGEVIN":
        notes.append(
            f"{len(holds)} 个「保持」段在 ENSEMBLE {name} 下不会真的保持温度："
            "NVE/NPE 没有恒温器，温度只会自己漂。要保温请用 LANGEVIN（或 NVT/NPT）。")
    for stage in stages:
        if stage.kind != "ramp":
            continue
        duration = stage.steps * timestep / 1000.0
        per_step = abs(stage.factor - 1.0) * stage.start
        if per_step > 1.0:
            notes.append(
                f"{stage.name} 每步移动约 {per_step:.1f} K"
                f"（{stage.start:g}→{stage.end:g} K 用 {duration:.2f} ps）："
                "比多数 AIMD 升降温快一个量级，恒温器跟不上。")
    if stages and stages[0].mechanism == "temperature":
        notes.append(
            "每一段都写了自己的起始温度，所以**不需要** "
            "`RESTART_TEMPERATURE_ANNEALING`：CP2K 默认(不写)就用输入里的 "
            "TEMPERATURE（那正是这一段的起点）。")
    return notes


# --------------------------------------------------------------------------- #
# writing it all out
# --------------------------------------------------------------------------- #
@dataclass
class RunOptions:
    """How ``run_program.sh`` launches each stage (the machine's half).

    These are *not* CP2K input parameters - they only shape the driver script,
    which is why they are a separate object instead of fields on ``Spec``.  The
    defaults are this project's cluster: ``mpirun -np 64 cp2k.psmp`` with two
    OpenMP threads per rank.
    """

    mpirun_bin: str = "mpirun"
    cp2k_bin: str = "cp2k.psmp"
    mpi_procs: int = 64
    omp_threads: int = 2
    #: extra words for mpirun (``--bind-to none`` on OpenMPI; MPICH/Intel MPI
    #: refuse that option, so it is off by default)
    mpirun_extra: str = ""
    #: skip a stage whose ``<段名>-1.restart`` is newer than its input
    resume: bool = True


def program_script(stages: list[Stage], *, run: "RunOptions | None" = None,
                   program_file: str = "program.txt",
                   table_file: str = "program.ssv") -> str:
    """``run_program.sh``: run the stages **in order** and chain their restarts.

    Serial on purpose: stage N+1 reads what stage N wrote, so the like of
    ``cutoff_run.sh`` (several jobs at once) is impossible here.  The restart file
    CP2K writes is not always named the same way (``<project>-1.restart``, and a
    step-numbered variant when ``ADD_LAST``/walltime kick in), so after every
    stage the newest ``<project>*.restart`` is copied to the plain
    ``<project>-1.restart`` the next stage asks for.

    The per-stage launch is whatever :class:`RunOptions` says, by default this
    project's cluster shape::

        export OMP_NUM_THREADS=2
        mpirun -np 64 cp2k.psmp -i <input> -o <output>

    (the cluster's file names are ``cp2k.inp``/``cp2k.out``; a chain has one pair
    per stage, so they are named after the stage instead - ``-i`` never takes part
    in the chaining, the ``PROJECT_NAME`` does).  ``mpirun_bin=`` empty still
    means "one process, no mpirun".
    """
    run = run or RunOptions()
    lines = [
        "#!/bin/bash",
        "# 温度程序：分段**串行**运行——后一段要读前一段写出的 restart 和波函数，",
        "# 所以段与段之间不能并行（不像 cutoff_run.sh 那样几个算例同时跑）。",
        "# 每一段**内部**是并行的：段内用 mpirun -np $no_proc 起 MPI 进程。",
        "# 由 cp2kbuild 生成",
        f"# 计划表：{table_file}；原始程序：{program_file}",
        "#",
        "# 下面这五个变量在界面上（「温度程序」那一栏）也能改，这里是脚本里的值；",
        "# 默认是本项目集群的写法：mpirun -np 64 cp2k.psmp，每进程 2 个 OpenMP 线程。",
        "# 这个脚本用 bash 起头，但函数体只用 POSIX 语法，`sh run_program.sh` 也能跑。",
        "",
        "# 每个 MPI 进程的 OpenMP 线程数（export 只在本次运行里生效）",
        f"export OMP_NUM_THREADS={int(run.omp_threads)}",
        "",
        f"mpirun_bin={run.mpirun_bin}",
        f"cp2k_bin={run.cp2k_bin}",
        f"no_proc={int(run.mpi_procs)}",
        "# mpirun 的额外参数，默认空：官方 CUTOFF 脚本在“同时起多个作业”时用",
        "# --bind-to none 避免抢核，这里一次只有一个作业；而且它是 OpenMPI 专有选项",
        "# （MPICH/Intel MPI 不认），需要就自己加。",
        f'mpirun_extra="{run.mpirun_extra}"',
        "",
        "# 已经跑完的段默认跳过（墙钟被砍之后接着跑）：判据是脚本自己在段末写的",
        "# <段名>.done 标记，里面记着这一段输入的校验和——不用 CP2K 写出来的 restart",
        "# 名字（那个名字随 CP2K 的迭代层数变，跑一半被杀掉时也可能已经存在），",
        "# 也不用 .inp 的时间戳（重新生成同一份程序会让已完成的段白跑）。",
        "# 一旦有一段重跑，它后面的段都会跟着重跑（链条从那里断了）。",
        "# 想从头重跑：把 resume 设成 false，或者删掉那些 <段名>.done。",
        f"resume={'true' if run.resume else 'false'}",
        "rerun=0",
        "",
        "# 目录里的 .marker-scheme 说明这个目录已经用“新判据”（完成标记）跑过：",
        "# 没有它就是老版本生成的目录，这一次仍按老判据（交接文件比输入新）跳过，",
        "# 免得升级工具后已跑完的段白跑一遍；跑过一次之后一律只认完成标记。",
        "new_scheme=0",
        '[ -f .marker-scheme ] && new_scheme=1',
        ": > .marker-scheme",
        "",
        f'echo "温度程序：共 {len(stages)} 段，串行跑'
        '（每段都要读上一段写出的 restart 和波函数）"',
        "",
        "run_stage() {",
        '    stage="$1"',
        '    previous="$2"',
        '    if [ ! -f "$stage.inp" ] ; then',
        '        echo "缺少 $stage.inp" >&2',
        "        exit 1",
        "    fi",
        '    if [ -n "$previous" ] && [ ! -f "$previous-1.restart" ] ; then',
        '        echo "缺少 $previous-1.restart：上一段没跑完（或者没写 restart）" >&2',
        "        exit 1",
        "    fi",
        "    # 这一段的“指纹”：输入内容（不是时间）变了才重跑；重新生成同一份程序",
        "    # 不会让已完成的段白跑。没有 cksum 就用字节数兜底。",
        '    fingerprint=$(cksum "$stage.inp" 2>/dev/null | awk \'{print $1"-"$2}\')',
        '    [ -n "$fingerprint" ] || fingerprint=$(wc -c < "$stage.inp" | tr -d " ")',
        '    skip=""',
        '    if [ "$resume" = "true" ] && [ "$rerun" = "0" ] ; then',
        '        if [ -f "$stage.done" ] && [ "$(cat "$stage.done")" = "$fingerprint" ] ; then',
        '            skip="yes"',
        '        elif [ "$new_scheme" = "0" ] && [ -f "$stage-1.restart" ] \\',
        '                && [ "$stage-1.restart" -nt "$stage.inp" ] ; then',
        '            skip="legacy"',
        "        fi",
        "    fi",
        '    if [ -n "$skip" ] ; then',
        '        echo "== $stage 已经跑完，跳过（想重跑就删掉 $stage.done）"',
        "        return 0",
        "    fi",
        '    echo "== $stage 开始 $(date +%H:%M:%S)"',
        '    : > "$stage.started"     # 这次开跑的时刻：用来确认真的写了新 restart',
        '    if [ -z "$mpirun_bin" ] ; then',
        '        $cp2k_bin -i "$stage.inp" -o "$stage.out" || exit 1',
        "    else",
        "        # 一段一个 CP2K 作业：输入/输出就是这一段的 <段名>.inp / <段名>.out",
        '        $mpirun_bin -np $no_proc $mpirun_extra \\',
        '            $cp2k_bin -i "$stage.inp" -o "$stage.out" || exit 1',
        "    fi",
        "    rerun=1          # 这一段真的跑了：后面的段都得跟着重跑",
        "    # CP2K 写出来的 restart 名字可能带步数（<项目>-1_40000.restart）：",
        "    # 统一复制成 <项目>-1.restart，下一段就读这个名字。",
        '    newest=$(ls -t "$stage"*.restart 2>/dev/null | head -1)',
        '    if [ -z "$newest" ] ; then',
        '        echo "$stage 没有写出 restart 文件：检查 &MOTION/&PRINT/&RESTART" >&2',
        "        exit 1",
        "    fi",
        '    if [ ! "$newest" -nt "$stage.started" ] ; then',
        '        echo "$stage 没有写出**新的** restart（$newest 比这次开跑还旧）：" >&2',
        '        echo "  这一段不算跑完，后面不会继续——查日志/磁盘后重跑这一段" >&2',
        "        exit 1",
        "    fi",
        '    if [ "$newest" != "$stage-1.restart" ] ; then',
        '        cp -f "$newest" "$stage-1.restart" || exit 1',
        '        echo "   restart: $newest → $stage-1.restart"',
        "    fi",
        "    # 只有走到这里才算这一段跑完：写完成标记（内容 = 输入指纹）",
        "    printf '%s\\n' \"$fingerprint\" > \"$stage.done\"",
        '    echo "== $stage 结束 $(date +%H:%M:%S)"',
        "}",
        "",
    ]
    previous = ""
    for stage in stages:
        lines.append(f'run_stage {stage.name} "{previous}"')
        previous = stage.name
    lines += [
        "",
        'echo "全部跑完：各段的 <段名>.out / -1.ener / 轨迹都在本目录"',
        'echo "温度曲线：把各段 -1.ener 的 Temp[K] 列按 program.ssv 对齐拼起来"',
        "",
    ]
    return "\n".join(lines)


def build_program(spec, index, stages: list[Stage], outdir: str | Path, *,
                  program_text: str = "",
                  seed_restart: str | None = None,
                  run: RunOptions | None = None) -> ProgramPlan:
    """Write every stage input plus ``run_program.sh`` and ``program.ssv``.

    ``seed_restart`` lets the *first* stage continue an existing run (an
    equilibration, say) instead of drawing fresh velocities; every later stage
    always continues the one before it.
    """
    if getattr(spec, "task", "md") != "md":
        raise ValueError("温度程序是 MD 的东西：请把任务设成 md（AIMD 或经典 MD 都行）")
    target = Path(outdir)
    target.mkdir(parents=True, exist_ok=True)
    plan = ProgramPlan(outdir=target, stages=list(stages),
                       timestep=float(spec.timestep))
    plan.notes += program_notes(stages, timestep=float(spec.timestep),
                                ensemble=spec.ensemble)
    if seed_restart:
        plan.notes.append(
            f"第一段从 {seed_restart} 接手（接力文件是你在界面上/命令行里给的那个），"
            "后面的段各接各的前一段。")
    # The program owns the temperature curve.  The single-run heating/cooling
    # setting ("heat to 600 K over this one run") does not reach any stage: every
    # stage writes the factor its own segment needs, and a "hold" stage writes
    # none.  Saying so matters, because the two controls sit on the same page and
    # a left-over factor would otherwise look active.
    taken_over = [
        name for name, value in
        (("「升温 / 降温」里的 TEMPERATURE_ANNEALING", spec.temperature_annealing),
         ("「升温 / 降温」里的 ANNEALING", spec.annealing))
        if value is not None]
    if taken_over:
        plan.notes.append(
            "、".join(taken_over) + " 不会进这份程序：每一段的目标温度和因子都由"
            "程序自己算（那个单段设置只作用于「保存/预览」的那一份输入；"
            "要改程序里的某一段，改表格/程序文本本身）。")
    # ANNEALING_CELL has no column in the table, so it stays a *program-wide*
    # setting - and it applies to the hold stages too, which is worth spelling out
    if spec.annealing_cell is not None:
        plan.notes.append(
            f"晶胞速度退火 `&MD ANNEALING_CELL {spec.annealing_cell:g}` 不由温度程序表"
            "控制：它是**程序级**的，每一段（含「保持」段）都会带上它——它管的是"
            "盒子的涨落，不是体系温度。只有 NPE_I / NPE_F 会真的用它，"
            "其他系综下工具不会写出这个关键字。")
    if spec.restart_temperature_annealing:
        plan.notes.append(
            "「接力时接着斜坡走」（RESTART_TEMPERATURE_ANNEALING）在温度程序里不需要："
            "每一段都写了自己的起始温度，CP2K 默认就用输入里的 TEMPERATURE，"
            "而那正是这一段的起点。")

    take = restart_takes(spec.ensemble)
    extension = ".kp" if writes_kpoints(spec) else ".wfn"
    previous = ""
    for stage in stages:
        source = f"{previous}-1.restart" if previous else (seed_restart or None)
        # Hand the wave function over as well: the stages have different project
        # names (that is what keeps their restart/.ener files apart), so a plain
        # SCF_GUESS RESTART in stage N+1 would look for the *wrong* file and fall
        # back to an atomic guess - one wasted SCF start per stage, on a run whose
        # whole point is to stay on the same potential energy surface.
        if previous:
            wfn = f"{previous}-RESTART{extension}"
            guess = "RESTART"
        else:
            wfn = _wfn_of_restart(seed_restart, extension) if seed_restart else None
            guess = "RESTART" if wfn else "ATOMIC"
        work = replace(
            spec,
            task="md",
            project=stage.name,
            temperature=float(stage.start),
            steps=int(stage.steps),
            temperature_annealing=(stage.factor
                                   if stage.mechanism == "temperature" else None),
            annealing=(stage.factor if stage.mechanism == "velocity" else None),
            scf_guess=guess,
            wfn_restart_file=wfn,
            # 20 steps is CP2K's own interval; with a very short stage write at the
            # end only, so the last restart is not stale
            restart_every=min(20, int(stage.steps)),
            ext_restart_file=source,
            ext_restart_take=(take if source else None),
            ext_restart_default=False,
            restart_temperature_annealing=False,
        )
        result = build_inputs(work, index)
        notes = list(result.notes)
        notes.append(_stage_note(stage, source or "", spec, first=not previous))
        text = render_document(
            result.sections,
            title=f"CP2K input generated by cp2kbuild {__version__} - "
                  f"温度程序第 {stage.index} 段（共 {len(stages)} 段）",
            notes=notes)
        path = target / f"{stage.name}.inp"
        write_text_lf(path, text)
        plan.written.append(path)
        previous = stage.name

    table = target / "program.ssv"
    write_text_lf(table, "\n".join("\t".join(row) for row in plan.table()) + "\n")
    plan.written.append(table)

    driver = target / "run_program.sh"
    write_text_lf(driver, program_script(stages, run=run))
    plan.written.append(driver)

    if program_text.strip():
        source = target / "program.txt"
        write_text_lf(source, program_text.rstrip() + "\n")
        plan.written.append(source)

    plan.notes.append(
        "每段的 restart、.ener、轨迹都在这个目录里，名字带段名前缀；"
        "`run_program.sh` 会把每段写出的 restart 归一化成 `<段名>-1.restart`，"
        "下一段读的就是它。")
    if len(stages) > 1:
        plan.notes.append(
            "段边界是**连续**的：坐标、速度（所以瞬时温度 T(t) 不断）、步数计数器、"
            "波函数，以及 NVT/NPT 的恒温器/压浴内部状态都接过来了——各段 `-1.ener` "
            "的 Temp[K] 拼起来就是一条完整曲线。真正不随 restart 走的只有两样："
            "**SCF 外推历史**（ASPC 的环形缓冲只在内存里，`&EXT_RESTART` 没有对应开关，"
            "所以每段第一步会少一点加速、多几次 SCF 迭代，结果不受影响）和"
            "**随机数发生器**（只影响整条轨迹的严格复现，不影响物理；要复现就把 "
            "`RESTART_RANDOMG T` 加进各段的 `&EXT_RESTART`）。每段重启的开销是读 "
            "restart + 波函数、重建网格，相对几千个 MD 步可以忽略。")
        plan.notes.append(
            "接力的不只是坐标/速度：第 2 段起还写上 "
            f"`WFN_RESTART_FILE_NAME <上一段>-RESTART{extension}`，"
            "让每段的 SCF 接着上一段的波函数跑（各段项目名不同，"
            "不写这一行 CP2K 会找不到文件、退回原子初猜，每段都要多花一次 SCF 起步）；"
            "第一段没有可接的就用 `SCF_GUESS ATOMIC`，不会去打那条找不到文件的 warning。")
    return plan


def _wfn_of_restart(restart_name: str | None, extension: str) -> str | None:
    """``equil-1.restart`` -> ``equil-RESTART.wfn`` (CP2K's own file naming).

    Used only for the *seed* restart of the first stage: the wave function that
    sits next to it is what that run was reading, so handing it on saves the new
    stage an atomic guess.  ``None`` when the name does not follow the convention
    (then the stage starts from ``SCF_GUESS ATOMIC`` and says so by itself).
    """
    if not restart_name:
        return None
    name = Path(str(restart_name)).name
    if not name.endswith("-1.restart"):
        return None
    return f"{name[:-len('-1.restart')]}-RESTART{extension}"


def program_text(rows) -> str:
    """``[(T_start, T_end, duration_ps), ...]`` -> the canonical program text.

    The GUI's table and the command line both go through this, so the two front
    ends cannot drift apart on formatting (the self-test compares their output
    byte for byte).
    """
    lines = []
    for start, end, duration in rows:
        if abs(float(end) - float(start)) < 1e-9:
            lines.append(f"hold {float(start):g} {float(duration):g}ps")
        else:
            lines.append(f"ramp {float(start):g} {float(end):g} "
                         f"{float(duration):g}ps")
    return "\n".join(lines) + "\n"


def build_from_text(spec, index, text: str, outdir: str | Path, *,
                    compact: bool = False,
                    seed_restart: str | None = None,
                    run: RunOptions | None = None) -> ProgramPlan:
    """The one call both front ends use: parse -> plan -> write."""
    segments = (parse_compact(text, timestep=float(spec.timestep)) if compact
                else parse_program(text, timestep=float(spec.timestep)))
    stages = plan_program(segments, timestep=float(spec.timestep),
                          ensemble=spec.ensemble)
    return build_program(spec, index, stages, outdir, program_text=text,
                         seed_restart=seed_restart, run=run)


def _stage_note(stage: Stage, source: str, spec, *, first: bool) -> str:
    """The comment line that goes into every stage input."""
    duration = stage.steps * spec.timestep / 1000.0
    what = ("保持" if stage.kind == "hold" else
            ("升温" if stage.end > stage.start else "降温"))
    line = (f"温度程序第 {stage.index} 段：{what} {stage.start:g} → {stage.end:g} K，"
            f"{stage.steps} 步 = {duration:.2f} ps")
    if stage.keyword:
        line += (f"；每步乘 {stage.factor:.8f}（`&MD {stage.keyword}`）"
                 + ("——Langevin 的目标温度" if stage.mechanism == "temperature"
                    else "——速度缩放（温度按平方）"))
    if source:
        line += (f"；从 {source} 接手坐标/速度/计数器"
                 if not first else
                 f"；从 {source} 接手（这是你给的接力文件）")
    else:
        line += "；第一段，速度按这个温度重新抽取"
    return line
