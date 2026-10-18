"""Generate CP2K's own CUTOFF/REL_CUTOFF convergence study - the file set.

The product is exactly what CP2K's tutorial *How to Converge the CUTOFF and
REL_CUTOFF* (https://manual.cp2k.org/trunk/methods/dft/cutoff.html) uses, so it
can be read, edited and re-run by anybody who knows CP2K::

    template.inp            your input, with CUTOFF/REL_CUTOFF replaced by the
                            placeholders ``LT_cutoff`` / ``LT_rel_cutoff``
    cutoff_inputs.sh        one directory per CUTOFF value (``cutoff_50Ry/`` ...)
    cutoff_run.sh           run those jobs (mpirun, several at a time)
    cutoff_analyse.sh       grep/awk -> ``cutoff_data.ssv``
    rel_cutoff_inputs.sh    the same three for the second step
    rel_cutoff_run.sh
    rel_cutoff_analyse.sh

Everything *after* that happens on the cluster: the scripts read the outputs,
the user looks at the tables.  This module deliberately does not parse a single
CP2K output file.

What the tool adds over copying the tutorial by hand is only bookkeeping:

* it plants the two placeholders (or reuses the ones your input already has), so
  ``sed`` cannot end up replacing nothing - which would silently produce N
  identical inputs;
* it fills in the basis/potential file names and the ``@INCLUDE`` files the
  scripts have to copy next to every input (the tutorial assumes you put them
  there yourself);
* it carries the two *fixed* values (step 1's REL_CUTOFF, step 2's CUTOFF) as
  parameters, and makes step 2 refuse to run until the CUTOFF from step 1 is
  filled in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import template as template_module
from .model import render_document
from .paths import read_template_text, write_text_lf, write_template_text

#: The tutorial's ranges, verbatim from its own ``cutoff_inputs.sh`` /
#: ``rel_cutoff_inputs.sh`` (and from the finished example it links to,
#: cp2k-examples/converging_grid): CUTOFF 50-500 Ry in steps of 50,
#: REL_CUTOFF 10-100 Ry in steps of 10.  The second one is the *second* step of
#: the recipe.  Ten values each - the point of "the official range" is that the
#: generated scripts are the tutorial's, not a shortened version of them.
DEFAULT_CUTOFFS = (50, 100, 150, 200, 250, 300, 350, 400, 450, 500)
DEFAULT_REL_CUTOFFS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
#: "We start by setting REL_CUTOFF to a relatively high number" - 60 Ry.
DEFAULT_FIXED_REL_CUTOFF = 60.0

PLACEHOLDER_CUTOFF = "LT_cutoff"
PLACEHOLDER_REL_CUTOFF = "LT_rel_cutoff"

#: What stands in for step 2's CUTOFF until step 1 has produced one.  The
#: generated scripts refuse to run while it is still there.
CHANGE_ME = "CHANGE_ME"

RE_BASIS_FILES = re.compile(r"^\s*BASIS_SET_FILE_NAME\s+(\S+)", re.M | re.I)
RE_POTENTIAL_FILES = re.compile(r"^\s*POTENTIAL_FILE_NAME\s+(\S+)", re.M | re.I)
RE_MAX_SCF_ONE = re.compile(r"^\s*MAX_SCF\s+1\s*$", re.M | re.I)
RE_IGNORE_FAILURE = re.compile(r"IGNORE_CONVERGENCE_FAILURE\s+[Tt]", re.M | re.I)

#: ``RUN_TYPE`` as a literal line (the value is replaced, the layout is kept).
RE_RUN_TYPE = re.compile(r"^([ \t]*RUN_TYPE[ \t]+)(\S+)(.*)$", re.M | re.I)
#: ``@SET RTYPE <token>`` - the tutorial's own template style, where ``RUN_TYPE``
#: itself is written as ``RUN_TYPE ${RTYPE}``.
RE_SET_RTYPE = re.compile(r"^([ \t]*@SET[ \t]+RTYPE[ \t]+)(\S+)(.*)$", re.M | re.I)

#: Settings that only mean something for an MD or a geometry/CELL optimisation.
#: A grid scan is a single-point energy run, so these are reported as dropped
#: when the "current parameters" channel is used from such a page.  The
#: ``&EXT_RESTART`` chain belongs here too: it continues a previous run.
_TASK_ONLY_FIELDS = ("ensemble", "thermostat", "steps", "timestep", "temperature",
                     "optimizer", "max_iter", "external_pressure",
                     "keep_angles", "ext_restart_file", "ext_restart_take",
                     "wfn_restart_file")

#: How those field names are shown in the note (the note is read by users, and
#: only the GUI's own vocabulary is obvious enough to name them there).
_TASK_ONLY_LABELS = {
    "ensemble": "系综", "thermostat": "恒温器", "steps": "MD 步数",
    "timestep": "时间步长", "temperature": "温度",
    "optimizer": "优化器", "max_iter": "优化步数上限",
    "external_pressure": "外压", "keep_angles": "保持晶胞角度",
    "ext_restart_file": "接力用的 restart 文件",
    "ext_restart_take": "接力取哪些量",
    "wfn_restart_file": "波函数 restart 文件",
}

#: ``&EXT_RESTART`` - the block that makes CP2K read a previous run's restart
#: file.  A grid study may not have one (see :func:`neutralize_ext_restart`).
RE_EXT_RESTART_START = re.compile(r"^[ \t]*&EXT_RESTART\b[^\n]*$", re.M | re.I)
RE_EXT_RESTART_BLOCK = re.compile(
    r"^([ \t]*&EXT_RESTART\b[^\n]*)$(.*?)^([ \t]*&END[ \t]+EXT_RESTART\b[^\n]*)$",
    re.M | re.S | re.I)
RE_EXT_RESTART_FILE = re.compile(r"^[ \t]*RESTART_FILE_NAME[ \t]+(\S+)", re.M | re.I)


def neutralize_ext_restart(text: str, where: str = "输入") -> tuple[str, list[str]]:
    """Comment out ``&EXT_RESTART``: a grid study must not chain to a restart.

    ``RUN_TYPE ENERGY`` plus a restart file is not a single point run any more -
    CP2K takes coordinates, velocities and the step counters from it, and
    **aborts** when the file is not there::

        [ABORT] The specified OLD file <x-1_40000.restart> cannot be opened.
                common/cp_files.F:406

    That is the one failure a scan must never have: every parameter point would
    die during input checking, before the first SCF step, and the points would
    depend on each other (each one reads what the previous one wrote).  The block
    is not deleted - every line gets CP2K's comment character ``#`` in front of
    it, so the original text is still readable and can be restored by hand.
    """
    notes: list[str] = []
    matches = list(RE_EXT_RESTART_BLOCK.finditer(text))
    if matches:
        name = RE_EXT_RESTART_FILE.search(text)
        for match in reversed(matches):
            block = match.group(0)
            commented = "\n".join("#" + line for line in block.split("\n"))
            text = text[:match.start()] + commented + text[match.end():]
        notes.append(
            f"{where}里的 &EXT_RESTART 整段注释掉了"
            + (f"（它读的是 {name.group(1)}）" if name else "")
            + "：网格扫描的每一档都必须是独立的单点能，而这个块会让每档都去读"
            "上一次的 restart 文件——文件不在（或者被上一档覆盖了）就会在检查输入时"
            "ABORT（common/cp_files.F:406），一个参数点都跑不出来。"
            "原文还在，只是每行前面加了 `#`。")
    elif RE_EXT_RESTART_START.search(text):
        notes.append(
            f"⚠ {where}里有 &EXT_RESTART 但找不到对应的 &END EXT_RESTART，"
            "没法安全注释：请自己删掉这个块，否则每档都会去读那个 restart 文件。")
    return text, notes



@dataclass
class StudySource:
    """Where ``template.inp`` comes from: an existing input, or the form.

    Both channels end up as *text* plus the files that travel with it, so the
    rest of the pipeline does not care which one was used.
    """

    name: str                     # how the generated input is named per point
    text: str                     # the template text, before the placeholders
    origin: str                   # for the log: "「生成输入」页的参数" / a file name
    side_files: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CutoffPlan:
    """What was generated, plus the lines the CLI prints and the GUI shows."""

    input_path: Path
    outdir: Path
    input_name: str
    cutoffs: tuple
    rel_cutoffs: tuple
    fixed_rel_cutoff: float
    fixed_cutoff: float | None
    basis_files: list[str] = field(default_factory=list)
    potential_files: list[str] = field(default_factory=list)
    side_files: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = [f"wrote {len(self.written)} file(s) to {self.outdir}"]
        lines += [f"  {path.name}" for path in self.written]
        lines.append("")
        lines.append("拷到服务器上（整个目录），然后：")
        lines.append(f"  cd {self.outdir.name}")
        lines.append("  sh cutoff_inputs.sh && sh cutoff_run.sh && sh cutoff_analyse.sh")
        lines.append("  # 看 cutoff_data.ssv 定下 CUTOFF，填进 rel_cutoff_inputs.sh / "
                     "rel_cutoff_run.sh / rel_cutoff_analyse.sh 的 cutoff= 再跑第二套")
        lines.append("  sh rel_cutoff_inputs.sh && sh rel_cutoff_run.sh && "
                     "sh rel_cutoff_analyse.sh")
        for note in self.notes:
            lines.append("")
            lines.append("note: " + note)
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def parse_values(text) -> list:
    """``"50,100 150" -> [50, 100, 150]``; keep the numbers as ints when they are."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return list(text)
    values: list = []
    for token in re.split(r"[,\s]+", str(text).strip()):
        if not token:
            continue
        try:
            number = float(token)
        except ValueError:
            continue
        values.append(int(number) if number.is_integer() else number)
    return values


