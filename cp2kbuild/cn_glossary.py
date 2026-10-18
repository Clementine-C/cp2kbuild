"""Chinese layer for the manual browser: curated notes, terminology, search.

Three things live here, and they are deliberately kept apart from the manual's
own text:

* :data:`KEYWORDS` - hand written Chinese notes for the keywords people actually
  search for.  These are **cp2kbuild's own explanations, not a translation of
  the manual**, and the interface labels them as such.
* :data:`TERMS` - a EnglishChinese glossary.  The manual browser highlights these
  terms inside the original English description so the text can be read without
  leaving the tool.
* the helpers that let a Chinese query find the right English keyword.

Nothing here invents a translation of a manual paragraph: a wrong translation of
a technical description is worse than no translation, so where there is no
curated note the original English is shown with terminology hints only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Cn:
    name: str            # 中文名，用于列表显示与中文搜索
    text: str            # 中文说明（cp2kbuild 自己写的，不是手册译文）
    extra: tuple[str, ...] = ()   # 其它可用于搜索的中文说法


# --------------------------------------------------------------------------- #
# curated notes
# --------------------------------------------------------------------------- #
KEYWORDS: dict[str, Cn] = {
    # ---------------- GLOBAL ----------------
    "GLOBAL/RUN_TYPE": Cn(
        "运行类型",
        "决定这次跑什么：ENERGY（单点能）、ENERGY_FORCE（单点能+力）、"
        "GEO_OPT（几何优化）、CELL_OPT（晶胞优化）、MD（分子动力学）、"
        "VIBRATIONAL_ANALYSIS（频率）、BAND（NEB 过渡态）。\n"
        "它只切换执行流程，具体参数在对应的 &MOTION 段里。"
        "注意 ENERGY 不会做任何优化。\n"
        "2026.2 新增 RTP（实时传播）和 MIMIC，"
        "并移除了旧的 ELECTRONIC_SPECTRA / SPECTRA（用 XAS、TDDFPT 等替代）。",
        ("计算类型", "任务类型", "单点能", "几何优化", "分子动力学",
         "频率计算", "过渡态搜索", "晶胞优化")),
    "GLOBAL/PROJECT_NAME": Cn(
        "项目名",
        "输出的前缀名。CP2K 用它生成 <项目名>.out、<项目名>-1.restart、"
        "<项目名>-pos-1.xyz 等文件。\n"
        "旧写法是 PROJECT（同一个关键字）。名字里不要有空格。"),
    "GLOBAL/PRINT_LEVEL": Cn(
        "输出详细程度",
        "SILENT / LOW / MEDIUM / HIGH / DEBUG。MEDIUM 是默认值，"
        "排查 SCF 不收敛时用 HIGH 能看到每一步的能量和 DIIS 信息。"),
    "GLOBAL/WALLTIME": Cn(
        "墙钟时限",
        "单位秒。到点 CP2K 会正常收尾并写 restart 文件，而不是被调度器杀掉。"
        "建议设成队列时限的 95%。"),
    "GLOBAL/SEED": Cn(
        "随机数种子",
        "全局随机数发生器的初始种子，给 1 个或 6 个正整数。"
        "MD 的初始速度由它决定，所以固定它可以让同一次模拟完全复现。"),

    # ---------------- FORCE_EVAL ----------------
    "FORCE_EVAL/METHOD": Cn(
        "力场/方法",
        "QUICKSTEP（DFT，绝大多数情况）、FIST（经典力场）、"
        "QMMM（QM/MM）、EIP、NNP（机器学习势）等。\n"
        "选 QUICKSTEP 时才需要 &DFT 段。"),
    "FORCE_EVAL/STRESS_TENSOR": Cn(
        "应力张量算法",
        "做 CELL_OPT 时必须算应力。analytical 是解析应力，比默认的数值差分更快更准，"
        "推荐给 CELL_OPT 用。"),

    # ---------------- DFT ----------------
    "FORCE_EVAL/DFT/BASIS_SET_FILE_NAME": Cn(
        "基组文件名",
        "告诉 CP2K 去哪里找基组。可以写多行来加载多个文件。\n"
        "常用 BASIS_MOLOPT（分子优化基组）、BASIS_SET、GTH_BASIS_SETS。"
        "运行时会先在输入文件所在目录找，再找 CP2K_DATA_DIR。",
        ("基组文件",)),
    "FORCE_EVAL/DFT/POTENTIAL_FILE_NAME": Cn(
        "赝势文件名",
        "同上，找赝势用。常见 GTH_POTENTIALS、POTENTIAL。\n"
        "里面必须有你在 &KIND 里写的那个势（如 GTH-PBE-q6）。",
        ("赝势文件",)),
    "FORCE_EVAL/DFT/CHARGE": Cn(
        "总电荷",
        "整个体系的总电荷（不是某个原子的）。带电体系能量收敛慢，"
        "建议加中和背景电荷或显式反离子。填错最典型的表现是 SCF 不收敛。"),
    "FORCE_EVAL/DFT/MULTIPLICITY": Cn(
        "自旋多重度",
        "2S+1。1=单重态，2=双重态，3=三重态。\n"
        "电子数与多重度必须同奇偶：9 个电子只能是 2、4、6…；"
        "不写时 CP2K 按电子数自动取 1 或 2。",
        ("自旋", "多重度")),
    "FORCE_EVAL/DFT/UKS": Cn(
        "自旋极化",
        "Unrestricted Kohn-Sham：alpha/beta 轨道分开算。\n"
        "开壳层体系、磁性体系、过渡金属配合物需要它；代价约为闭壳层的 1.5–2 倍。"
        "新版 CP2K 在给了 MULTIPLICITY 时会自动打开。"),

    "FORCE_EVAL/DFT/MGRID/CUTOFF": Cn(
        "平面波截断能",
        "最细网格的截断能（Ry）。**这是最需要自己收敛的参数**。\n"
        "经验起点：SZV≈350、DZVP≈400、TZVP≈600、TZV2P≈700 Ry。\n"
        "把 CUTOFF 翻倍看总能量变化，小于 1 meV/atom 通常就够用了。",
        ("截断能", "截断", "cutoff")),
    "FORCE_EVAL/DFT/MGRID/REL_CUTOFF": Cn(
        "相对截断能",
        "决定一个单位高斯被映射到哪一层网格，经验值 60 Ry（弥散基组可到 80）。\n"
        "它必须小于 CUTOFF，否则最细网格装不下这个高斯。",
        ("相对截断",)),
    "FORCE_EVAL/DFT/MGRID/NGRIDS": Cn(
        "多重网格层数",
        "默认 4。层数多则不同宽度的高斯各有合适的网格，效率高；"
        "层数太少会让高斯挤在最细网格上，变慢。一般不用改。",
        ("网格层数",)),
    "FORCE_EVAL/DFT/MGRID/PROGRESSION_FACTOR": Cn(
        "网格递增因子",
        "默认 3.0，即相邻网格的截断能差 3 倍。"),

    "FORCE_EVAL/DFT/QS/METHOD": Cn(
        "GPW 方法",
        "GPW（高斯平面波，默认，赝势体系）、GAPW（高斯增广平面波，全电子或需要准确芯区时）、"
        "GAPW_XC、DFTB、PM3 等半经验方法。\n"
        "GAPW 更贵但芯区描述更准。",
        ("方法",)),
    "FORCE_EVAL/DFT/QS/EPS_DEFAULT": Cn(
        "积分精度阈值",
        "默认 1E-10。它控制所有积分和滤波的精度，相当于总开关："
        "调它会影响一系列导出阈值。能量差要做精细对比时用 1E-12。",
        ("积分精度",)),
    "FORCE_EVAL/DFT/QS/EXTRAPOLATION": Cn(
        "波函数外推",
        "MD/优化时用上一步的结果当初猜。ASPC 是推荐值；"
        "单点能计算没有上一步，用 USE_GUESS 或 PS。",
        ("外推",)),

    "FORCE_EVAL/DFT/SCF/SCF_GUESS": Cn(
        "初始猜测",
        "ATOMIC（原子密度叠加，最常用最稳）、RESTART（从文件读）、"
        "MOPAC、TRANSPORT 等。\n"
        "难收敛的体系可以先用 ATOMIC 跑一遍再换 RESTART。",
        ("初猜", "初始猜测")),
    "FORCE_EVAL/DFT/SCF/EPS_SCF": Cn(
        "SCF 收敛阈值",
        "能量/密度的收敛判据。日常 1E-6，做能量差、频率、NEB 建议 1E-7。\n"
        "太松会让力有噪声，几何优化和分子动力学都会抖。",
        ("收敛阈值", "scf 收敛")),
    "FORCE_EVAL/DFT/SCF/MAX_SCF": Cn(
        "最大 SCF 步数",
        "默认 50。到上限还没收敛 CP2K 会报错退出（除非设了 "
        "IGNORE_CONVERGENCE_FAILURE）。不收敛通常是体系有问题，不是步数不够。",
        ("scf 步数",)),
    "FORCE_EVAL/DFT/SCF/ADDED_MOS": Cn(
        "附加空轨道",
        "多算几个空轨道。金属/开壳层、要算 PDOS 或能带、要开 smearing 时必须有；"
        "写 -1 表示加满所有可用轨道。\n"
        "OT 计算里也写在 &SCF 下（不是 &OT 里）。",
        ("空轨道",)),
    "FORCE_EVAL/DFT/SCF/LEVEL_SHIFT": Cn(
        "能级平移",
        "给虚轨道加一个正的平移量，压制金属性体系的自旋/电荷振荡，帮助收敛。"
        "0.05–0.5 Hartree 常用。收敛后要关掉，否则影响结果。",
        ("能级平移", "level shift")),
    "FORCE_EVAL/DFT/SCF/CHOLESKY": Cn(
        "Cholesky 分解",
        "用 Cholesky 分解代替传统对角化，大体系更省时间。"),
    "FORCE_EVAL/DFT/SCF/OT/MINIMIZER": Cn(
        "OT 最小化器",
        "SD / CG / DIIS / BROYDEN。DIIS 通常最快，CG 更稳。",
        ("最小化器",)),
    "FORCE_EVAL/DFT/SCF/OT/PRECONDITIONER": Cn(
        "OT 预条件子",
        "FULL_SINGLE_INVERSE 是常用平衡点；FULL_ALL 内存吃紧时换它；"
        "大体系可用 FULL_KINETIC。",
        ("预条件",)),
    "FORCE_EVAL/DFT/SCF/OT/ENERGY_GAP": Cn(
        "OT 能隙估计",
        "告诉 OT 大概的 HOMO-LUMO 能隙，帮助构造预条件子。"
        "金属/小能隙体系填 0.01–0.05，绝缘体可以不填。",
        ("能隙", "gap")),
    "FORCE_EVAL/DFT/SCF/DIAGONALIZATION/ALGORITHM": Cn(
        "对角化算法",
        "STANDARD（默认，稳健）、DAVIDSON、FILTER_MATRIX（大体系省时）。"),
    "FORCE_EVAL/DFT/SCF/MIXING/METHOD": Cn(
        "密度混合方法",
        "BROYDEN_MIXING（默认，稳健）、DIRECT_P_MIXING、KERKER_MIXING（金属）、"
        "PULAY_MIXING。金属和难收敛体系换方法往往比调参数有效。",
        ("混合方法", "mixing")),
    "FORCE_EVAL/DFT/SCF/MIXING/ALPHA": Cn(
        "混合系数",
        "新旧密度的混合比例，默认 0.4。收敛困难就减小到 0.2 甚至 0.1（更稳但更慢）。",
        ("混合系数",)),
    "FORCE_EVAL/DFT/SCF/MIXING/NBUFFER": Cn(
        "混合历史长度",
        "Broyden/Pulay 保留多少步历史，默认 7–8。难收敛的金属可以到 20。",
        ("历史长度",)),
    "FORCE_EVAL/DFT/SCF/SMEAR/METHOD": Cn(
        "展宽方法",
        "FERMI_DIRAC（金属首选，物理意义明确：等价于电子温度）、"
        "ENERGY_WINDOW（按能量窗口占据，省时）、LIST（固定占据表）。\n"
        "**2026.2 新增三种**：GAUSSIAN（高斯展宽，按 SIGMA 控制，"
        "大多数情况都稳）、METHFESSEL_PAXTON（一阶 MP，收敛快但"
        "**别用于半导体/绝缘体**，会出现非物理占据）、"
        "MARZARI_VANDERBILT（冷展宽，低温下更准）。\n"
        "注意 2026.2 把默认值从 ENERGY_WINDOW 改成了 GAUSSIAN；"
        "本工具始终显式写 FERMI_DIRAC，所以不受影响。\n"
        "用展宽必须走 DIAGONALIZATION，不能配 OT。",
        ("展宽", "smearing", "smear", "费米")),
    "FORCE_EVAL/DFT/SCF/SMEAR/ELECTRONIC_TEMPERATURE": Cn(
        "电子温度",
        "展宽对应的温度（K）。金属 300 K 附近常用；"
        "想接近 0 K 的基态用 100–200 K，但太低会难收敛。\n"
        "注意它影响能量，外推时要说明。",
        ("电子温度",)),
    "FORCE_EVAL/DFT/SCF/OUTER_SCF/MAX_SCF": Cn(
        "外层 SCF 步数",
        "OT 计算里外层循环负责更新预条件子和旋转矩阵，通常 20–50 步。"),

    "FORCE_EVAL/DFT/XC/XC_FUNCTIONAL": Cn(
        "交换关联泛函",
        "写法是 &XC_FUNCTIONAL <泛函名>（如 PBE、BLYP、PBE0）。\n"
        "PBE 是通用默认；PBEsol 晶格常数更准；PBE0/HSE06 带隙更准但贵得多。"
        "要组合两个泛函就再开一个 &XC_FUNCTIONAL 段或用简写名。",
        ("泛函", "交换关联")),
    "FORCE_EVAL/DFT/XC/VDW_POTENTIAL/POTENTIAL_TYPE": Cn(
        "色散校正类型",
        "PAIR_POTENTIAL（D3/D2 这类成对势，最常用）、NON_LOCAL（vdW-DF 系列）、"
        "NONE（不加）。\n"
        "分子晶体、吸附、层状材料必须加，否则结合能被严重低估。",
        ("色散", "vdw")),
    "FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL/TYPE": Cn(
        "成对势种类",
        "DFTD3 / DFTD3(BJ) / DFTD2 / DFTD4。推荐 DFTD3(BJ)（带 Becke-Johnson 阻尼）。\n"
        "要和老文献对照时注意他们用的是 D3 还是 D3(BJ)，结果会有差异。",
        ("d3", "d3bj")),
    "FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL/REFERENCE_FUNCTIONAL": Cn(
        "参考泛函",
        "D3 参数是针对某个泛函拟合的（PBE、BLYP…），必须和 &XC_FUNCTIONAL 一致，"
        "否则等于用了错的色散参数。",
        ("参考泛函",)),

    "FORCE_EVAL/DFT/KPOINTS/SCHEME": Cn(
        "k 点取样方案",
        "MONKHORST-PACK n1 n2 n3（最常见）、GAMMA（只用 Γ 点，大晶胞够用）、"
        "MACDONALD、GENERAL。\n"
        "金属需要密网格（8×8×8 起）并配展宽；半导体 4×4×4 常够；"
        "分子/大晶胞用 GAMMA。",
        ("k点", "k 点", "倒空间取样")),
    "FORCE_EVAL/DFT/KPOINTS/SYMMETRY": Cn(
        "k 点对称约化",
        "打开后利用晶体对称性减少不可约 k 点数目，省时间。默认开。"),
    "FORCE_EVAL/DFT/KPOINTS/WAVEFUNCTIONS": Cn(
        "波函数类型",
        "REAL（实数，省一半内存和时间，但要求体系有反演对称性）、COMPLEX（默认）。"),
    "FORCE_EVAL/DFT/KPOINTS/UNITS": Cn("k 点单位", "B_VECTOR（倒格矢分数坐标）或 CART_ANGSTROM。"),
    "FORCE_EVAL/DFT/KPOINTS/FULL_GRID": Cn(
        "全网格",
        "用完整 k 点网格而不是不可约部分，调试时用来排除对称性问题。"),

    "FORCE_EVAL/DFT/POISSON/PERIODIC": Cn(
        "泊松方程周期性",
        "必须和 &CELL 里的 PERIODIC 一致。\n"
        "孤立分子用 NONE（配合 MT 或 WAVELET 求解器）；"
        "设了 CELL PERIODIC NONE 却忘了这里，CP2K 会直接报错。",
        ("泊松", "poisson")),
    "FORCE_EVAL/DFT/POISSON/POISSON_SOLVER": Cn(
        "泊松求解器",
        "PERIODIC（默认，三维周期）、MT（Martyna-Tuckerman，孤立分子/团簇，"
        "要求密度被限制在盒子内）、WAVELET（大盒子非周期体系，比 MT 便宜）、"
        "ANALYTIC、MULTIPOLE、IMPLICIT。\n"
        "旧写法是 PSOLVER。",
        ("求解器",)),

    "FORCE_EVAL/DFT/PRINT/DOS": Cn(
        "态密度输出",
        "DOS/PDOS 的总开关（2026.2 起 PDOS 和 LDOS 都挂在这个段下面；"
        "2025.1 里它们直接挂在 &PRINT 下）。\n"
        "常用关键字：NLUMO（多算几个空轨道，看导带必须给够）、"
        "DELTA_E（能量网格间距，默认 0.001）。\n"
        "要画平滑曲线就在 &CURVE/&BROADEN 里设展宽。",
        ("态密度", "dos", "pdos")),
    "FORCE_EVAL/DFT/PRINT/DOS/NLUMO": Cn(
        "PDOS 空轨道数",
        "要画导带就得给够空轨道，一般 20–50。给太少导带是空的。\n"
        "（2026.2 起它在 &DOS 下；2025.1 在 &PDOS 下。）",
        ("态密度", "pdos", "空轨道")),
    "FORCE_EVAL/DFT/PRINT/DOS/LDOS/LIST": Cn(
        "投影原子列表",
        "对哪些原子做投影态密度，写原子序号。做吸附时很有用："
        "只看吸附分子和它下面的金属原子。\n"
        "配合 COMPONENTS T 可以进一步分 s/p/d 轨道。",
        ("投影态密度", "ldos")),
    "FORCE_EVAL/DFT/PRINT/DOS/PDOS/COMPONENTS": Cn(
        "按角动量拆分",
        "把 PDOS 再按 s/p/d/f 分量拆开，做化学键分析时很方便。",
        ("投影态密度",)),
    "FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN": Cn(
        "态密度展宽",
        "把离散的能级展宽成平滑的 DOS 曲线（2026.2 新增）。\n"
        "TYPE 选 GAUSSIAN / LORENTZIAN / PSEUDO_VOIGT；"
        "WIDTH 是半高全宽（eV），经验值 0.05–0.2：太小曲线毛糙，"
        "太大会把相邻峰糊在一起。\n"
        "**注意**：展宽只影响画图观感，不改变任何计算结果。",
        ("展宽", "dos", "平滑")),
    "FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN/WIDTH": Cn(
        "展宽宽度",
        "半高全宽 FWHM（eV），默认 0.1。\n"
        "0.05 左右适合分辨精细结构，0.2 左右适合看整体趋势。",
        ("展宽宽度", "fwhm")),
    "FORCE_EVAL/DFT/PRINT/DOS/DELTA_E": Cn(
        "能量网格间距",
        "DOS 输出的能量点间隔，默认 0.001（Ry）。调大可以减小输出文件体积。",
        ("能量网格",)),
    "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/FILE_NAME": Cn(
        "能带文件名",
        "能带数据写到哪个文件（CP2K 的 .bs 格式，可以用 "
        "cp2k_bs_to_csv 之类的小工具转成 CSV 再画）。",
        ("能带",)),
    "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET/SPECIAL_POINT": Cn(
        "高对称点",
        "写法 SPECIAL_POINT <名字> x y z，坐标是倒格矢分数坐标。\n"
        "同一个 KPOINT_SET 里按顺序给出路径上的点，NPOINTS 是这段路径上的总点数；"
        "换了不连续的一段就再开一个 KPOINT_SET。",
        ("高对称点", "k路径", "k 路径")),
    "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET/NPOINTS": Cn(
        "路径点数",
        "这段 k 路径上采多少点。看形状 40–60，精确找带隙位置 100–200。"),
    "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET/UNITS": Cn(
        "k 点坐标单位",
        "B_VECTOR 表示倒格矢分数坐标（最常用），CART_ANGSTROM 是 1/Å。"),
    "FORCE_EVAL/DFT/PRINT/MOMENTS": Cn(
        "偶极矩输出",
        "打印偶极矩。只对非周期体系有意义；分子要配真空层，"
        "否则相邻镜像的偶极会互相干扰。",
        ("偶极",)),
    "FORCE_EVAL/DFT/PRINT/MULLIKEN": Cn(
        "Mulliken 布居",
        "快速看电荷转移和自旋分布。基组依赖性强，适合定性，不适合定量结论。",
        ("布居分析", "电荷分析")),

    # ---------------- SUBSYS ----------------
    "FORCE_EVAL/SUBSYS/CELL/ABC": Cn(
        "正交晶胞边长",
        "写法 ABC a b c [angstrom]，只适用于正交（90°）晶胞，"
        "有角度时要用 A/B/C 三个矢量。\n"
        "默认单位是 Å，也可以写 [bohr]。\n"
        "做孤立分子时这里就是那个「盒子」：把分子包在一层真空里，"
        "边长取分子尺寸加两倍真空层（经验 10–15 Å）。",
        ("晶胞", "晶格常数", "真空层", "盒子")),
    "FORCE_EVAL/SUBSYS/CELL/PERIODIC": Cn(
        "周期性方向",
        "XYZ（三维周期，体相）、XY/XZ/YZ（板层）、NONE（孤立分子）。\n"
        "必须和 DFT/POISSON 的设置一致，否则报错或结果无意义。\n"
        "孤立分子把它设成 NONE，配合真空层（盒子边长 = 分子尺寸 + 2×真空层）；"
        "只设 NONE 而不改 &POISSON，CP2K 会直接报错。",
        ("周期性", "真空层", "孤立分子")),
    "FORCE_EVAL/SUBSYS/CELL/CELL_REF": Cn(
        "参考晶胞",
        "给正交化/重整用的参考晶胞，做形变或约束优化时用。"),
    "FORCE_EVAL/SUBSYS/CELL/SYMMETRY": Cn(
        "晶胞对称性",
        "晶胞的对称性判定精度/开关，影响 k 点约化和优化时的对称约束。"),
    "FORCE_EVAL/SUBSYS/COORD/SCALED": Cn(
        "分数坐标",
        "写在 &COORD 里的 SCALED 表示下面的坐标是 0–1 的分数坐标，"
        "会按 &CELL 换算。CIF 出来的坐标常需要它。",
        ("分数坐标",)),
    "FORCE_EVAL/SUBSYS/KIND/ELEMENT": Cn(
        "元素",
        "这个 KIND 对应哪种元素。写在 &KIND 段里，用于校验基组和赝势是否配套。",
        ("元素",)),
    "FORCE_EVAL/SUBSYS/KIND/BASIS_SET": Cn(
        "基组",
        "这个元素用哪个基组，如 DZVP-MOLOPT-SR-GTH。\n"
        "名字末尾的 -qN 是价电子数，**必须和 POTENTIAL 的 -qN 相等**，"
        "这是 CP2K 最常见的崩溃原因。\n"
        "也可以写成 BASIS_SET ORB <名字>。",
        ("基组",)),
    "FORCE_EVAL/SUBSYS/KIND/POTENTIAL": Cn(
        "赝势",
        "这个元素的赝势名，如 GTH-PBE-q6。括号里的 q 必须和基组的 q 一致。\n"
        "全电子计算写 ALLELECTRON，此时不需要赝势。",
        ("赝势", "pseudopotential")),
    "FORCE_EVAL/SUBSYS/KIND/MAGNETIZATION": Cn(
        "初始磁矩",
        "给这个元素一个初始自旋，帮助自旋极化的 SCF 找到想要的那支解。"
        "单位是玻尔磁子，如 2.0；写 0 表示非磁性初猜。",
        ("磁矩", "自旋初猜")),
    "FORCE_EVAL/SUBSYS/KIND/GHOST": Cn(
        "幽灵原子",
        "把该原子变成只提供基函数、不带核和电子的幽灵原子，"
        "用于计算基组重叠误差（BSSE）。",
        ("幽灵原子", "bsse")),
    "FORCE_EVAL/SUBSYS/TOPOLOGY/COORD_FILE_NAME": Cn(
        "坐标文件",
        "从外部文件读坐标（xyz、pdb 等）。注意不要同时再写 &COORD 段。",
        ("坐标文件",)),

    # ---------------- MOTION ----------------
    "MOTION/GEO_OPT/OPTIMIZER": Cn(
        "几何优化算法",
        "BFGS（默认，中小体系最省步数）、LBFGS（大体系省内存）、"
        "CG（初期快，近极小点慢）。\n"
        "不收敛时先查初始结构、SCF 收敛和 cutoff，别急着换算法。",
        ("优化算法", "优化器")),
    "MOTION/GEO_OPT/MAX_ITER": Cn(
        "最大优化步数",
        "每一步都是一次完整的 SCF。200 对分子够用，表面板可以 500。"),
    "MOTION/GEO_OPT/MAX_FORCE": Cn(
        "最大力收敛判据",
        "默认 4.5E-4 a.u.（约 0.023 eV/Å）。要更精确的几何可到 1E-4。",
        ("力的判据", "收敛判据")),
    "MOTION/GEO_OPT/RMS_FORCE": Cn(
        "均方根力判据",
        "默认 3.0E-4 a.u.。和 MAX_FORCE 一起构成收敛条件。"),
    "MOTION/CELL_OPT/EXTERNAL_PRESSURE": Cn(
        "外压",
        "单位 bar（不是 GPa！1 GPa = 10000 bar）。0 表示优化到零压，"
        "高压相研究可以填实际压力。",
        ("外压", "压力")),
    "MOTION/CELL_OPT/KEEP_ANGLES": Cn(
        "固定晶胞角度",
        "只优化 a/b/c 和原子位置，角度不变。单轴压缩、已知晶系时用。"),
    "MOTION/CELL_OPT/KEEP_SYMMETRY": Cn(
        "保持对称性",
        "优化过程中保持空间群，能大幅减少自由度。默认开。"),
    "MOTION/MD/ENSEMBLE": Cn(
        "系综",
        "NVE（能量守恒，用来查步长）、NVT（控温，最常用）、"
        "NPT_I（控温控压，各向同性）、NPT_F（形状也变）、"
        "LANGEVIN、MSST（冲击）、NPE_I/NPE_F 等。\n"
        "先用 NVE 看总能量漂移是否可接受，再换 NVT 取数据。",
        ("系综", "ensemble", "分子动力学", "控温控压")),
    "MOTION/MD/STEPS": Cn(
        "步数",
        "总时间 = 步长 × 步数。0.5 fs × 20000 步 = 10 ps。\n"
        "做统计平均至少要几十 ps，前 10–20% 当平衡段丢掉。",
        ("步数",)),
    "MOTION/MD/TIMESTEP": Cn(
        "时间步长",
        "单位 fs。含氢体系不要超过 0.5 fs（否则 O-H 键会飞）；"
        "只含重元素可以 1–2 fs。判断标准是 NVE 下的能量漂移。",
        ("步长", "时间步")),
    "MOTION/MD/TEMPERATURE": Cn(
        "目标温度",
        "单位 K。室温 300，生物体系 310。多温度跑可以拟合活化能。",
        ("温度",)),
    "MOTION/MD/TEMP_TOL": Cn(
        "温度容差（已过时）",
        "瞬时温度偏离目标超过它就把速度整体重标定一次，默认 0 = 不做。"
        "CP2K 手册明确标注这个关键字**正在淘汰**，"
        "推荐改用**时间常数小的 CSVR 恒温器**；有恒温器时不要再叠加这一套。"),
    "MOTION/MD/COMVEL_TOL": Cn(
        "质心速度容差",
        "每隔多少步把质心速度归零（单位 a.u.），避免整个体系整体漂移。"),
    "MOTION/MD/ANGVEL_ZERO": Cn(
        "角速度归零",
        "每隔多少步清零整体角速度，防止分子整体旋转带走动能。"),
    "MOTION/MD/THERMOSTAT/TYPE": Cn(
        "恒温器类型",
        "NOSE（Nose-Hoover，正则系综严格，小体系温度会振荡）、"
        "CSVR（随机速度重标定，温度稳、平衡快，取样常用）、"
        "GLE（广义 Langevin，可加量子核效应，需要自己给 S/A 矩阵）、"
        "AD_LANGEVIN。\n"
        "拿不准用 CSVR。",
        ("恒温器", "控温")),
    "MOTION/MD/THERMOSTAT/REGION": Cn(
        "控温区域",
        "GLOBAL（整个盒子）、MOLECULE、MASSIVE、DEFINED（用 &DEFINE_REGION 指定原子）、"
        "NONE。做非平衡模拟（比如热传导）时用它只控一部分原子。",
        ("控温区域",)),
    "MOTION/MD/THERMOSTAT/NOSE/TIMECON": Cn(
        "Nose 时间常数",
        "单位 fs，默认 100。越大耦合越松、温度振荡越慢；"
        "小体系（几十个原子）建议加大到几百，否则温度会剧烈振荡。",
        ("时间常数",)),
    "MOTION/MD/BAROSTAT/PRESSURE": Cn(
        "目标压力",
        "单位 bar。1 bar ≈ 常压。只在会改变晶胞的系综里生效。",
        ("压力", "barostat")),
    "MOTION/MD/BAROSTAT/TIMECON": Cn(
        "压力耦合时间常数",
        "单位 fs，默认 500。太小会让晶胞剧烈抖动。"),
    "MOTION/MD/LANGEVIN/GAMMA": Cn(
        "Langevin 摩擦系数",
        "单位 1/fs。**CP2K 的默认值是 0**（既没有摩擦也没有随机力，等于 NVE，温度不受控）。"
        "0.001–0.01（=1–10 ps⁻¹）是常用范围：耦合时间 1/γ 要明显长于你要观察的过程、"
        "又要短于你想跟踪的温度变化；0.1 属于强耦合（CP2K 的回归测试用它快速平衡），"
        "扩散系数这类动力学量会被压小。算扩散/黏度就用更小的值（1e-4–1e-3）。",
        ("摩擦", "langevin", "控温")),
    "MOTION/MD/TEMPERATURE_ANNEALING": Cn(
        "升温/降温（目标温度）",
        "每步把 **Langevin 的目标温度**乘这个因子：<1 降温、>1 升温，"
        "N 步从 T1 到 T2 就是 (T2/T1)^(1/N)。**只在 LANGEVIN 系综下生效**"
        "（手册原文 works only for the Langevin ensemble），而且是几何斜率、不是线性。",
        ("升温", "降温", "退火", "annealing")),
    "MOTION/MD/ANNEALING": Cn(
        "升温/降温（缩放速度）",
        "每步把所有**速度**乘这个因子：温度大约按它的**平方**变化。"
        "它不是恒温器，是直接加减动能（经典模拟退火/淬火）；"
        "只能配没有粒子恒温器的系综（NVE/NPE_*），和 NVT/NPT 的恒温器同时开 CP2K 会直接中止。",
        ("退火", "淬火", "升温", "降温")),
    "MOTION/MD/ANNEALING_CELL": Cn(
        "晶胞速度退火",
        "同上，但乘的是晶胞速度。**只有 NPE_I / NPE_F 会用它**（integrator.F），"
        "别的系综写了也不生效。",
        ("退火", "晶胞")),
    "EXT_RESTART/RESTART_TEMPERATURE_ANNEALING": Cn(
        "接力时接着温度斜坡",
        "接力启动时用 restart 文件里存下的**目标温度**接着升/降温，"
        "而不是退回本次输入里的 TEMPERATURE 重新开始（多段接力必须打开它）。",
        ("接力", "升温", "降温")),

    "MOTION/BAND/BAND_TYPE": Cn(
        "NEB 变体",
        "CI-NEB（爬山弹性带，会精确收敛到鞍点，最常用）、"
        "IT-NEB（改进切线）、B-NEB（普通弹性带）、D-NEB（DIIS 加速）、"
        "SM（弦方法）、EB（能量带）。",
        ("过渡态", "neb", "鞍点")),
    "MOTION/BAND/NUMBER_OF_REPLICA": Cn(
        "镜像数",
        "路径上的像个数，通常 5–9。太少路径太粗，太多每步都贵。",
        ("镜像数",)),
    "MOTION/BAND/K_SPRING": Cn(
        "弹簧常数",
        "相邻镜像间的弹簧刚度，典型 0.01–0.1。太大路径僵硬、鞍点被压歪；"
        "太小镜像会聚堆。",
        ("弹簧",)),
    "MOTION/BAND/CI_NEB/NSTEPS_IT": Cn(
        "CI-NEB 迭代步数",
        "每个 NEB 步里爬山迭代几次，通常 1–5。"),
    "MOTION/BAND/REPLICA/COORD_FILE_NAME": Cn(
        "镜像坐标文件",
        "每个像一份完整坐标文件，顺序是 反应物 → … → 产物。\n"
        "每个像都要有晶胞，否则会被放进默认的 1 Å 立方体。",
        ("镜像",)),
    "MOTION/BAND/CONVERGENCE_CONTROL/MAX_FORCE": Cn(
        "NEB 最大力判据",
        "路径上所有像的最大受力，建议 1E-3 或更小。",
        ("neb 收敛",)),
    "MOTION/PRINT/TRAJECTORY/FORMAT": Cn(
        "轨迹格式",
        "XYZ（默认，通用）、DCD、XMOL 等。XYZ 可以直接用 VMD/OVITO 打开。",
        ("轨迹",)),
    "MOTION/PRINT/TRAJECTORY/EACH/MD": Cn(
        "轨迹输出间隔",
        "每多少步 MD 写一帧轨迹。写太密会让 I/O 成为瓶颈（尤其在大体系上）。",
        ("轨迹间隔",)),
    "MOTION/CONSTRAINT/FIXED_ATOMS/LIST": Cn(
        "固定原子列表",
        "固定哪些原子（写序号，支持 1..10 这种区间）。做表面吸附时固定底部几层。",
        ("固定原子", "约束")),
    "MOTION/CONSTRAINT/FIXED_ATOMS/COMPONENTS_TO_FIX": Cn(
        "固定方向",
        "XYZ / XY / Z 等，控制固定哪几个方向。做单轴拉伸时常用。",
        ("固定方向",)),

    # ---------------- 频率 ----------------
    "VIBRATIONAL_ANALYSIS/INTENSITIES": Cn(
        "红外强度",
        "计算偶极对位移的导数，从而给出红外强度。**只对非周期体系有意义**。\n"
        "代价是普通频率计算的数倍。CP2K 2025.1 没有 &RAMAN 段，"
        "拉曼强度要另行后处理。",
        ("红外", "频率", "振动")),
    "VIBRATIONAL_ANALYSIS/THERMOCHEMISTRY": Cn(
        "热化学量",
        "顺便给零点能、内能、熵、热容。谐振近似，用于孤立分子；"
        "低频扭转会低估熵。",
        ("热化学", "零点能")),
    "VIBRATIONAL_ANALYSIS/DX": Cn(
        "位移步长",
        "有限差分求 Hessian 时的原子位移量（Bohr），默认 0.01。"
        "太小会被数值噪声淹没，太大会引入非谐性。",
        ("位移步长",)),
    "VIBRATIONAL_ANALYSIS/NPROC_REP": Cn(
        "每个位移的并行组",
        "程序自己复制并行组来算不同位移。不确定就写 1。"),

    # ---------------- 打印 ----------------
    "FORCE_EVAL/PRINT/FORCES": Cn(
        "输出受力",
        "打印每个原子的受力。几何优化/频率/NEB 必需，单点能时也能用来判断结构是否合理。",
        ("受力", "力")),
    "FORCE_EVAL/PRINT/STRESS_TENSOR": Cn(
        "输出应力",
        "打印应力张量。做 CELL_OPT 需要它（或直接在 &FORCE_EVAL 里设 "
        "STRESS_TENSOR analytical）。",
        ("应力",)),
    "FORCE_EVAL/PRINT/DISTRIBUTION": Cn(
        "输出分布统计",
        "打印指定量的分布直方图，做统计平均时方便。"),
    "FORCE_EVAL/DFT/PRINT/MO": Cn(
        "分子轨道输出",
        "输出轨道能级和占据数；加 &MO_CUBES 可以导出 cube 文件给 VMD 画轨道。\n"
        "cube 文件很大，大体系慎用。",
        ("轨道", "分子轨道")),
    "FORCE_EVAL/DFT/PRINT/ELECTRIC_FIELD_GRADIENT": Cn(
        "电场梯度",
        "用于计算 NQR/EFG 相关性质。"),
    "FORCE_EVAL/DFT/SCF/OUTER_SCF/EPS_SCF": Cn(
        "外层 SCF 阈值",
        "外层循环（换预条件子/混合参数后再跑一轮）的收敛判据。\n"
        "一般可以比内层松一档（内层 1e-6、外层 1e-5），能省机时；"
        "但内层本身就很松时，外层再松没有意义。",
        ("外层阈值", "OUTER_SCF")),
    "FORCE_EVAL/DFT/SCF/IGNORE_CONVERGENCE_FAILURE": Cn(
        "SCF 不收敛也继续",
        "SCF 到了 MAX_SCF 还没收敛也照样往下算并打印能量。\n"
        "什么时候能用：只比较**差值**的时候（比如扫 CUTOFF，两档之间 SCF 收敛"
        "程度的差别基本抵消）。\n"
        "什么时候不能用：要绝对能量、结合能、力、应力、频率，或者做 MD/"
        "几何优化——不收敛的力会把结构推歪。\n"
        "和 MAX_SCF 一起用时，实际含义是「固定迭代步数」。",
        ("不收敛也继续", "算完再说", "固定步数")),
    "FORCE_EVAL/DFT/SCF/MIXING/BETA": Cn(
        "混合阻尼（Kerker）",
        "Broyden 混合里的 Kerker 阻尼因子，留空就不写（用 CP2K 默认）。\n"
        "金属体系常见做法是给一个 0.5–1.5 的值压住长波方向的电荷振荡；"
        "绝缘体通常不需要。",
        ("阻尼", "Kerker")),
}


# --------------------------------------------------------------------------- #
# glossary: shown next to the English description
# --------------------------------------------------------------------------- #
TERMS: dict[str, str] = {
    "ab initio": "第一性原理",
    "algorithm": "算法",
    "analytic": "解析的",
    "approximation": "近似",
    "atomic": "原子的",
    "band gap": "带隙",
    "band structure": "能带结构",
    "basis set": "基组",
    "basis functions": "基函数",
    "Brillouin zone": "布里渊区",
    "cell": "晶胞",
    "charge": "电荷",
    "cluster": "团簇",
    "coefficient": "系数",
    "configuration": "构型 / 组态",
    "convergence": "收敛",
    "correlation": "关联",
    "cutoff": "截断能",
    "damping": "阻尼",
    "default": "默认值",
    "density": "密度",
    "density matrix": "密度矩阵",
    "derivative": "导数",
    "diagonalization": "对角化",
    "dispersion": "色散",
    "eigenvalue": "本征值",
    "eigenvector": "本征矢",
    "electron": "电子",
    "energy": "能量",
    "ensemble": "系综",
    "exchange": "交换",
    "exponent": "指数（高斯函数的 α）",
    "external pressure": "外压",
    "force": "力",
    "functional": "泛函",
    "Gaussian": "高斯函数",
    "geometry": "几何结构",
    "grid": "网格",
    "Hessian": "Hessian 矩阵（二阶导数）",
    "k-point": "k 点",
    "kinetic energy": "动能",
    "lattice": "晶格",
    "linear scaling": "线性标度",
    "magnetization": "磁化 / 磁矩",
    "mixing": "密度混合",
    "molecular dynamics": "分子动力学",
    "multiplicity": "自旋多重度",
    "optimization": "优化",
    "orbital": "轨道",
    "occupation": "占据数",
    "periodic": "周期性",
    "plane wave": "平面波",
    "Poisson solver": "泊松求解器",
    "potential": "势 / 赝势",
    "pseudopotential": "赝势",
    "relativistic": "相对论效应",
    "restart": "重启文件",
    "smearing": "能级展宽",
    "spin": "自旋",
    "stress tensor": "应力张量",
    "thermostat": "恒温器",
    "trajectory": "轨迹",
    "valence": "价电子",
    "wave function": "波函数",
    "barostat": "恒压器",
    "band": "能带",
    "bead": "珠子（路径积分）",
    "bulk": "体相",
    "charge density": "电荷密度",
    "core": "芯区（非价电子）",
    "dipole moment": "偶极矩",
    "dos": "态密度",
    "error": "错误",
    "excited state": "激发态",
    "fractional": "分数的",
    "geometry optimization": "几何优化",
    "HF exchange": "Hartree-Fock 交换",
    "imaginary frequency": "虚频",
    "interpolation": "插值",
    "k-path": "k 点路径",
    "kinetic": "动能的",
    "local": "局域的",
    "nudged elastic band": "微动弹性带（NEB）",
    "occupation numbers": "占据数",
    "overlap": "重叠",
    "polarizability": "极化率",
    "quadrupole": "四极矩",
    "reciprocal": "倒空间的",
    "slab": "板层模型",
    "solvent": "溶剂",
    "surface": "表面",
    "symmetry": "对称性",
    "transition state": "过渡态",
    "unit cell": "晶胞（单胞）",
    "vacuum": "真空层",
    "van der Waals": "范德华",
    "vibrational analysis": "振动分析",
    "wavefunction": "波函数",
}


# --------------------------------------------------------------------------- #
# lookup helpers
# --------------------------------------------------------------------------- #
def _bare_index() -> dict[str, list[str]]:
    """Map a bare keyword name to the fully qualified keys that define it."""
    index: dict[str, list[str]] = {}
    for key in KEYWORDS:
        bare = key.split("/")[-1]
        index.setdefault(bare, []).append(key)
    return index


_BARE = _bare_index()
_TERM_KEYS = sorted(TERMS, key=len, reverse=True)


def lookup(section: str | None, keyword: str | None) -> Cn | None:
    """Find the Chinese note for a section/keyword pair."""
    if keyword:
        if section:
            exact = KEYWORDS.get(f"{section.strip('/')}/{keyword.upper()}")
            if exact is not None:
                return exact
        candidates = _BARE.get(keyword.upper(), [])
        if len(candidates) == 1:
            return KEYWORDS[candidates[0]]
        if candidates and section:
            tail = section.strip("/")
            for key in candidates:
                if key.startswith(tail):
                    return KEYWORDS[key]
    if section and not keyword:
        # section level note, e.g. the &XC_FUNCTIONAL block itself
        return KEYWORDS.get(section.strip("/"))
    return None


def terms_in(text: str, limit: int = 12) -> list[tuple[str, str]]:
    """Which glossary terms occur in ``text``, longest match first."""
    if not text:
        return []
    lowered = text.lower()
    found: list[tuple[str, str]] = []
    for term in _TERM_KEYS:
        if term.lower() in lowered:
            found.append((term, TERMS[term]))
            if len(found) >= limit:
                break
    return found


def search(query: str, limit: int = 40) -> list[tuple[str, Cn]]:
    """Chinese search over the curated notes; returns (qualified key, entry).

    Ranking puts the entry the user most likely meant first: an exact name match,
    then a query listed as one of the entry's aliases, then a name that merely
    contains the query, and only then a hit inside the explanation.
    """
    needle = query.strip()
    if not needle:
        return []

    candidates: list[tuple[str, Cn]] = []
    for key, entry in KEYWORDS.items():
        haystack = " ".join((entry.name, *entry.extra, entry.text))
        if needle in haystack:
            candidates.append((key, entry))

    def rank(item: tuple[str, Cn]) -> tuple[int, int, str]:
        entry = item[1]
        if entry.name == needle:
            return (0, len(entry.name), entry.name)
        if needle in entry.extra:
            return (1, len(entry.name), entry.name)
        if needle in entry.name:
            return (2, len(entry.name), entry.name)
        return (3, len(entry.name), entry.name)

    candidates.sort(key=rank)
    return candidates[:limit]


def is_chinese(query: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", query))


def split_key(key: str) -> tuple[str, str]:
    """``"FORCE_EVAL/DFT/MGRID/CUTOFF"`` -> ``("FORCE_EVAL/DFT/MGRID", "CUTOFF")``."""
    section, _, keyword = key.rpartition("/")
    return section, keyword