def _numbers(values, default, keyword: str) -> tuple:
    parsed = parse_values(values) if values else list(default)
    if not parsed:
        raise ValueError(f"{keyword} 没有给出任何取值")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in parsed):
        raise ValueError(f"{keyword} 的取值必须是正数：{parsed}")
    return tuple(parsed)


def _shell_list(values) -> str:
    return " ".join(f"{value:g}" for value in values)


def _output_name(source: "StudySource") -> str:
    """``system.out``: the output file name inside every parameter directory."""
    return Path(source.name).stem + ".out"


def _expand_includes(path: Path, seen: set[Path] | None = None) -> str:
    """The template's text with its ``@INCLUDE`` files inlined (for grepping)."""
    seen = seen if seen is not None else set()
    try:
        resolved = path.resolve()
    except OSError:
        return ""
    if resolved in seen or not path.is_file():
        return ""
    seen.add(resolved)
    text = read_template_text(path)
    for name in template_module.RE_INCLUDE.findall(text):
        target = path.parent / name.strip("\"'")
        text += "\n" + _expand_includes(target, seen)
    return text


def _declared_files(text: str, pattern: re.Pattern) -> list[str]:
    out: list[str] = []
    for name in pattern.findall(text):
        name = name.strip("\"'")
        if name and name not in out:
            out.append(name)
    return out


def prepare_template(text: str) -> tuple[str, list[template_module.Substitution], list[str]]:
    """Put ``LT_cutoff`` / ``LT_rel_cutoff`` into the template.

    Whatever the input already uses is reused: an existing placeholder stays,
    ``@SET CUTOFF LT_cutoff`` keeps its token, and a literal ``CUTOFF 400`` is
    turned into ``CUTOFF LT_cutoff``.  A keyword that is nowhere to be found is
    reported instead of guessed - the generated scripts would otherwise replace
    nothing and produce N identical inputs.
    """
    substitutions: list[template_module.Substitution] = []
    notes: list[str] = []
    # REL_CUTOFF first: "CUTOFF" must never be matched inside it
    for keyword, placeholder in (("REL_CUTOFF", PLACEHOLDER_REL_CUTOFF),
                                 ("CUTOFF", PLACEHOLDER_CUTOFF)):
        strategy = template_module.detect(text, keyword)
        if strategy is None:
            notes.append(
                f"输入里找不到 {keyword}：请确认它是一份带 &MGRID 的输入，"
                f"或者自己把那一行写成 {placeholder}。")
            continue
        text, info = template_module.replace(text, keyword, placeholder)
        if info is not None:
            substitutions.append(info)
    return text, substitutions, notes


# --------------------------------------------------------------------------- #
# where the template comes from
# --------------------------------------------------------------------------- #
#: The tutorial's cheap recipe, applied when the *tool* writes the template.
GRID_PROTOCOL_NOTE = (
    "官方网格扫描协议（省机时）：RUN_TYPE ENERGY + MAX_SCF 1 + OUTER_SCF MAX_SCF 0 + "
    "IGNORE_CONVERGENCE_FAILURE TRUE + SCF_GUESS ATOMIC，不算力、"
    "PRINT_LEVEL ≥ MEDIUM（每个参数点只跑 1 步 SCF）")

#: What to add by hand when the template is your own file.
GRID_PROTOCOL_HAND_LINES = ("SCF_GUESS ATOMIC", "MAX_SCF 1",
                            "IGNORE_CONVERGENCE_FAILURE TRUE")

_PRINT_LEVELS = ("SILENT", "LOW", "MEDIUM", "HIGH", "DEBUG")


def apply_grid_protocol(spec) -> None:
    """Put CP2K's own cheap grid-scan settings into a :class:`Spec`."""
    spec.task = "energy"
    spec.scf_guess = "ATOMIC"
    spec.max_scf = 1
    # no *outer* loop either: the tool writes &OUTER_SCF MAX_SCF 20 by default,
    # which would turn "one SCF step" into twenty
    spec.outer_max_scf = 0
    spec.ignore_convergence_failure = True
    spec.print_forces = False
    spec.wfn_restart_print = False          # no wave function dump per point
    # no chain to a previous run either: a restart file that is not shipped with
    # the test aborts every point, and the points would depend on each other
    spec.ext_restart_file = None
    spec.ext_restart_take = None
    spec.wfn_restart_file = None
    level = (spec.print_level or "MEDIUM").upper()
    if level not in _PRINT_LEVELS or \
            _PRINT_LEVELS.index(level) < _PRINT_LEVELS.index("MEDIUM"):
        spec.print_level = "MEDIUM"


def source_from_input(path: str | Path) -> StudySource:
    """The template channel: an existing input, taken as it is written.

    Nothing is reformatted - the file is kept verbatim (``@SET``/``@INCLUDE``,
    comments and line endings), and only ``RUN_TYPE`` is looked at; see
    :func:`align_run_type`.
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"找不到输入文件：{source}")
    return StudySource(name=source.name, text=read_template_text(source),
                       origin=source.name,
                       side_files=template_module.side_files(source))


def source_from_spec(spec, index) -> StudySource:
    """The "current parameters" channel: build the input out of the form.

    A grid study is an *energy* run (the total energy is what CUTOFF/REL_CUTOFF
    are converged against), so the task is forced to ``energy`` - otherwise the
    page's MD or geometry-optimisation input would be run for every cutoff value,
    which is both wrong and hundreds of times more expensive.  On top of that the
    tool writes CP2K's own cheap recipe for this test, because here it is the one
    writing the input: one SCF step per point, no forces, MULTIGRID INFO kept.
    """
    import dataclasses

    from . import __version__
    from .recipes import build as build_inputs, is_classical

    if is_classical(spec):
        raise ValueError(
            "METHOD FIST 是经典力场：没有 CUTOFF/REL_CUTOFF 可收敛。"
            "要做经典 MD 请回「生成输入」页；要扫截断能请把方法改成 Quickstep。")

    # what the user set that a single-point energy run has no use for: compare
    # against the *defaults for the task they were on* (not against the energy
    # copy, whose untouched fields are identical by construction)
    baseline = type(spec)(task=getattr(spec, "task", "energy"))
    dropped = [name for name in _TASK_ONLY_FIELDS
               if getattr(spec, name, None) != getattr(baseline, name, None)]

    work = dataclasses.replace(spec, task="energy")
    apply_grid_protocol(work)
    result = build_inputs(work, index)
    text = render_document(result.sections,
                           title=f"CP2K input generated by cp2kbuild {__version__}",
                           notes=list(result.notes))
    project = (getattr(work, "project", None) or "cp2k").strip() or "cp2k"
    suffix = ("你在这页填的 MD/接力/优化选项（"
              + "、".join(_TASK_ONLY_LABELS.get(name, name) for name in dropped)
              + "）也不会进这份模板：网格扫描每个点都是单点能。"
              if dropped else "")
    return StudySource(
        name=f"{project}.inp", text=text, origin="「生成输入」页此刻的参数",
        notes=["template.inp 是按「生成输入」页的参数生成的：RUN_TYPE 固定成 ENERGY"
               "（网格扫描比的是总能），并按官方教程写成 " + GRID_PROTOCOL_NOTE + "。"
               + suffix + "想换成自己写好的输入，把来源改成「已有输入」。"])


def align_run_type(text: str) -> tuple[str, list[str]]:
    """Force ``RUN_TYPE ENERGY``: that is what a grid study compares.

    Every point of a CUTOFF/REL_CUTOFF scan has to be a single-point energy run -
    an MD or geometry-optimisation input would do something else entirely.  Both
    spellings CP2K inputs use are handled: a literal ``RUN_TYPE MD`` line, and the
    tutorial's ``@SET RTYPE ...`` + ``RUN_TYPE ${RTYPE}``.
    """
    notes: list[str] = []
    match = RE_SET_RTYPE.search(text)
    if match and "${RTYPE}" in text:
        before = match.group(2)
        if before.upper() != "ENERGY":
            text = RE_SET_RTYPE.sub(
                lambda m: f"{m.group(1)}ENERGY{m.group(3)}", text)
            notes.append(f"RUN_TYPE 已改成 ENERGY（原 `@SET RTYPE {before}`）——"
                         "截断能扫描比的是总能，每个点都必须按单点能跑。")
        return text, notes

    match = RE_RUN_TYPE.search(text)
    if match is None:
        notes.append("输入里没有 RUN_TYPE：CP2K 默认是 ENERGY，但仍请确认一下。")
        return text, notes
    current = match.group(2)
    if current.upper() == "ENERGY":
        return text, notes
    if current.startswith(("$", "@")):
        notes.append(
            f"⚠ 输入里的 RUN_TYPE 是 `{current}`（预处理器/变量），工具改不了；"
            "请确认展开以后是 ENERGY，否则每个参数点跑的不是单点能。")
        return text, notes
    text = RE_RUN_TYPE.sub(lambda m: f"{m.group(1)}ENERGY{m.group(3)}", text)
    notes.append(f"RUN_TYPE 已从 {current.upper()} 改成 ENERGY——"
                 "截断能扫描比的是总能，每个点都必须按单点能跑。")
    return text, notes


# --------------------------------------------------------------------------- #
# the shell scripts (the tutorial's, with the values filled in)
# --------------------------------------------------------------------------- #
def _header(title: str, extra: list[str] | None = None) -> list[str]:
    # POSIX sh on purpose: the tutorial's scripts use ``#!/bin/bash`` with
    # ``for ((...))``, which a dash-style /bin/sh refuses ("Bad for loop
    # variable").  Everything below is safe in sh, bash and dash, so
    # ``sh script.sh``, ``bash script.sh`` and ``./script.sh`` all work.
    lines = ["#!/bin/sh",
             f"# {title}",
             "# 由 cp2kbuild 生成；做法来自 CP2K 官方教程",
             "# https://manual.cp2k.org/trunk/methods/dft/cutoff.html"]
    if extra:
        lines += ["#"] + [f"# {line}" for line in extra]
    return lines


def inputs_script(*, keyword: str, values, fixed_name: str, fixed_value,
                  input_name: str, basis_files, potential_files,
                  side_files) -> str:
    """``cutoff_inputs.sh`` / ``rel_cutoff_inputs.sh``: sed the template."""
    var = keyword.lower() + "s"      # the tutorial's names: cutoffs / rel_cutoffs
    other = "rel_cutoff" if keyword == "CUTOFF" else "cutoff"
    work_prefix = "cutoff" if keyword == "CUTOFF" else "rel_cutoff"
    # The swept keyword gets the loop value, the *other* one keeps the fixed
    # value.  For step 1 that is ``LT_rel_cutoff -> ${rel_cutoff}`` and
    # ``LT_cutoff -> ${ii}``; for step 2 it is exactly the other way round
    # (the official rel_cutoff_inputs.sh does ``s/LT_cutoff/${cutoff}/g`` first,
    # then ``s/LT_rel_cutoff/${ii}/g``) - hard-coding step 1's two lines here
    # would sweep CUTOFF twice and leave the REL_CUTOFF column meaningless.
    swept = PLACEHOLDER_REL_CUTOFF if keyword == "REL_CUTOFF" else PLACEHOLDER_CUTOFF
    fixed = PLACEHOLDER_CUTOFF if keyword == "REL_CUTOFF" else PLACEHOLDER_REL_CUTOFF
    lines = _header(
        f"生成 {keyword} 扫描的输入（每个值一个目录）",
        ["只替换 template.inp 里的 LT_cutoff / LT_rel_cutoff，别的字节一个不动。",
         "如果模板里有 @INCLUDE，被包含的文件也要放在同一个目录里（已一起复制）。"])
    lines += [
        "",
        f'{var}="{_shell_list(values)}"',
        f'basis_files="{" ".join(basis_files)}"',
        f'potential_files="{" ".join(potential_files)}"',
        f'side_files="{" ".join(side_files)}"',
        "template_file=template.inp",
        f"input_file={input_name}",
        f"{other}={fixed_value:g}" if isinstance(fixed_value, (int, float))
        else f'{other}="{fixed_value}"',
    ]
    if fixed_value == CHANGE_ME:
        lines += [
            f"# ⚠ 第 2 步的 {other.upper()} 还没定：把上面这一行改成第 1 步"
            f"（{('cutoff' if other == 'cutoff' else 'rel_cutoff')}_analyse.sh）"
            "给出的值再跑。",
            f'if [ "$' + other + f'" = "{CHANGE_ME}" ] ; then',
            f'    echo "先把脚本开头的 {other} 改成第 1 步定下来的值再跑。" >&2',
            "    exit 1",
            "fi",
        ]
    lines += [
        "",
        f"for ii in ${var} ; do",
        f"    work_dir={work_prefix}_${{ii}}Ry",
        "    if [ ! -d $work_dir ] ; then",
        "        mkdir $work_dir",
        "    else",
        "        rm -rf $work_dir/*",
        "    fi",
        f'    sed -e "s/{fixed}/${{{other}}}/g" \\',
        f'        -e "s/{swept}/${{ii}}/g" \\',
        f"        $template_file > $work_dir/$input_file",
        '    for f in $basis_files $potential_files $side_files ; do',
        '        [ -n "$f" ] && cp "$f" "$work_dir"',
        "    done",
        "done",
        f'echo "生成了 $(ls -d {work_prefix}*Ry 2>/dev/null | wc -l) 个目录："'
        f" {work_prefix}*Ry",
        "",
    ]
    return "\n".join(lines)


def run_script(*, keyword: str, values, input_name: str, output_name: str) -> str:
    """``cutoff_run.sh`` / ``rel_cutoff_run.sh``: run the jobs."""
    var = keyword.lower() + "s"
    work_prefix = "cutoff" if keyword == "CUTOFF" else "rel_cutoff"
    lines = _header(
        f"跑 {keyword} 扫描（每档一个目录）",
        ["改下面这几个变量就能适配你的机器；mpirun_bin 留空就是串行。",
         "几个算例同时跑：no_proc_to_use / no_proc_per_calc 就是同时跑几个。"])
    lines += [
        "",
        "mpirun_bin=mpirun",
        "cp2k_bin=cp2k.popt",
        "no_proc_per_calc=2",
        "no_proc_to_use=16",
        "",
        f'{var}="{_shell_list(values)}"',
        f"input_file={input_name}",
        f"output_file={output_name}",
        "",
        "counter=1",
        "max_parallel_calcs=$((no_proc_to_use / no_proc_per_calc))",
        "[ $max_parallel_calcs -lt 1 ] && max_parallel_calcs=1",
        f"for ii in ${var} ; do",
        f"    work_dir={work_prefix}_${{ii}}Ry",
        "    if [ ! -d $work_dir ] ; then",
        f'        echo "没有 $work_dir：先跑 {work_prefix}_inputs.sh" >&2',
        "        continue",
        "    fi",
        "    cd $work_dir",
        '    [ -f $output_file ] && rm $output_file',
        '    if [ -z "$mpirun_bin" ] ; then',
        '        $cp2k_bin -o $output_file $input_file &',
        "    else",
        "        # --bind-to none：官方脚本用它避免从同一个进程启动的算例抢同一批核",
        "        $mpirun_bin -np $no_proc_per_calc --bind-to none "
        "$cp2k_bin -o $output_file $input_file &",
        "    fi",
        "    cd ..",
        "    if [ $((counter % max_parallel_calcs)) -eq 0 ] ; then wait ; fi",
        "    counter=$((counter + 1))",
        "done",
        "wait",
        "",
    ]
    return "\n".join(lines)


def analyse_script(*, keyword: str, values, input_name: str, output_name: str,
                   fixed_name: str, fixed_value, ssv_name: str) -> str:
    """``*_analyse.sh``: grep/awk the outputs into a ``.ssv`` table."""
    var = keyword.lower() + "s"
    work_prefix = "cutoff" if keyword == "CUTOFF" else "rel_cutoff"
    fixed_var = fixed_name.lower()
    lines = _header(
        f"汇总 {keyword} 扫描：写 {ssv_name}",
        ["只需要 sh + grep + awk，集群上没有 Python 也能跑。",
         "把 .ssv 拖进 gnuplot/Excel 就能画；判据自己定（官方教程里是<1e-6 Ry，",
         "实际常用每原子 1 meV 量级）。",
         "前提：输出的 PRINT_LEVEL ≥ MEDIUM，否则没有 'count for grid' 那几行。"])
    if fixed_value == CHANGE_ME:
        lines += [
            "",
            f'if [ "$' + fixed_var + f'" = "{CHANGE_ME}" ] ; then',
            f'    echo "先把脚本开头的 {fixed_var} 改成第 1 步定下来的值再跑。" >&2',
            "    exit 1",
            "fi",
        ]
    lines += [
        "",
        f'{var}="{_shell_list(values)}"',
        f"{fixed_var}=" + (f"{fixed_value:g}" if isinstance(fixed_value, (int, float))
                           else f'"{fixed_value}"'),
        f"input_file={input_name}",
        f"output_file={output_name}",
        f"plot_file={ssv_name}",
        "",
        f'echo "# Grid cutoff vs total energy" > $plot_file',
        'echo "# Date: $(date)" >> $plot_file',
        'echo "# PWD: $PWD" >> $plot_file',
        f'echo "# {fixed_name} = ${fixed_var}" >> $plot_file',
        f'echo -n "# {"Rel " if keyword == "REL_CUTOFF" else ""}Cutoff (Ry) '
        '| Total Energy (Ha)" >> $plot_file',
        "grid_header=true",
        f"for ii in ${var} ; do",
        f"    work_dir={work_prefix}_${{ii}}Ry",
        "    if [ ! -f $work_dir/$output_file ] ; then",
        '        echo "# 缺 $work_dir/$output_file（这一档没跑完？）" >&2',
        "        continue",
        "    fi",
        "    total_energy=$(grep -e '^[ \\t]*Total energy' $work_dir/$output_file "
        "| awk '{print $3}')",
        "    ngrids=$(grep -e '^[ \\t]*QS| Number of grid levels:' "
        "$work_dir/$output_file | awk '{print $6}')",
        "    : ${ngrids:=0}",
        "    if $grid_header ; then",
        "        igrid=1",
        "        while [ $igrid -le $ngrids ] ; do",
        '            printf " | NG on grid %d" $igrid >> $plot_file',
        "            igrid=$((igrid + 1))",
        "        done",
        "        printf \"\\n\" >> $plot_file",
        "        grid_header=false",
        "    fi",
        '    printf "%10.2f  %15.10f" $ii $total_energy >> $plot_file',
        "    igrid=1",
        "    while [ $igrid -le $ngrids ] ; do",
        "        grid=$(grep -e '^[ \\t]*count for grid' $work_dir/$output_file "
        "| awk -v igrid=$igrid '(NR == igrid){print $5}')",
        "        : ${grid:=0}",
        '        printf "  %6d" $grid >> $plot_file',
        "        igrid=$((igrid + 1))",
        "    done",
        '    printf "\\n" >> $plot_file',
        "done",
        "cat $plot_file",
        "",
        f'echo "\\n$plot_file 里每档的差值是相对最细一档算的，自己在 gnuplot/Excel 里看。"',
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the whole study
# --------------------------------------------------------------------------- #
def build_cutoff_study(source, outdir: str | Path, *,
                       cutoffs=None, rel_cutoffs=None,
                       fixed_rel_cutoff: float = DEFAULT_FIXED_REL_CUTOFF,
                       fixed_cutoff: float | None = None) -> CutoffPlan:
    """Write ``template.inp`` + the six scripts into ``outdir``.

    ``source`` is a :class:`StudySource` (see :func:`source_from_input` and
    :func:`source_from_spec`) or, for convenience, the path of an existing input.
    """
    if not isinstance(source, StudySource):
        source = source_from_input(source)
    target_dir = Path(outdir)
    target_dir.mkdir(parents=True, exist_ok=True)

    step1 = _numbers(cutoffs, DEFAULT_CUTOFFS, "CUTOFF")
    step2 = _numbers(rel_cutoffs, DEFAULT_REL_CUTOFFS, "REL_CUTOFF")
    if fixed_rel_cutoff is None:
        fixed_rel_cutoff = DEFAULT_FIXED_REL_CUTOFF
    fixed_step2 = CHANGE_ME if fixed_cutoff in (None, "") else float(fixed_cutoff)

    # a grid study is an energy run: say so if the input was not one
    text, run_type_notes = align_run_type(source.text)
    # ... and it must not chain to a previous run's restart file
    text, restart_notes = neutralize_ext_restart(text)
    prepared, substitutions, notes = prepare_template(text)
    notes = list(source.notes) + run_type_notes + restart_notes + notes
    included = "".join(
        read_template_text(path) for path in source.side_files
        if path.suffix.lower() in (".inc", ".inp"))
    expanded = text + "\n" + included
    # an @INCLUDE is copied verbatim, so an &EXT_RESTART inside one cannot be
    # commented out here - it has to be pointed out instead
    if RE_EXT_RESTART_START.search(included):
        notes.append(
            "@INCLUDE 进来的文件里有 &EXT_RESTART：工具不改你的 include 文件，"
            "请自己把那个块删掉（它的 RESTART_FILE_NAME 在集群上不存在就会 "
            "ABORT，common/cp_files.F:406），否则每一档都跑不起来。")
    basis_files = _declared_files(expanded, RE_BASIS_FILES)
    potential_files = _declared_files(expanded, RE_POTENTIAL_FILES)
    side_files = [path.name for path in source.side_files]

    plan = CutoffPlan(
        input_path=Path(source.origin), outdir=target_dir,
        input_name=source.name,
        cutoffs=step1, rel_cutoffs=step2,
        fixed_rel_cutoff=float(fixed_rel_cutoff),
        fixed_cutoff=(None if fixed_step2 == CHANGE_ME else float(fixed_step2)),
        basis_files=basis_files, potential_files=potential_files,
        side_files=side_files, notes=notes)

    # the template: bytes-preserving, only the two values were replaced
    write_template_text(target_dir / "template.inp", prepared)
    plan.written.append(target_dir / "template.inp")

    scripts = {
        "cutoff_inputs.sh": inputs_script(
            keyword="CUTOFF", values=step1, fixed_name="REL_CUTOFF",
            fixed_value=float(fixed_rel_cutoff), input_name=source.name,
            basis_files=basis_files, potential_files=potential_files,
            side_files=side_files),
        "cutoff_run.sh": run_script(
            keyword="CUTOFF", values=step1, input_name=source.name,
            output_name=_output_name(source)),
        "cutoff_analyse.sh": analyse_script(
            keyword="CUTOFF", values=step1, input_name=source.name,
            output_name=_output_name(source), fixed_name="REL_CUTOFF",
            fixed_value=float(fixed_rel_cutoff), ssv_name="cutoff_data.ssv"),
        "rel_cutoff_inputs.sh": inputs_script(
            keyword="REL_CUTOFF", values=step2, fixed_name="CUTOFF",
            fixed_value=fixed_step2, input_name=source.name,
            basis_files=basis_files, potential_files=potential_files,
            side_files=side_files),
        "rel_cutoff_run.sh": run_script(
            keyword="REL_CUTOFF", values=step2, input_name=source.name,
            output_name=_output_name(source)),
        "rel_cutoff_analyse.sh": analyse_script(
            keyword="REL_CUTOFF", values=step2, input_name=source.name,
            output_name=_output_name(source), fixed_name="CUTOFF",
            fixed_value=fixed_step2, ssv_name="rel_cutoff_data.ssv"),
    }
    for name, text in scripts.items():
        write_text_lf(target_dir / name, text)
        plan.written.append(target_dir / name)

    # the files the scripts copy next to every input
    copied: list[Path] = []
    for path in source.side_files:
        destination = target_dir / path.name
        try:
            if path.resolve() != destination.resolve():
                destination.write_bytes(path.read_bytes())
        except OSError as exc:            # noqa: BLE001 - report, never crash
            notes.append(f"复制 {path.name} 失败：{exc}")
            continue
        copied.append(path)
    if copied:
        plan.notes.append("这些文件也复制过来了（脚本会再复制进每个参数点目录）："
                          + "、".join(path.name for path in copied))

    # what the scripts cannot know about your input
    planted = {info.keyword for info in substitutions}
    for keyword, placeholder in (("CUTOFF", PLACEHOLDER_CUTOFF),
                                 ("REL_CUTOFF", PLACEHOLDER_REL_CUTOFF)):
        if keyword not in planted and placeholder not in prepared:
            plan.notes.append(
                f"template.inp 里没有 {keyword}（也没有占位符）：脚本的 sed "
                f"替换不到东西，请自己把那一行写成 {placeholder}。")
    if basis_files or potential_files:
        missing = [name for name in basis_files + potential_files
                   if not (target_dir / name).exists() and not Path(name).is_file()]
        if missing:
            plan.notes.append("这些文件不在本地，请自己放到目录里（脚本要 cp 它们）："
                              + "、".join(missing))
    else:
        plan.notes.append(
            "template.inp 里没写 BASIS_SET_FILE_NAME / POTENTIAL_FILE_NAME："
            "如果计算需要它们，请自己放进目录，并把脚本开头那两行变量填好。")
    if not (RE_MAX_SCF_ONE.search(expanded) and RE_IGNORE_FAILURE.search(expanded)):
        plan.notes.append(
            "这份输入没有 MAX_SCF 1 + IGNORE_CONVERGENCE_FAILURE：官方教程的网格扫描"
            "不收敛 SCF（每个点只跑 1 步，快一个量级）。想按教程走，在 &SCF 里加上："
            "SCF_GUESS ATOMIC / MAX_SCF 1 / IGNORE_CONVERGENCE_FAILURE TRUE；"
            "不加也能跑，只是每个点都是完整的 SCF。")
    if fixed_cutoff in (None, ""):
        plan.notes.append(
            "第 2 步的 CUTOFF 还没定：先跑第 1 步（cutoff_inputs.sh → cutoff_run.sh → "
            "cutoff_analyse.sh），把 cutoff_data.ssv 里定下来的值填进 "
            "rel_cutoff_inputs.sh / rel_cutoff_run.sh / rel_cutoff_analyse.sh 的 "
            f"cutoff= 那一行（现在是 {CHANGE_ME}，没改之前脚本会拒绝跑）。")
    return plan
