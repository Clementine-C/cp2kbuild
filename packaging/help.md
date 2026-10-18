# cp2kbuild 界面里的「?」帮助文本

鼠标悬浮在界面上任意「?」上就会显示下面这些内容；带手册链接的「?」还可以点击直接打开对应的手册页。

共 208 条。

## 结构

*标识*：`structure`

支持 xyz / pdb / POSCAR(CONTCAR) / cif / 已有的 CP2K `.inp` / **上一次运行写出的 `<项目名>-1.restart`**。也可以把文件直接拖进窗口。
扩展名认不出来时会看内容：文件里有 `&COORD` 就按 CP2K 输入读，有 `data_`/`_atom_site_` 按 CIF 读，以此类推。
**为什么能读 restart 文件**：CP2K 写出来的 restart 本身就是一份完整输入，里面有那个运行最后的 `&COORD` 和 `&CELL`。所以做**任务接力**时不必再准备一份结构文件——填上接力文件、点「读它的坐标」，体系（原子 + 晶胞）就进来了，生成的输入里会写上 `&COORD`/`&CELL`，**本次要算的就是它**。
坐标既然已经进来了，「取哪些量」里就不必再取 `RESTART_POS`/`RESTART_CELL`（取了只是把同一份坐标再覆盖一遍）；第 6 节只在还要**速度**、**步数计数器**或**恒温器/压浴状态**时才需要填。
读到晶胞就用晶胞；孤立分子请配合下面的「真空层」。
CIF 里的对称操作会被展开成原子。
CIF 的对称性展开只做几何展开，不会做空间群约束下的对称化，所以优化前先看一眼原子数对不对。
注意：restart 文件里是**那次运行的构型快照**（可能已经跑了几万步），拿来做「从头算」的起点时，先看清结构面板上显示的原子数和晶胞是不是你要的。

## import_params

*标识*：`import_params`

**把文件里的参数读回表单**（旁边的「读参数」）。
CP2K 的输入文件——以及 `-1.restart`，因为 CP2K 把它写成一份完整输入——不只记着坐标，还记着**这次是怎么算的**：`RUN_TYPE`（任务）、`METHOD`（Quickstep / FIST）、SCF 路线与阈值、泛函与色散、MD 系综/温度/步数、打印与重启设置、FIST 的力场文件……点一下就把这些填进对应控件，**表单和文件就对齐了**，不会再出现「文件里是 NPT，界面还停在 NVT」这种事。
读完之后会列出读了哪些参数，**点参数名直接跳到那个控件**（会高亮一下）；对不上的（界面里没有对应控件、或者取值不在下拉框里）会单独列出来，不会悄悄丢掉。
什么时候用：
• 拿到别人（或自己上次）写的输入，想在它的基础上改点东西；
• 接力时用 `-1.restart` 当结构文件，顺便把上次的运行参数也带过来；
• 校验一个文件前，先看清它到底设了什么。
**只读参数，不动结构**——结构始终来自上面那个文件。

## scf_guess

*标识*：`scf_guess`

SCF 初始波函数从哪里来。
• **RESTART（默认）**：读上次算完留下的 `<项目名>-RESTART.wfn`，接着上次的波函数算，通常能省掉一半以上的 SCF 步数。
• **ATOMIC**：从孤立原子密度拼一个初猜。第一次算、或者换了泛函 / 基组 / 体系之后用这个。
**不用担心「第一次算时没有 restart 文件」**：CP2K 自己会检查文件，没有就打一条 warning 然后回退成 ATOMIC 初猜（源码 `qs_initial_guess.F`）；连「文件在但原子数变了」也会回退，所以默认 RESTART 是安全的。
真正要小心的是**同名同原子数但换了元素/基组**的陈旧 .wfn，CP2K 不会检查这些，读进去就是一个错误的初猜（不会崩，只会多算几步或者收敛到别的解）。换体系时要么换项目名，要么把 .wfn 删掉。
k 点计算（DIAGONALIZATION + &KPOINTS）读的是 `.kp` 文件，不是 `.wfn`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.SCF_GUESS>

## wfn_print

*标识*：`wfn_print`

**波函数文件**：算完之后写不写 `<项目名>-RESTART.wfn`（k 点是 `.kp`）。
**默认打开**：这就是下一次 `SCF_GUESS RESTART` 要读的文件，不写的话下次只能从头猜。
CP2K 自己本来也会写（&PRINT/&RESTART 的默认等级是 LOW，MEDIUM 打印等级下就打开了），这里显式写出来有两个好处：PRINT_LEVEL 调成 SILENT 时仍然会写；文件里能一眼看到它会写。
注意 `.wfn` 和坐标文件一起写，大体系可能上百 MB。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/PRINT/RESTART.html>

## wfn_every

*标识*：`wfn_every`

`&SCF/&PRINT/&RESTART/&EACH`：**跑的过程中**每多少步写一次波函数文件。
「按哪一级计数」由右边的下拉框决定（默认跟着任务走）：
• MD 任务数 **MD 步**、几何优化数 **GEO_OPT 步**、晶胞优化数 **CELL_OPT 步**——要「长跑中途定期落盘」就用这三个；
• 单点能这类任务数 **QS_SCF 迭代**。注意它数的是**同一次力计算内部**的 SCF 迭代，所以在 MD 里写「每 500 个 SCF 步」几乎永远不会触发（除非某一步的 SCF 真的要迭代 500 次）——这正是以前容易踩的坑。
本工具默认 **0**：过程中不写，只在结束时写一次（靠右边「最后一步也写」）。中间那些没人看还占磁盘（一个 `.wfn` 可能上百 MB）。
它和「最后一步也写」是**两个独立设置**：`0` + 最后一步 = 结束时写一次；`0` + 不写最后一步 = 永远不写。
「备份」是 BACKUP_COPIES：保留几份旧文件（默认 1 份，文件名叫 `.wfn.bak-1`），文件被写坏时还能退回去。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/PRINT/RESTART.html>

## wfn_level

*标识*：`wfn_level`

`&EACH` 按哪一级计数——同一个「每 N 步」在不同层级下含义完全不同：
• **自动（默认）**：按任务选。MD → `MD`，几何优化 → `GEO_OPT`，晶胞优化 → `CELL_OPT`，单点 / 频率 / 能带 / PDOS → `QS_SCF`；
• **按 SCF 步（QS_SCF）**：`QS_SCF` 是**一次力计算内部**的 SCF 迭代序号。在单点能里「500」是第 500 次迭代；在 MD 里则是「某一步的 SCF 迭代到 500」——基本不会发生，等于只有「最后一步也写」在起作用；
• **按 MD / GEO_OPT / CELL_OPT 步**：按运行的步数计数，长跑定期落盘就用这三个。
CP2K 的规则是**出现的每个层级都要同时满足**（没写的层级默认 1，等于不限制），所以只写一个就够。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH.html>

## motion_restart

*标识*：`motion_restart`

**重启文件**：写不写 `<项目名>-1.restart`——整个运行的续算文件，里面是坐标、速度、步数计数器和重复一遍的输入。
**默认打开**，因为 `&EXT_RESTART`（第 6 节的「从上次续算」）读的就是它；MD 跑一半被杀掉时就靠它接着跑。
它和 `.wfn`（上面那个「波函数文件」）不是一回事：`.wfn` 只是波函数（给 SCF 当初猜），`.restart` 是整个运行的状态（给 &EXT_RESTART 续轨迹）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/PRINT/RESTART.html>

## restart_default

*标识*：`restart_default`

`&EXT_RESTART RESTART_DEFAULT`：续算文件里记着**所有**输入项，打开时以文件里的值为准（位置、速度、步数、温度、各种开关），当前这块输入里写的东西会被覆盖。
这正是「原样接着跑」需要的，所以默认 T。
关掉之后只取你在 `&EXT_RESTART` 里显式打开的那些项（例如只要 `RESTART_VEL T` 接速度），其余用本文件写的值——换泛函、改温度、改步数接着跑时用这个。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/EXT_RESTART.html#CP2K_INPUT.EXT_RESTART.RESTART_DEFAULT>

## ext_restart

*标识*：`ext_restart`

要接着跑哪个运行就填哪个文件：上一轮写出来的 `<项目名>-1.restart`。
只对带迭代步的计算有意义（MD、GEO_OPT、CELL_OPT）；单点能量没有东西可续，填了也只是白读一遍。
留空 = 从头算。
这是「运行的续算」，跟上面 SCF 波函数的 RESTART 初猜是两件事，两个可以同时用，也可以只用其中一个。
填进去以后下面那行提示会念出这个文件里有什么（坐标、速度、晶胞、步数计数器…）；**换任务**（GEO_OPT → MD(NVT)、MD(NVT) → MD(NPT)）时不要用默认的「全部」，改下面的「取哪些量」。
**restart 文件里本来就有坐标和晶胞**，所以接力不必再从别的结构文件开始：文件填好后右边会出现「读它的坐标」，点一下就把这次的体系读进来（输入里会写上 `&COORD`/`&CELL`，**这就是本次要算的体系**；只有还要速度/计数器/控温控压状态时才需要这个 `&EXT_RESTART`）。
注意：光填这个文件**不会**让预览出来——输入自己仍然要定义体系。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/EXT_RESTART.html#CP2K_INPUT.EXT_RESTART.RESTART_FILE_NAME>

## restart_every

*标识*：`restart_every`

`&MOTION/&PRINT/&RESTART/&EACH`：**跑的过程中**每多少个迭代步写一次 `<项目名>-1.restart`。
本工具默认 **20**（和 CP2K 自己的默认一致），MD 步或 GEO_OPT/CELL_OPT 步。
调大 = I/O 少、掉机器时丢得多；调小 = 安全但 I/O 多；0 = 过程中不写（配合右边「最后一步也写」就只有结束时一份）。
它和「最后一步也写」（`ADD_LAST`）是两个独立设置。
「备份」是 BACKUP_COPIES，保留几份旧的（默认 1 份）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/PRINT/RESTART.html#CP2K_INPUT.MOTION.PRINT.RESTART.BACKUP_COPIES>

## add_last

*标识*：`add_last`

`ADD_LAST`：**迭代的最后一步**要不要也写一次，取值 `NO` / `NUMERIC` / `SYMBOLIC`（本工具只用前两个；`SYMBOLIC` 是把文件名里的步数换成字母 `l`）。
CP2K 自己的默认就是 `NUMERIC`，所以工具默认勾上。
它和左边的「每 N 步」是**两个独立设置**，组合起来是四种行为：
• 每 20 步 + 最后一步 → CP2K 的传统行为（掉机器最多丢 20 步）；
• 每 0 步 + 最后一步 → 只在结束时写一份（波函数重启文件的推荐用法：间隔 0 + `ADD_LAST NUMERIC`，层级按任务走）；
• 每 20 步 + 不写最后一步 → 只有那些中间文件；
• 每 0 步 + 不写最后一步 → 什么都不会写（那就等于白开这个开关）。
`&EXT_RESTART` 读的是 `-1.restart`，所以长跑时别把这一行关掉，否则中断后没有可续的文件。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/PRINT/RESTART.html#CP2K_INPUT.MOTION.PRINT.RESTART.ADD_LAST>

## restart_take

*标识*：`restart_take`

**从上一个任务留下的 restart 文件里只取你要的那几样。**
写法：选一项就写 `RESTART_DEFAULT F`（先把全部 38 个 `RESTART_*` 开关关掉）再加上点名的那几个 `T`——没写出来的都跟着 `RESTART_DEFAULT`。

**先说清楚现在的分工**（这决定了哪些选项值得选）：
把 restart 当**结构文件**载入时，坐标和晶胞已经随它继承进本次输入（写成 `&COORD`/`&CELL`，CP2K 自己写 restart 时也是这么干的）。所以 `&EXT_RESTART` 唯一还能补的是**内联输入给不了的三样**：
• **速度**（`RESTART_VEL`）——restart 里有一段 `&VELOCITY`，输入不会内联它；
• **步数计数器**（`RESTART_COUNTERS`）——想让新任务从旧步数接着编号；
• **恒温器/压浴状态**（`RESTART_THERMOSTAT` / `RESTART_BAROSTAT`）——接着控温控压，压浴才有历史。
其它一切（`AVERAGES`、`RANDOMG`、`METADYNAMICS`、shell/core 位置速度…）都只有在非常明确的场合才需要，界面上没做预设。

**每个选项的用场：**
| 选项 | 取什么 | 什么时候用 |
|---|---|---|
| 只取速度 | `RESTART_VEL T` | **MD → MD 接力**（最常用）：坐标已经继承了，只把速度接上；步数从 0 起、恒温器重新起 |
| 速度 + 热浴/压浴 | `+ THERMOSTAT/BAROSTAT T` | 断点续跑一段 NVT/NPT，希望控温控压状态也连续；坐标系综不变时用它 |
| 只取步数计数器 | `RESTART_COUNTERS T` | 只想让轨迹编号接着数（比如接着写同一个 trajectory），**不要**它在普通接力里 |
| 全部（CP2K 默认） | `RESTART_DEFAULT T` | 同一套参数、同一个任务、「像没中断过一样」接着跑；注意它**连计数器一起搬**，换任务会出怪现象 |
| 只取坐标 | `RESTART_POS T` | 一般**用不上**：坐标已经继承了。只有结构不是从这个 restart 读进来的时候才有意义 |
| 坐标 + 晶胞 | `+ RESTART_CELL T` | 同上：盒子通常也继承了。CELL_OPT 之后想用优化后的盒子而结构来自别处时才选 |

两条实用规则：
• **换任务别用「全部」**：GEO_OPT 的文件里没有速度，MD 读进去会打乱初速度；反过来把 GEO_OPT 的迭代号搬进 MD，编号会从中间开始。
• **计数器默认不取**：MD 的 `STEP_START_VAL`、GEO_OPT 的迭代号都属于它；接力时通常希望新任务从第 0 步/第 1 次数起。

下面的说明行会按**当前任务和系综**把后果讲出来（例如「本次是 NPT_I：步数从第 0 步起、速度按 300 K 重新起、压浴从零开始」），换系综/改温度时跟着变。
**不需要手改 restart 文件**：CP2K 写出来的 restart 本身就是一份完整输入，手改容易和后面的关键字对不上；用取哪些量来控制更清楚，生成的输入里也会把每一项的原因写成注释。
前提：上一步真的写了 `-1.restart`（第 5 节「给下次算准备（写）」里的「重启文件」），而且文件要和本次输入放在同一个目录（或者用完整路径）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/EXT_RESTART.html>

## print_molden

*标识*：`print_molden`

`&DFT/&PRINT/&MO_MOLDEN`：把分子轨道写成 **Molden 格式**（`<项目名>-MOS.molden`），可以直接拖进 Molden、Avogadro、Jmol 看轨道形状和做轨道能级图。
它只是**多写一个文件**，不改变计算结果。
两点要注意：
• **只有 `METHOD QUICKSTEP`（DFT）有分子轨道**：FIST 经典力场下这项不出现；
• **带 k 点（周期性金属/能带）时 CP2K 会静默跳过**：不报错，文件就是不生成。想看轨道请用 Γ 点的分子/团簇模型。
文件可能不小（空轨道越多越大），长跑时留意磁盘。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.FILENAME>

## molden_file

*标识*：`molden_file`

`&MO_MOLDEN FILENAME`：文件名前缀，CP2K 的默认是 `MOS`，写出来就是 `<项目名>-MOS.molden`。
留空 = 用默认值。想跑两次不同设置、又不想互相覆盖时，在这里给个不同的名字（例如 `MOS-PBE0`）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.FILENAME>

## molden_nlumo

*标识*：`molden_nlumo`

`&MO_MOLDEN NLUMO`：**空轨道**写几条。
• `0`（默认）= 只写占据轨道，文件最小，看 HOMO/LUMO 以下够用；
• `-1` = 能算多少写多少（看导带、做能级图用这个）；
• `N` = 写 N 条空轨道。
能不能拿到空轨道取决于 SCF 路线：OT 在收敛后自己生成；对角化路线要有 `ADDED_MOS`（这条由工具按需要自动加）。
空轨道越多，文件越大、耗时越长。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.NLUMO>

## molden_unit

*标识*：`molden_unit`

`&MO_MOLDEN UNIT`：**Molden 文件内部**的坐标与晶胞单位。
CP2K 自己的默认是 `BOHR`，但大多数观看器按 Å 理解，所以本工具默认写 `ANGSTROM`——原子位置和晶胞都不会错位。
只有在你明确要跟另一份 Bohr 单位的文件对比时才改成 `BOHR`；**不会**影响 CP2K 自己算的坐标（那是另一回事）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.UNIT>

## molden_gto_kind

*标识*：`molden_gto_kind`

`&MO_MOLDEN GTO_KIND`：轨道用**球谐**还是**笛卡尔**高斯函数写。
`SPHERICAL`（默认）是 Molden/Avogadro/Jmol 期望的形式；`CARTESIAN` 手工和别的程序对接时才用——手册点名它和 VMD 不兼容（d/f 轨道会多出几条，看起来像多了几个轨道）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.GTO_KIND>

## molden_write_pseudo

*标识*：`molden_write_pseudo`

`&MO_MOLDEN WRITE_PSEUDO`：把赝势数据也写进 `[Pseudo]` 块。
用 GTH 等赝势（本工具的默认）时，勾上它文件才自包含——换台机器看轨道不用再带赝势文件。
代价是文件变大。CP2K 默认不写（F）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.WRITE_PSEUDO>

## molden_write_cell

*标识*：`molden_write_cell`

`&MO_MOLDEN WRITE_CELL`：把晶胞写进 `[Cell]` 块。
周期性体系（表面、晶体、溶液盒）勾上，观看器才能画出重复的盒子；孤立分子没有晶胞，勾了也没内容。CP2K 默认不写（F）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_MOLDEN.WRITE_CELL>

## molden_vib

*标识*：`molden_vib`

`&VIBRATIONAL_ANALYSIS/&PRINT/&MOLDEN_VIB`：频率任务里把**振动模式**也写成 Molden 格式（`<项目名>-VIBRATIONS.molden`），在 Molden 里可以直接把某个简正模动起来看。
只有 `VIBRATIONAL_ANALYSIS`（频率）任务会出现这一项，**默认打开**——频率算完不给模式等于白算一遍。
它和上面的分子轨道是两个文件，互不影响。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/VIBRATIONAL_ANALYSIS/PRINT/MOLDEN_VIB.html#CP2K_INPUT.VIBRATIONAL_ANALYSIS.PRINT.MOLDEN_VIB.FILENAME>

## 输入预览

*标识*：`preview`

这是本工具**此刻会写进文件的东西**，参数一改就重算，所以你看到的就是会保存下来的内容。
# 开头的是注释：写下每个选择的原因、数据文件名、以及该参数的收敛建议。CP2K 会忽略这些注释，但半年后回来的你会感谢它们。
想改注释或加模板里没有的段（EFIELD、CONSTRAINT 等），保存后用任何编辑器直接改就行。

## 校验结果怎么读

*标识*：`diagnostics`

这里列出生成结果的问题，鼠标悬浮某一行能看到位置和建议。
三个级别不要混淆：
• **错误**（红）—— CP2K 会直接退出或算错。最典型的是基组与赝势价电子不匹配、smearing 配了 OT、k 点配了非周期晶胞。这类必须先改。
• **警告**（黄）—— 能跑，但结果可能不可信。例如 cutoff 偏小、EPS_SCF 太松、MD 缺恒温器、NEB 没有晶胞。
• **提示**（灰）—— 只是告诉你它做了什么选择，比如某个别名被规范化了。
错误数为 0 不等于「算得对」：工具只做静态检查，SCF 收敛性、cutoff 和 k 点的收敛必须你自己用 scan 验证。

## 基组表

*标识*：`kind_table`

这是本工具最想让你看到的一张表：
• 基组的 -qN 必须和赝势的 -qN 相等，否则 CP2K 直接报 “Mismatch in the number of electrons” 并退出；
• 元素不在 MOLOPT 覆盖范围内时（镧系、部分 4d/5d）会自动换家族；
• 括号里是找到该条目的数据文件名，写进输入的就是它。

## aimd_or_classical

*标识*：`aimd_or_classical`

「分子动力学」在 CP2K 里是两条完全不同的路，由 `FORCE_EVAL METHOD` 决定：
• **AIMD / 从头算 MD**——`METHOD QUICKSTEP`，**每一步都由 DFT 现算力**，原子核在势能面上跑。步长只能 0.5 fs 左右，通常 ps 量级，但不需要任何力场参数。
• **经典 MD**——`METHOD FIST`，力来自力场（`&MM/&FORCEFIELD`），原子是带电荷、带键的质点。步长可以 1–2 fs，能跑纳秒级、微秒级，但**必须提供力场参数**（prmtop / PSF / .pot 等）。
• DFTB/xTB 的 MD——力来自紧束缚，介于两者之间（本工具不生成）。
怎么选：小体系、要成键断键、要准确 → AIMD（贵）；大体系、要长时间统计、力场可信 → 经典 MD。
本工具两条都能生成：上面「计算方法」选 FIST 就会整块换成 `&MM` 页面。

## 任务

*标识*：`task`

选你要算什么，模板会自动配好 RUN_TYPE 和对应的 &MOTION 段。
• 单点能：最便宜，先用它确认体系、电荷、自旋和收敛；
• 几何优化 / 晶胞优化：要算力，建议先用单点能确认再开；
• MD：看步数×原子数，几十 ps 的 AIMD 通常是几十万核时量级；
• 能带 / 态密度：必须有周期性晶胞，孤立分子算这个没有意义；
• NEB：需要 5 个以上镜像文件，且每个镜像都要有晶胞。
不确定就从「单点能」开始。

## method

*标识*：`method`

`FORCE_EVAL METHOD`：算不算电子。
• **DFT / Quickstep**（默认）——`&DFT`，写基组、赝势、SCF、cutoff。除了 AIMD，其他所有任务都在这一条上。
• **经典力场 FIST**——`&MM`，**完全不涉及电子**，所以基组、赝势、cutoff、SCF、OT、k 点、Smearing 全部不写（CP2K 看到 `METHOD FIST` 旁边有 `&DFT` 会直接报错）。
两者互斥，选了 FIST 上面的 DFT 页面会整块收起来。
参考：CP2K 自带的经典 MD 回归测试有 341 个带 `PARMTYPE` 的输入，其中 329 个是 CHARMM。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL.html#CP2K_INPUT.FORCE_EVAL.METHOD>

## mm_path_style

*标识*：`mm_path_style`

生成输入里那几个力场文件（拓扑 / 参数 / 坐标）**怎么写法**：文件名还是完整路径。
为什么需要这个：输入通常是在 Windows 上生成、拿到 Linux 集群上跑。把 `E:/desktop/.../mol.prmtop` 原样写进去，到了集群上根本不存在。
• **只写文件名（默认）**——无论文件在哪都只写文件名。这是最常用的约定：拓扑、坐标、输入放在一个文件夹里，整个文件夹拷到集群就能跑。**代价**是它等于你自己保证这些文件确实和输入同目录；参数文件放在别处（比如 `../ff/`）就要换成下面两种之一。
• **自动**——文件真的在输入文件所在目录里时缩写成文件名，否则按你给的原样写路径。文件分散在几个目录时用这个最稳。
• **写完整路径**——保持原样（本机跑、或确实要用绝对路径时）。
「输入文件所在目录」取的是「5. 输出」里那个保存路径所在目录；命令行对应 `--mm-path-style`，「自动」判断用的是 `-o` 的目录。
注意这只是**写法**：文件是否存在仍然是按你给的原路径检查的，所以文件真的不在那儿时照样会提示（提示里带的是完整路径）。

## mm_route

*标识*：`mm_route`

用哪一套力场文件。CP2K 的 `&FORCEFIELD PARMTYPE` 只有五个值，这里按「文件怎么给」分成几条路线：
• **AMBER**——`PARMTYPE AMBER` + prmtop（`%VERSION`/`%FLAG` 那种文本），连接性用 `CONNECTIVITY AMBER`，坐标是 AMBER 的 inpcrd（`COORDINATE CRD`）。**prmtop 里同时有连接性、电荷和 LJ 参数**，所以 `PARM_FILE_NAME` 和 `CONN_FILE_NAME` 常常指同一个文件。
• **CHARMM**——`PARMTYPE CHM` + `.pot/.prm` 参数文件，拓扑是 PSF，坐标用 PDB/CRD。CP2K 自己的测试绝大多数是这条。
• **GROMOS96 / GROMOS87**——`.top/.gtop` 拓扑 + `.g96` 坐标。
• **参数写在输入里**——`PARMTYPE OFF`，把 `&CHARGE/&BOND/&BEND/&NONBONDED` 直接写在 `&FORCEFIELD` 里（小体系、教学例子最方便）。
• **只有对势**——`PARMTYPE OFF` 且只给 `&CHARGE` 和 `&NONBONDED/&LENNARD-JONES`，适合 Ar 这类单原子体系。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.PARMTYPE>

## mm_parm_file

*标识*：`mm_parm_file`

`&FORCEFIELD PARM_FILE_NAME`：力场参数文件。
AMBER 用 tleap 生成的 **prmtop**；CHARMM 用 `.pot/.prm/.par`；GROMOS 用 `.top/.gtop`。
注意后缀不代表内容：CP2K 只看 `PARMTYPE`，所以别人把 prmtop 命名成 `.top` 也一样能读（官方测试里就有 `pc.top`/`lamol.top` 这种 prmtop）。
**这一格不能空**：`PARMTYPE` 是 CHM/AMBER/G96/G87 时，CP2K 在 `force_fields_input.F` 里发现文件名为空会直接 `CPABORT("Force Field Parameter's filename is empty!")`。
**用了 `&MOL_SET`（每个组分一个拓扑）也照样要填它**，而且它是**参数的唯一来源**：每个单体的拓扑是以 `do_forcefield=.FALSE.` 读的，只给连接性和电荷；键长/键角/二面角的力常数和 LJ 参数全部从这个文件来，并按**原子类型名**套到原子上。所以两个不同组分各有一个 prmtop 时，指向其中一个是不够的——另一个的项不在表里，CP2K 会 abort：`Missing critical ForceField parameters!`（`force_fields_util.F`）。最省事的办法还是生成一个覆盖整个体系的 prmtop。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.PARM_FILE_NAME>

## mm_conn_file

*标识*：`mm_conn_file`

`&TOPOLOGY CONN_FILE_NAME`：拓扑文件，决定「谁和谁成键、谁和谁排斥」。
AMBER 指 prmtop，CHARMM 指 PSF，GROMOS 指 `.top`。
LJ 参数、1-4 排除表（exclusion list）和 1-4 缩放都从它来，所以**它和坐标文件的原子顺序必须完全一致**，否则 CP2K 要么报错，要么（更糟）算出一个原子配错键的体系。
**AMBER/GROMOS 下它和参数文件通常是同一个文件**（prmtop 里同时装着参数和连接性），所以这一行右边有个**「同参数文件」**框，默认勾上——那样只用填一次路径，生成时会自动把它继承过去；勾掉才能给一个不同的拓扑文件。不勾而留空时，生成器会写一条注释说明缺这一项，校验器也会报 `mm-connectivity-without-file`。
CHARMM 不继承：PSF（连接性）和 `.pot`（参数）本来就是两个文件，所以切到 CHARMM 路线时这个框会自动取消勾选。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.TOPOLOGY.CONN_FILE_NAME>

## mm_coord_file

*标识*：`mm_coord_file`

`&TOPOLOGY COORD_FILE_NAME`：坐标文件。
AMBER 路线用 inpcrd/restart（`COORDINATE CRD`）；CHARMM 常给 PDB；GROMOS 给 `.g96`。
**这一格空着也能算**：力场路线空着时工具会把体系坐标写成输入里的 `&SUBSYS/&COORD`（CP2K 的 restart 就是这么做的——它写 restart 时会把 `COORD_FILE_NAME` 去掉、把坐标内联进去）。所以拿上一轮的 `-1.restart` 当结构时，**不需要再准备 inpcrd**；那种情况下这一格会被自动清空（里面残留的 restart 路径不是坐标文件，CP2K 会把它当 CRD 读）。
**填了这一格就以文件为准**：工具这时不再写 `&COORD`。
**两处都给坐标时谁赢？是 `&COORD`，不是文件**（`topology.F` 的 `coordinate_control`）：CP2K 先读坐标文件，再读 `&COORD` 并且 `overwrite=.TRUE.`，日志里会打 *"Overwriting coordinates. Active coordinates read from &COORD section."*。所以工具绝不两个都写——留两处最容易让人以为读的是文件。
**盒子的优先级又是另一套**（`cell_methods.F`）：`CELL_FILE_NAME` > `ABC`/`ALPHA_BETA_GAMMA` > `A`/`B`/`C` > 最后才轮到 `COORD_FILE_NAME` 里的 box。工具总会写 `&CELL`，所以**坐标文件里的盒子用不上**（AMBER 的 inpcrd 自带 box 也一样）——盒子要在这里下方的「4. 体系」里给。
于是有一件 CP2K 不会替你查的事：坐标来自文件、盒子来自载入的结构，两份不是同一次运行的产物（restart + inpcrd 很常见）时它们就对不上。工具会核对**原子数**和**盒子**（inpcrd 的 box 读得出时），不一致会警告；接力时想用 restart 里的坐标，把这一格清空即可。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.TOPOLOGY.COORD_FILE_NAME>

## mm_coord_format

*标识*：`mm_coord_format`

`&TOPOLOGY COORD_FILE_FORMAT`（别名 `COORDINATE`）。
CP2K 只认这几个：`OFF`（坐标写在输入里）、`PDB`、`XYZ`、`G96`、`CRD`、`CIF`、`XTL`、`CP2K`。
**`CRD` 就是 AMBER 的坐标格式**（inpcrd / restart 的定宽卡片格式），不是「随便什么坐标文件」。把 PDB 当 CRD 读会得到一堆错位的数字，所以选错时会提示。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.TOPOLOGY.COORD_FILE_FORMAT>

## mm_molecules

*标识*：`mm_molecules`

`&MOL_SET`：**每个组分一个拓扑文件**——体系由几个组分拼成，就填几行。
**不是只有「混合体系」才用它**：只要体系里的分子是**同一个单分子拓扑复制出来的**就要用这张表，哪怕只有一种分子——比如手里是 1 个水分子的 prmtop、盒子里有 200 个水，就填一行 `NMOL 200`（这时顶层的 `CONN_FILE_NAME` 反而不对：那个 prmtop 只描述一个分子，而 CP2K 用 `CONN_FILE_NAME` 时会要求它覆盖整个体系的原子）。
只有当你有一个**整个体系合并好的拓扑**（例如 tleap 里 combine 后 saveAmberParm 得到的那一个 prmtop）时才不用这张表——那时直接在「拓扑文件」里填它。
⚠ **注意参数**：AMBER 的参数**只从 `PARM_FILE_NAME` 来**，每个单体的拓扑只提供连接性和电荷（CP2K 读它时用 `do_forcefield=.FALSE.`）。所以两个不同组分各有一个 prmtop 时，把「参数文件」指向其中一个是不够的——另一个的键/角项不在表里，CP2K 会直接 `Missing critical ForceField parameters!` 退出。稳妥做法还是生成一个覆盖整个体系的 prmtop。
两点必须对上：
• **坐标仍然只有一个文件**（`COORD_FILE_NAME`），顺序要和这里的行序一致；
• `NMOL` 的份数乘上单体原子数要等于坐标文件里的原子数。
如果坐标文件是 packmol 的 PDB，点下面的「按坐标文件识别组分」就能把行数和 `NMOL` 自动填出来（PDB 的残基/链号标出了分子边界）。官方例子见 `tests/Fist/regtest-14/lamol.inp`（一种分子 ×4）和 `tests/Fist/regtest-1-2/multi_psf.inp`（溶质 + 溶剂）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY/MOL_SET.html>

## mm_ff_file

*标识*：`mm_ff_file`

「参数写在输入里」路线用的力场条款文件：里面写 `&CHARGE`、`&BOND`、`&BEND`、`&TORSION`、`&IMPROPER`、`&NONBONDED/&LENNARD-JONES` 这些。
文件可以是零散的条款，也可以是完整的 `&FORCEFIELD` 甚至 `&MM` 段——三种写法都会被认出来（从手册或 CP2K 测试里直接拷一段过来就能用）。
它和 ε/σ 表**二选一**：给了文件就不再自动生成对势块。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html>

## mm_lj

*标识*：`mm_lj`

`&NONBONDED/&LENNARD-JONES`：对势参数，一个元素对一块。
**单位是 CP2K 规定的：`EPSILON` 用 K（开尔文），`SIGMA` 和 `RCUT` 用 Å**（`unit_str="K_e"` / `"angstrom"`）——写成 kJ/mol 是经典错误。
函数形式 `V = 4ε[(σ/r)¹² − (σ/r)⁶]`，所以 σ 是势能为零的距离，不是平衡距离（平衡距离是 2^(1/6)σ ≈ 1.122σ）。
填「元素」这一列（标量行）就会按 **Lorentz-Berthelot** 规则（σ_ij=(σ_i+σ_j)/2，ε_ij=√(ε_i ε_j)）自动补出所有交叉对；写成 `O-H` 这种两元素形式则是显式指定某一对，优先使用。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES.html>

## mm_lj_rcut

*标识*：`mm_lj_rcut`

`&LENNARD-JONES RCUT`：这个对势的截断半径（Å），CP2K 默认 10 Å。
它和 Ewald 的实空间截断是两回事，两者不一致时 CP2K 取合适的邻居表范围。
经验上取到 2.5σ 以上（Ar 的 σ=3.4 Å → RCUT≈8.5 Å），再大只会变慢。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.NONBONDED.LENNARD-JONES.RCUT>

## mm_element_lj

*标识*：`mm_element_lj`

按元素给 ε/σ，交叉对用 Lorentz-Berthelot 混合规则生成：
σ_ij = (σ_i + σ_j)/2、ε_ij = √(ε_i·ε_j)。
这是大多数力场（含 AMBER/OPLS 的默认做法）的混合规则，但**不是唯一的**：如果你的力场表里直接给了交叉项，就在表里用 `O-H` 这样的两元素行显式写出来，它会覆盖混合结果。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES.html>

## mm_charges

*标识*：`mm_charges`

`&FORCEFIELD/&CHARGE`：每个原子类型的点电荷，单位是电子电荷 e。
只对「参数写在输入里」的两条路线有意义——从 prmtop/PSF 读参数的体系，电荷在拓扑文件里，这里写了也会被忽略。
留空时工具会给每个元素写 0.0：纯 LJ 体系（Ar 那种）确实需要显式写 0，CP2K 自己的 argon 测试就是这么写的；但**别把 0 当成「中性」**，带电体系漏掉电荷会得到完全错误的静电。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/CHARGE.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.CHARGE.CHARGE>

## mm_vdw_scale14

*标识*：`mm_vdw_scale14`

`VDW_SCALE14` / `EI_SCALE14`：1-4 相互作用的缩放因子。
**这是最容易静默算错的地方**：CP2K 的默认值是 `VDW_SCALE14 1.0` / `EI_SCALE14 0.0`，而 `0.0` 会把**所有 1-4 静电项全部去掉**——AMBER 和 CHARMM 都不是这么定义的。
本工具按路线填推荐值：
• AMBER：0.5 / 0.8333（PARM94 及以后；PARM91 是 0.5/0.5）
• CHARMM：1.0 / 1.0（CHARMM 不做 1-4 缩放）
想用 CP2K 默认就显式填 1.0/0.0。留空 = 跟随路线推荐值。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.EI_SCALE14>

## mm_ei_scale14

*标识*：`mm_ei_scale14`

同上：`EI_SCALE14` 是 1-4 静电的缩放。
CP2K 默认 0.0（等于关掉 1-4 静电），AMBER 通常 0.8333，CHARMM 是 1.0。改它之前先确认你的参数集是怎么定义的。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.EI_SCALE14>

## mm_ewald

*标识*：`mm_ewald`

`&MM/&POISSON/&EWALD EWALD_TYPE`：经典静电怎么算。
• **SPME**（周期体系的推荐值）——平滑粒子网格 Ewald，O(N log N)；
• **PME**——普通粒子网格 Ewald；**EWALD**——不用 FFT 的标准 Ewald；
• **NONE**——只算实空间库仑和。**非周期体系只能用它**（SPME/PME 要在周期网格上插值），对孤立分子是对的、对周期溶液是错的。
工具默认按晶胞周期自动选：周期 → SPME，非周期 → NONE。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD.html#CP2K_INPUT.FORCE_EVAL.MM.POISSON.EWALD.EWALD_TYPE>

## mm_ewald_gmax

*标识*：`mm_ewald_gmax`

`&EWALD GMAX`：倒空间的最大 Miller 指数（网格点数）。越大越准也越贵。
⚠ **`EWALD_TYPE EWALD` 和 `SPME` 必须给 GMAX**：CP2K 对 GMAX **没有默认值**（`ewald_environment_types.F` 的注释就是这么写的），缺了会在 `fist_init` 阶段直接中止：
`Value requested, but no value set getting value from keyword GMAX of section EWALD`。
所以留「自动」时**不是留空**——工具按晶胞和 ALPHA 替你估一个（用 CP2K 自己那条 TB-Ewald 公式：`cutoff = 45·α`，`gmax_i = 2·floor(√(2·cutoff)·|a_i|/2π)+1`；24.4 Å 的盒子、α=0.35 得到 59）。
什么时候自己填：想让两次计算**完全可比**（对比能量差），或者盒子/电荷分布很特殊时按上面的估计值上下调一档。
`EWALD_TYPE PME` 用的是 `NS_MAX`（有默认值 11），不需要 GMAX。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD.html#CP2K_INPUT.FORCE_EVAL.MM.POISSON.EWALD.GMAX>

## mm_ewald_alpha

*标识*：`mm_ewald_alpha`

`&EWALD ALPHA`：Ewald 分裂参数（实空间/倒空间的分工），单位 Å⁻¹。
它和 `GMAX`、实空间截断是一组：ALPHA 越大实空间收敛越快、倒空间就需要越多 G 矢量；**单独改一个会让另一边的精度悄悄变差**，所以要么两个都留默认，要么两个都自己定。
留空 = CP2K 自己的默认 **0.35**（不是按 EWALD_ACCURACY 估的）。
（自动的 GMAX 也是按你填的这个 ALPHA 估的，所以改 ALPHA 会同时改 GMAX。）

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD.html#CP2K_INPUT.FORCE_EVAL.MM.POISSON.EWALD.ALPHA>

## mm_o_spline

*标识*：`mm_o_spline`

`&EWALD O_SPLINE`：PME/SPME 的插值阶数，6 是常用值（阶数越高越准、越贵）。`EWALD_TYPE NONE/EWALD` 下没有这个参数。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD.html#CP2K_INPUT.FORCE_EVAL.MM.POISSON.EWALD.O_SPLINE>

## mm_rcut

*标识*：`mm_rcut`

`&EWALD RCUT`：Ewald 实空间部分的截断半径（Å）。
给了它就覆盖 `EWALD_ACCURACY` 的估计值。
注意它和 `&LENNARD-JONES RCUT` 是两回事：后者是每个对势自己的截断，两者不一致时 CP2K 会取合适的邻居表范围。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD.html#CP2K_INPUT.FORCE_EVAL.MM.POISSON.EWALD.RCUT>

## mm_verlet_skin

*标识*：`mm_verlet_skin`

`&MM/&NEIGHBOR_LISTS VERLET_SKIN`：邻居表缓冲厚度（Å）。
每步不重建邻居表，只有原子移动超过半个 skin 才重建。
调大省重建时间但更费内存；CP2K 默认 1.0 Å，通常不用改。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/NEIGHBOR_LISTS.html#CP2K_INPUT.FORCE_EVAL.MM.NEIGHBOR_LISTS.VERLET_SKIN>

## mm_spline_emax

*标识*：`mm_spline_emax`

`&FORCEFIELD/&SPLINE EMAX_SPLINE`：非键势用样条拟合的能量上限（Ha）。
CP2K 把非键势在近程用样条近似以换取速度，1.0 Ha 是常用值。
只有当体系里有非常近的接触（比如高压、或者初始结构没优化过）才需要调大，否则会出现样条区间外的警告。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/SPLINE.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.SPLINE.EMAX_SPLINE>

## mm_shift_cutoff

*标识*：`mm_shift_cutoff`

`&FORCEFIELD SHIFT_CUTOFF`：把非键相互作用整体平移，使它在截断半径处正好为 0（能量连续、力连续）。
CP2K 默认 `T`。关掉它（`F`）会有截断处的不连续能量跳变，但和某些力场定义的原始形式更一致——官方测试里两种都有（`cubane_15.inp` 就显式写了 `SHIFT_CUTOFF .false.`）。
留空 = 跟随 CP2K 默认。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.SHIFT_CUTOFF>

## mm_multiple_potential

*标识*：`mm_multiple_potential`

`MULTIPLE_POTENTIAL`：允许 `&NONBONDED`/`&NONBONDED14` 里同一个原子对叠好几块不同的势（分段定义的势）。
默认关。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.MULTIPLE_POTENTIAL>

## mm_ignore_missing_params

*标识*：`mm_ignore_missing_params`

`IGNORE_MISSING_CRITICAL_PARAMS`：缺少关键力场参数时不报错，当作那一项为 0 继续算。
**这是调试参数集用的**，正常计算一定要让它报错——否则你会得到一个少了某类键/角的能量，而且不会有任何提示。
默认关（和 CP2K 一致）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD.html#CP2K_INPUT.FORCE_EVAL.MM.FORCEFIELD.IGNORE_MISSING_CRITICAL_PARAMS>

## mm_stress_tensor

*标识*：`mm_stress_tensor`

`&FORCE_EVAL STRESS_TENSOR`：算不算应力张量。
**CP2K 的默认是 `NONE`**，而变胞优化（CELL_OPT）和 NPT 系综必须有应力才能压缩晶胞——所以工具在这两种情况下自动写 `ANALYTICAL`。
经典力场的应力是解析可得的，很便宜；单点能/几何优化想顺便看应力也可以手动打开。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL.html#CP2K_INPUT.FORCE_EVAL.STRESS_TENSOR>

## 泛函

*标识*：`functional`

交换关联泛函。常用选择：
• PBE —— 通用默认，固体、表面、吸附都先用它；
• PBEsol —— 晶格常数比 PBE 准，做晶胞优化可以换它试试；
• BLYP —— 分子更常用，配合 D3 色散；
• PBE0 / HSE06 —— 带隙更准，但代价高一个量级，必须先做 PBE 再上；
• PADE —— LDA，只在需要和文献对照时用。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/XC/XC_FUNCTIONAL.html>

## 色散

*标识*：`dispersion`

色散校正。判断标准很简单：
• 分子晶体、吸附能、层状材料（石墨、MoS2）、π-π 堆积 —— **必须加**，不加会算成几乎没有结合；
• 共价晶体、离子晶体、致密金属 —— 可加可不加；
• 孤立分子做构象能差 —— 建议加。
一般选 DFT-D3(BJ)，它是当前 D3 的推荐形式；D3 用零阻尼，要和老文献对照时选。
REFERENCE_FUNCTIONAL 会自动跟着上面的泛函走。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/XC/VDW_POTENTIAL.html>

## CUTOFF

*标识*：`cutoff`

平面波截断（最细网格）。**这是最需要你自己收敛的参数**。
经验起点：SZV≈350、DZVP≈400、TZVP≈600、TZV2P≈700 Ry。
勾「自动」时按你选的基组给初值。
把它从 400 提到 600 常常只差几 meV/atom，但力和平滑性会变好；用 `cp2kbuild scan --cutoffs 400,500,600,700`（或 GUI 外的命令行）看总能量随 cutoff 的变化，小于 1 meV/atom 就算收敛了。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/MGRID.html#CP2K_INPUT.FORCE_EVAL.DFT.MGRID.CUTOFF>

## REL_CUTOFF

*标识*：`rel_cutoff`

决定一个“标准”高斯（alpha=1）被映射到哪一层网格。
经验值 60 Ry，基组越弥散可以到 80；它必须小于 CUTOFF。
只影响精度和速度，不影响结果定义；一般不需要动。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/MGRID.html#CP2K_INPUT.FORCE_EVAL.DFT.MGRID.REL_CUTOFF>

## eps_scf

*标识*：`eps_scf`

SCF 的收敛阈值，默认 1e-6（freq 会收紧到 1e-7）。
该取多少：
• 算能量/力：1e-6 够用；
• 算频率、声子、应力、能量差很小的反应：1e-7 或更紧，否则数值噪声会盖过你要的那个差别；
• 1e-8 以下通常只是在烧机时，收益很小。
先松后紧是个好习惯：先用 1e-5 确认结构没崩，再收紧。

## outer_max_scf

*标识*：`outer_max_scf`

外层 SCF 循环的次数（`&SCF/&OUTER_SCF/MAX_SCF`）——**你模板里的 `SCF_OCYCLES` 就是这个**。
内外两层的分工：
• **内层**（`MAX_SCF`，上面的 `SCF_NCYCLES`）每次电子步最多迭代几次；
• **外层**每跑完一轮内层就换一次预条件子/混合参数再跑一轮，专门用来救那些「内层怎么迭代都不收敛」的体系。
取值建议：
• 留「自动」——OT 路线工具会写 20（CP2K 自己的默认是 50），对角化路线不写这个段（让 CP2K 用自己的默认）；
• **0 = 关掉外层循环**。做收敛扫描时常见做法：内层给 1、外层给 0，那就是「只迭代固定步数」，这时候**必须同时打开「SCF 没收敛也继续」**，否则 CP2K 会直接中止；
• 难收敛体系给 10–50（语料里最常写的就是 10）。
注意：外层循环每一轮都要重跑一遍内层，是很贵的——先调混合参数，再考虑靠它硬撑。

## outer_eps_scf

*标识*：`outer_eps_scf`

外层循环的收敛阈值（`&OUTER_SCF/EPS_SCF`），留空就与内层的 EPS_SCF 相同。
什么时候要分开：外层判的是「换了预条件子之后整体还动不动」，通常可以比内层松一档（比如内层 1e-6、外层 1e-5），能省不少机时。
但如果内层阈值本身就给得松（1e-5 以上），外层再松就没意义了。

## max_scf

*标识*：`max_scf`

**内层** SCF 每次电子步最多迭代几次（`&SCF/MAX_SCF`）——**你模板里的 `SCF_NCYCLES` 就是这个**。工具默认 30，CP2K 自己的默认是 50。
• 正常体系 20–30 就收敛；
• 难收敛（金属、强关联、大体系）可以加到 100–200；
• 但**一直靠加大 MAX_SCF 是治标**：先看是不是混合参数不对（下面那组 α/缓冲）、或者该开 SMEAR；
• 想只迭代固定步数（做收敛扫描时常用）：内层给 1、外层给 0，并且**必须**打开「SCF 没收敛也继续」，否则 CP2K 会直接中止（校验时也会提醒你这一点）。
配合「SCF 没收敛也继续」用时，这里就是「固定迭代步数」的意思。

## qs_method

*标识*：`qs_method`

**QS = Quickstep**，就是 CP2K 的电子结构引擎（`&FORCE_EVAL/&DFT/&QS`）；这一项是它的两种方法：
• **GPW**（默认）——高斯与平面波混合，绝大多数情况用它；
• **GAPW**——把原子附近区域单独处理，全电子计算或需要精确的芯区描述时用。贵得多（一般 2–5 倍），而且对基组/赝势的搭配更敏感。
除非你在做全电子或专门研究芯电子，否则不要换。
（`METHOD FIST` 是另一条路：经典力场，连 `&DFT` 都不写。）

## eps_default

*标识*：`eps_default`

`&QS EPS_DEFAULT`：**一个总精度旋钮**。
散在别处的阈值（`EPS_SCF`、网格积分的 `EPS_RHO`/`EPS_RHO_GSPACE`、`EPS_PGF_ORB`…）如果没有单独写，**都跟着它缩放**；CP2K 自己的默认是 `1e-10`。
怎么选：
• 默认 1e-10 对绝大多数体系够用；
• 觉得 SCF 收敛慢、或只是做粗略扫描 → 1e-8 / 1e-9 会明显变快，但**能量、力、应力都会受影响**，要跟 CUTOFF 一样做一次收敛确认；
• 需要高精度能量差（声子、弱相互作用）→ 1e-12 也有用，代价是机时。
留空 = 用 CP2K 的 1e-10（工具也不写这一行）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/QS.html#CP2K_INPUT.FORCE_EVAL.DFT.QS.EPS_DEFAULT>

## extrapolation

*标识*：`extrapolation`

`&QS EXTRAPOLATION`：用上一步的波函数/密度**猜下一步**（外推），MD 和几何优化里能让每步的 SCF 快很多。
工具的默认是「自动」：
• 有连续迭代步的任务（MD / GEO_OPT / CELL_OPT）→ `ASPC`
（CP2K 里最常用的多项式外推）；
• 单点能量/频率 → `USE_GUESS`（没有「上一步」可用，外推毫无意义）。
其它取值：`LINEAR_P`/`LINEAR_PS`/`PS` 是不同阶数的外推，`USE_PREV_P`/`USE_PREV_WF` 只拿上一步的值，`USE_GUESS` 等于不外推。
注意手册点明 `LINEAR_WF`/`LINEAR_PS` **不适用于 k 点**。
SCF 莫名收敛困难时，把它设成 `USE_GUESS` 可以排除外推的干扰。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/QS.html#CP2K_INPUT.FORCE_EVAL.DFT.QS.EXTRAPOLATION>

## ngrids

*标识*：`ngrids`

多级网格的档数，CP2K 默认 4。
4 档的分辨率比例固定为 1 : 1/3 : 1/9 : 1/27，大多数体系够用。
什么时候改：
• 体系很大、格子很空 → 加一档（5）能省机时；
• 出现「网格太粗」的报错或能量对 CUTOFF 异常敏感 → 也值得试 3/5 档。
改档数会改变每档网格上的点数分布，所以**改完要重做 CUTOFF/REL_CUTOFF 收敛**。

## added_mos

*标识*：`added_mos`

额外计算的空轨道数。
• 用 OT 时不需要（工具不写）；
• 用对角化（k 点、SMEAR、能带、PDOS）时**必须够**：空轨道太少，费米面附近和未占据态就不准，PDOS 会缺一截；
• 留「自动」时工具按原子数给（≤20 原子给 10，≤100 给 20…）。
算 PDOS/能带怀疑「上面少东西」时，先把它加到 2 倍看看。

## smearing

*标识*：`smearing`

给占据数加一个电子温度的模糊（Fermi-Dirac），金属和小带隙体系必用。
不加的后果很典型：SCF 在费米面附近来回振荡、收敛不了，或者能量随 k 点/展宽跳变。
代价：展宽本身会给能量加一点熵贡献，所以
• 只在确实需要时开；
• 算完看一眼输出里的「电子熵」，必要时把它从能量里扣掉；
• 开了之后工具会自动切到对角化路线，且不写 &MIXING（用 CP2K 自己的默认混合）。

## electronic_temperature

*标识*：`electronic_temperature`

展宽的电子温度，默认 300 K（≈0.026 eV）。
怎么选：
• 真金属：300–1000 K 都常见；
• 小带隙半导体：从 300 K 起试，收敛不了再往上加；
• 加得越高越容易收敛，但对能量的污染越大。
经验做法：用较高的温度先收敛，再用较低温度重新算一遍。
验证方法：把它乘 2，如果能量几乎没有变化，说明已经够用。

## ignore_convergence

*标识*：`ignore_convergence`

SCF 到 MAX_SCF 还没收敛也继续算完并打印能量（IGNORE_CONVERGENCE_FAILURE）。
**什么时候可以**：做相对比较时——比如扫 CUTOFF，你只关心不同档位之间的*差值*，SCF 收敛程度的差别在两档之间基本抵消。你自己的模板就是这种用法（MAX_SCF 1 硬顶）。
**什么时候不行**：
• 要报绝对能量、结合能、反应能；
• 要看力、应力、频率——它们对 SCF 收敛远比能量敏感；
• 做 MD/几何优化——不收敛的力会把结构推歪。
工具在分析结果时会认出这个设置并提醒你。

## mixing

*标识*：`mixing`

SCF 的电荷混合方式与参数。工具默认写 BROYDEN_MIXING：
• **α**（ALPHA，默认 0.4）——每一步用多少新密度。收敛振荡、能量上下跳 → 调小（0.1–0.2）；收敛太慢 → 可以试 0.5，但超过 0.5 通常更不稳；
• **缓冲**（NBUFFER，默认 8）——保留多少历史步做 Broyden 更新。体系大、需要更多历史时加到 10–20；
• **β**（BETA）——Kerker 阻尼，留空则不写。金属体系常常需要（试 0.5–1.5）。
注意：只有纯对角化路线会写 &MIXING。OT 不用它；开了 SMEAR 时工具也不写（CP2K 自己的默认对金属更合适）——这时候这两个框会变灰，免得你以为改了就有用。

## scaled_coords

*标识*：`scaled_coords`

用分数坐标（SCALED）写 &COORD，需要晶胞；没有晶胞时这个框是灰的。
什么时候想要它：
• 坐标来自晶体学文献/数据库（CIF 里基本都是分数坐标）；
• 手算掺杂、超胞、移动原子时，分数坐标不用乘晶胞，改晶胞也不用重算坐标；
• 变胞优化时想看「原子相对晶胞的位置动没动」。
换算：Cartesian = 分数坐标 × 晶胞矩阵。两者物理上完全等价，工具两种都能读（回来做输入时也能识别 SCALED）。

## thermostat_timecon

*标识*：`thermostat_timecon`

恒温器的耦合时间（&THERMOSTAT/&NOSE 或 &CSVR 的 TIMECON），默认 100 fs。
物理含义：温度偏差被修正掉的快慢。粗看很反直觉——
• **太大**（比如 1000 fs）→ 温度慢慢漂，跑短了统计不准；
• **太小**（比如 10 fs）→ 恒温器频繁「打」体系，把真实的振动动力学搅乱，扩散系数、振动谱都会失真。
经验范围：100–1000 fs，一般取时间步长的 100–1000 倍。报告里如果要算动力学性质，把它写清楚——这个数会直接影响结论。

## barostat_timecon

*标识*：`barostat_timecon`

恒压器的耦合时间（&BAROSTAT 的 TIMECON），默认 500 fs。
它和恒温器是一个道理，但**更慢**：体积的自由度比速度慢得多，所以常规做法是取恒温器耦合时间的 5–10 倍。
取值不当的典型症状：
• 太小 → 晶胞疯抖，甚至把结构压塌；
• 太大 → 跑完 10 ps 体积还没稳定，得到的密度不可信。
做 NPT 时建议先跑一小段看体积是不是平稳，再决定要不要缩短。

## trajectory_stride

*标识*：`trajectory_stride`

每隔多少步往轨迹文件写一帧（&MOTION/&PRINT/&TRAJECTORY/&EACH）。
计数层级跟着任务走：MD 数 **MD 步**、几何优化数 **GEO_OPT 步**、晶胞优化数 **CELL_OPT 步**（工具按任务自动写，读别人的输入时也按实际层级取值）。
默认 10 步。权衡：
• 写太密 → 文件巨大（几万步的 MD 能到 GB 级），I/O 拖慢计算；
• 写太疏 → 后处理（RDF、扩散、振动谱）频率分辨率不够，甚至时间相关函数算不准。
经验：轨迹里相邻帧的间隔（步数 × 时间步长）要明显小于你关心的最快运动周期；做振动分析时通常要每步都写。

## h_to_d

*标识*：`h_to_d`

**氘代（H→D）**：把氢的**核质量**换成氘的 2.0141017781 u（写 `&KIND H / MASS 2.0141017781`）。
这不是化学反应：Born–Oppenheimer 近似下势能面只依赖核电荷、不依赖核质量，所以**元素仍是 H**，基组/赝势一个字都不改，只有动力学变。
**为什么要这么做（AIMD 最常见的动机）：让步长翻倍。** 振动频率 ν ∝ 1/√m，所以 X–D 的伸缩频率是 X–H 的 1/√2：
• O–H 伸缩 ~3400 cm⁻¹ → 周期 ≈ 9.8 fs → 积分只能 **0.5 fs**；
• O–D 伸缩 ~2400 cm⁻¹ → 周期 ≈ 13.9 fs → **1.0 fs** 是常规选择。
AIMD 的开销 ≈ 步数 × 每步 SCF，所以同样模拟时长**直接省一半机时**。
**结果是「重同位素」体系，不是 H 体系**：经典核 MD 里玻尔兹曼分布与质量无关，静态结构（RDF、配位数）几乎不变，但时间尺度上的量会变——扩散系数（D₂O 比 H₂O 慢约 20%）、振动谱、H 键寿命、反应速率。想研究 H 本身（质子转移、量子核效应）不能拿这个顶替，那要用路径积分分子动力学（`&PINT`）。
另外两个常见用途：实验大多在氘代样品上做（中子衍射/NMR/IR），模拟 D 才能直接对比；算动力学同位素效应（KIE）时 D 就是你要的体系。
⚠ **和「质量放大」不是一回事**：有人为了省机时把 H 设成 2.0/3.0、连 O 一起放大（CP2K 自己的 `tests/QS/regtest-gpw-3/dynamics.inp` 就是 `MASS 2.` + O 18.0855），那只是个数值加速器，结果不对应任何真实体系；本工具只写氘的准确质量。
**力场路线（METHOD FIST）用不上这个**：AMBER 的质量来自 prmtop 的 `%FLAG MASS`、CHARMM 来自 PSF（`topology_amber.F:526`），`&KIND MASS` 不会被读——那时控件会变灰，要改质量得改拓扑文件。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.KIND.MASS>

## md_energy_print

*标识*：`md_energy_print`

`&MOTION/&MD/&PRINT/&ENERGY`：写 `<项目名>-1.ener`。**一行一步**：
步号 | 时间[fs] | 动能[a.u.] | **Temp[K]** | 势能[a.u.] | 守恒量[a.u.] | 用时[s]。
这就是 MD 最常用的「温度-时间曲线」：`plot 'cp2k-1.ener' u 2:4 w l` 直接画出 T(t)，`u 2:6` 看守恒量漂不漂。
**CP2K 自己的默认（PRINT_LEVEL LOW 起）就会写它**，所以不勾这个也有 `-1.ener`（每步一行）。勾上是为了显式打开、并控制间隔（`&EACH MD n`）和文件名——跑几十万步只想留稀疏的温度历史时用得上。
注意：它是**这次运行写出去**的文件，和 `&EXT_RESTART` 读的 `-1.restart` 是两回事。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/PRINT/ENERGY.html>

## md_energy_every

*标识*：`md_energy_every`

`&MD/&PRINT/&ENERGY/&EACH MD`：每多少 MD 步往 `-1.ener` 写一行，默认 **1**（每步）。
每步一行并不贵（一行几十字节，一百万步也就几十 MB），画温度曲线、看守恒量漂移都靠它，所以默认每步是合理的。
调大（比如 100）只在两种场合有意义：步数极多、或者只想看长期平均。代价是曲线变稀，短时间的温度尖峰可能整段被跳过。
注意 `&EACH` 只是**写文件**的频率，不影响每一步的计算。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/PRINT/ENERGY/EACH.html#CP2K_INPUT.MOTION.MD.PRINT.ENERGY.EACH.MD>

## md_energy_file

*标识*：`md_energy_file`

`&MD/&PRINT/&ENERGY FILENAME`：`-1.ener` 的文件名，留空（默认）就是`<项目名>-1.ener`。
CP2K 的拼法是 `<项目名>` + `-文件名` + `-<运行号>` + `.ener`：
• 留空 → `cp2k-1.ener`；
• 填 `run` → `cp2k-run-1.ener`；
• 填 `sub/run` → `sub/run-1.ener`（带目录就用这个目录）；
• 以 `=` 开头（`=ener.dat`）→ **完全按写的来**，不加项目名也不加运行号。
同一个目录里放多套 MD（不同温度、不同体系）时用得上。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/PRINT/ENERGY.html#CP2K_INPUT.MOTION.MD.PRINT.ENERGY.FILENAME>

## md_temp_kind_print

*标识*：`md_temp_kind_print`

**每个原子种类各自的温度**：`&MD TEMP_KIND T` + `&MD/&PRINT/&TEMP_KIND`，写 `<项目名>-1.temp`：一行里是 步号 / 时间[fs] / 每个 kind 一个温度。
什么时候用：
• **混合体系有没有真热平衡**——全局 300 K 里可能水 350 K、溶质 250 K，只看总温度看不出来；
• 离子液体/聚合物+小分子这类质量、内自由度差别大的组分之间能量传得慢；
• FIST 里核-壳、不同原子类型的动能分配。
**两个关键字必须成对**：只开打印键时 CP2K 只警告、不写文件（`md_energies.F`：「The print_key MD%PRINT%TEMP_KIND has been activated but the calculation of the temperature per kind has not been requested. Please switch on the keyword MD%TEMP_KIND.」）。工具会把两个一起写上，校验器也会点名手写输入里的这种组合。
划分依据是 **CP2K 的 kind**（QS 里是元素，FIST 里是力场里的原子类型），不是分子；想按**组分/分子**看温度请用「热浴区域」+「热浴温度」。
注意文件扩展名是 `.temp`（不是 `.tempkind`）：`<项目名>-1.temp`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.TEMP_KIND>

## md_temp_kind_every

*标识*：`md_temp_kind_every`

`&MD/&PRINT/&TEMP_KIND/&EACH MD`：每多少步写一行每个 kind 的温度，默认 **10**。
它的计算本身每步都做（`&MD TEMP_KIND T` 打开了就会算），这里只控制写文件的频率。
调稀一点通常没问题——判断「组分之间平衡了没有」看的是趋势，每 10 步一行比每步一行小一个量级，曲线一样清楚；要看短时间的能量传递快慢就调到 1。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/PRINT/TEMP_KIND/EACH.html#CP2K_INPUT.MOTION.MD.PRINT.TEMP_KIND.EACH.MD>

## thermostat_region

*标识*：`thermostat_region`

`&MOTION/&MD/&THERMOSTAT REGION`：**一个热浴还是多个**，直接决定后面「热浴温度」打印出来是几列。
• **整个体系 GLOBAL**（CP2K 默认）：一个恒温器管所有自由度，体系一个温度；
• **每个分子 MOLECULE**：每个 molecule kind 一个恒温器。FIST 里给了 `&MOL_SET` 就是每种组分一个热浴；QS 里按 CP2K 划分的 molecule kind。混合物平衡快得多，但这时每个组分被**单独**拉到目标温度，「体系的温度」不再是一个数——写文章时要讲清楚；
• **每个自由度 MASSIVE**：massive Nosé-Hoover，每个自由度一个热浴，用于极难平衡或要求严格正则采样的场合，内存/开销明显更大。
`DEFINED`（自己划区域）和 `THERMAL`（按 `&THERMAL_REGION`）需要在输入里另写 `&DEFINE_REGION` 子块，本版没有区域编辑器，界面上不提供——确实需要时请在生成的输入里手动加，并把这里的 REGION 改成对应值。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/THERMOSTAT.html#CP2K_INPUT.MOTION.MD.THERMOSTAT.REGION>

## thermostat_temp_print

*标识*：`thermostat_temp_print`

`&MOTION/&MD/&THERMOSTAT/&PRINT/&TEMPERATURE`：把**每个热浴区域的温度**写出来，文件是 `<项目名>-1.PARTICLES.temp`（2026.2 里粒子恒温器的 label 是 `PARTICLES`，`thermostat_utils.F:print_thermostat_status` 用 `-1.<label>.temp`）。
内容：先一行「全局温度」，然后 `# TEMPERATURE REGIONS: n` + 每个区域的实际温度。
什么时候用：把上面的「热浴区域」设成**每个分子**时，用它看每个组分**各自被控到多少、有没有谁掉队**——同一次 NPT 里水可能 303 K、溶质 296 K，全局温度完全看不出来。
热浴区域是「整个体系」时，这个文件里的数就是全局温度，和 `-1.ener` 的 Temp[K] 列重复（校验器会提醒一句）。
只有 NVT/NPT 这类带恒温器的系综才有区域；NVE 或恒温器选 NONE 时它没有意义（界面上会变灰）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE.html>

## thermostat_temp_every

*标识*：`thermostat_temp_every`

`&MD/&THERMOSTAT/&PRINT/&TEMPERATURE/&EACH MD`：每多少步写一次各热浴区域的温度，默认 **10**。
看「组分之间有没有平衡」用 10 步够细了；要观察恒温器把某个组分拉回来的响应过程（几百 fs 内）就调到 1–2。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE/EACH.html#CP2K_INPUT.MOTION.MD.THERMOSTAT.PRINT.TEMPERATURE.EACH.MD>

## md_ramp

*标识*：`md_ramp`

**升温 / 降温**（CP2K 自带的退火开关）。CP2K 没有「目标温度」这种关键字，它只有**每步乘一个因子**，所以这里填目标温度 + 步数，工具替你算因子。
**这一栏只作用于单段**：它写进的是「保存 / 预览」的那一份输入（一次运行一条斜坡）。做了**温度程序**（下面的表）就**不需要勾它**——程序里每一段的目标温度和因子都由表自己算，这一栏的因子不会进程序（生成结果里会写明）；反过来程序也不会改动你单段那份输入。只有你还想另外留一份「一次运行直接升到 X K」的输入时才勾。
两条路线，**由系综决定**：
• `ENSEMBLE LANGEVIN` → `&MD TEMPERATURE_ANNEALING`：每步把 **Langevin 的目标温度**乘因子。这是「恒温器下控温升降」的正路，加热降温都稳。注意 Langevin 的摩擦 `GAMMA` CP2K 默认是 **0**（等于没有控温），工具会写一个 0.01 fs⁻¹；因子只在 Langevin 积分器里生效（`integrator.F`，SUBROUTINE langevin）。
• **没有粒子恒温器的系综**（NVE / NPE_*）→ `&MD ANNEALING`：每步把**速度**乘因子，温度大约按它的**平方**变。这不是控温，是直接加减动能（经典模拟退火的淬火），速度分布会被拉偏；`ANNEALING_CELL` 同理作用于晶胞。
• NVT / NPT_* **两条都不行**：`ANNEALING` 与恒温器同时开会被 CP2K 直接中止（"these two methods ... act one against the other"），`TEMPERATURE_ANNEALING` 又只在 Langevin 下有效。要升降温请把系综换成 LANGEVIN。
斜率是**几何（指数）**的：T_n = T_start × f^n。所以
• 温度模式：f = (T_end/T_start)^(1/步数)
• 速度模式：f = (T_end/T_start)^(1/(2×步数))
倍数不大时（比如 300↔600 K）和线性几乎没差别；要严格线性就得**分段接力**：每段一个固定 TEMPERATURE，用 `&EXT_RESTART` + `RESTART_TEMPERATURE_ANNEALING T` 把斜坡接起来（CP2K 自己的 `wat125_Tanneal(_restart).inp` 就是这么写的）。
**要「保持一段、再升、再保持、再降回来」这种多段曲线，用下面的「温度程序」表**：一次运行只有这一段斜坡，做不了保持、换斜率和掉头。
速率要当心：AIMD 里一般 1–10 K/ps 量级，越快越接近非平衡构型；校验器在「每步超过 1 K」时会提醒。含氢体系记得 0.5 fs 步长。
想看温度随时间怎么走：把「每个原子种类的温度」或「能量文件」勾上（`-1.ener` 里有 Temp[K] 列），跑完直接画。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.TEMPERATURE_ANNEALING>

## temperature_annealing

*标识*：`temperature_annealing`

`&MOTION/&MD/TEMPERATURE_ANNEALING`：**每步把 Langevin 的目标温度乘这个因子**（<1 降温、>1 升温）。0 = 不写这个关键字（CP2K 默认 1.0 = 关）。
**只在 `ENSEMBLE LANGEVIN` 下生效**：手册原文 "This scheme works only for the Langevin ensemble"，源码里那个乘法只写在 `SUBROUTINE langevin` 里——别的系综下它一声不响什么都不做（校验器会提醒）。
因子由上面的「升温 / 降温」帮你算；手填的话记住它是**每步**乘一次，要 N 步从 T1 到 T2 就是 `(T2/T1)^(1/N)`——写 1.5 这种值一个体系瞬间就飞了。
接力时想让斜坡接着走（而不是退回本次输入的 TEMPERATURE 重新开始），勾上「接力时接着这个斜坡走」。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.TEMPERATURE_ANNEALING>

## annealing

*标识*：`annealing`

`&MOTION/&MD/ANNEALING`：**每步把所有速度乘这个因子**（<1 降温、>1 升温；温度大约按它的**平方**变化）。0 = 不写（默认 1.0 = 关）。
它是**经典模拟退火/淬火**，不是恒温器：不看瞬时温度、不保证分布，只适合「把体系快速加热/冷却到某个状态」或 NVE 里调整温度。
**只能在没有粒子恒温器的系综里用**（NVE / NPE_*）：和 NVT/NPT 的恒温器同时开会被 CP2K 直接中止——两个控温手段互相打架（`simpar_methods.F`）。想在恒温器下升降温，用 LANGEVIN + `TEMPERATURE_ANNEALING`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.ANNEALING>

## annealing_cell

*标识*：`annealing_cell`

`&MOTION/&MD/ANNEALING_CELL`：同上，但乘的是**晶胞速度**（NPT/NPE 里描述盒子涨落的那套自由度）。0 = 不写。
**只有 NPE_I / NPE_F 会用它**（`integrator.F` 里只有这两个系综做晶胞退火），别的系综写了也不生效——校验器会提醒。降温压缩晶胞、升温膨胀时，它比改 `&BAROSTAT` 的温度更直接。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.ANNEALING_CELL>

## langevin_gamma

*标识*：`langevin_gamma`

`&MOTION/&MD/&LANGEVIN GAMMA`（单位 fs⁻¹）：**Langevin 的摩擦系数**。CP2K 自己的默认值是 **0**，就是既没有摩擦也没有随机力——那样等于 NVE，温度不受控、升温降温也不会生效，所以工具在 LANGEVIN 系综下**一定会写**一个值（默认 0.01 fs⁻¹ = 10 ps⁻¹）。
怎么选：耦合时间 1/GAMMA 要比你想观察的过程**长**、比温度斜坡**短**。
• 0.001–0.01 fs⁻¹（1–10 ps⁻¹）：常用范围，动力学扰动小、又能控温；
• 0.1 fs⁻¹（CP2K 的回归测试用它）：耦合很快，适合快速平衡/退火，但扩散系数这类动力学量会被压小。
CP2K 自己的 Langevin 例子分别用 0.001 / 0.01 / 0.1，可以照抄。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/LANGEVIN.html#CP2K_INPUT.MOTION.MD.LANGEVIN.GAMMA>

## temperature_program

*标识*：`temperature_program`

**温度程序**：把「保持 N 秒 → 升到 X K → 保持 → 再升 → 保持 → 降回 Y K」这种多段温度曲线一次写出来。
**为什么是一个表**：CP2K 的 `&MD` 只有一个 `TEMPERATURE` 加一个**每步乘的因子**，所以一次运行只能做一段几何斜坡——不能保持、不能换斜率、更不能掉头。多段曲线只能靠**分段接力**：一段一次运行，用 `&EXT_RESTART` 把坐标、速度、步数计数器和恒温器状态接过去（CP2K 自己的 `wat125_Tanneal(_restart).inp` 就是这么写的）。
表里一行一段：**起始温度 | 目标温度 | 时长(ps)**。起始=目标 就是「保持」，不等就是「升/降温」。点「加一段」时起始温度自动接上一段的终点（所以默认是连续的）；想跳变就手动改起始温度，工具会提醒那是一次跳变。
「生成温度程序」会写出：每段一个 `<段名>.inp`、`run_program.sh`（**串行**跑，后一段依赖前一段）、`program.ssv`（计划曲线）和 `program.txt`（程序原文，便于复现）。每段的 `PROJECT_NAME` 就是段名，所以 restart/.ener/轨迹都带段名前缀；脚本会在每段结束后把 CP2K 写出的 restart（名字可能带步数，例如 `stage01-1_40000.restart`）归一化成 `<段名>-1.restart` 供下一段读。
**段边界上是连续的**：坐标、速度（所以瞬时温度 T(t) 不断）、步数计数器、波函数，以及 NVT/NPT 的恒温器/压浴状态都接了过来，各段 `-1.ener` 的 `Temp[K]` 拼起来就是一条完整曲线。不随 restart 走的只有两样：**SCF 外推历史**（ASPC 的环形缓冲只在内存里，`&EXT_RESTART` 没有对应开关——每段第一步少一点加速、多几次 SCF 迭代，结果不受影响）和**随机数发生器**（只影响严格复现，要复现就把 `RESTART_RANDOMG T` 写进各段的 `&EXT_RESTART`）。所以分段的代价只是每段重新启动一次 CP2K（读 restart + 波函数、重建网格），相对几千个 MD 步可以忽略。
跑完怎么看：每段都有自己的 `<段名>-1.ener`（含 `Temp[K]` 列），按 `program.ssv` 的顺序把它们拼起来就是实际的温度曲线，和计划值对照即可。
两条路线与上面的「升温 / 降温」完全一致：LANGEVIN 段用 `TEMPERATURE_ANNEALING`（受控），没有恒温器的系综用 `ANNEALING`（缩放速度），NVT/NPT 两条都不行——所以程序里有变化段时，系综要选 LANGEVIN 或 NVE/NPE。「保持」段必须有恒温器（LANGEVIN 或 NVT/NPT），NVE 里它保不住温度。
第一段想接着上一次的平衡跑（而不是重新抽速度），就把「从上次续算」那栏填上。
**和上面「升温 / 降温」那一栏的关系**（两个都在这页上，容易混）：
• 上面那一栏是**单段**：一次运行里把温度从起点几何地推到终点（写 `TEMPERATURE_ANNEALING` 或 `ANNEALING`），管的是你「保存/预览」的那一份输入；
• 这张表是**多段**：保持 + 多次升降 + 掉头，一段一次运行、接力跑；
• 两个都填了**不会生成坏输入**，但**程序说了算**：每一段的目标温度和因子都由表（或程序文本）自己算，「保持」段一个退火关键字都不写——所以你上面那个因子不会进程序，生成结果里会写明这一点。反过来，程序也不会改动你单段那份输入。
• 例外：**晶胞速度退火**（`ANNEALING_CELL`）不在这张表里，它是**程序级**的——设了每一段都会带上（含保持段），而且只有 `NPE_I`/`NPE_F` 会真的用它。

## program_launch

*标识*：`program_launch`

`run_program.sh` 用什么命令跑**每一段**（机器相关的那一半，不是 CP2K 的输入参数）：
• **mpirun** —— 启动器，默认 `mpirun`；留空就**不用 mpirun**、直接单进程跑 `cp2k`（本机调试用）。
• **CP2K 可执行** —— 默认 `cp2k.psmp`（MPI+OpenMP 版）。装的是别的就改：
  `cp2k.psmp` = MPI+OpenMP（集群常用）；`cp2k.popt` = 纯 MPI（官方截断能教程用它）；`cp2k.ssmp` = OpenMP 单机多线程；`cp2k.sopt` = 串行。
• **-np N** —— 每段用几个 MPI 进程（默认 **64**）。
• **OMP N** —— 每个进程几个 OpenMP 线程（默认 **2**，脚本里写成 `export OMP_NUM_THREADS=2`）。
所以并行度 ≈ N × OMP，别超过节点上的物理核数：64 × 2 = 128 线程通常就意味着一台双路机器；纯 MPI 就把 OMP 设成 1。**段与段仍然是串行的**（后一段要读前一段的 restart 和波函数），这里调的是**一段内部**的并行度。
`--bind-to none` 这类额外参数脚本里留了 `mpirun_extra` 变量：它是 OpenMPI 专有选项（官方 CUTOFF 脚本在同时起多个作业时用），MPICH/Intel MPI 不认——工具默认不写。

## program_resume

*标识*：`program_resume`

**跳过已经跑完的段**（断点续跑）。判据是 `<段名>-1.restart` 比 `<段名>.inp` 新：那个文件只有一段**真的跑完**才会出现（由 `run_program.sh` 在段末复制生成），被墙钟砍掉的那些段不会留下它——所以被砍的段会重跑，而前面跑完的段直接跳过。
为什么有用：一条 60 ps 的温度程序往往一次排队跑不完；接着跑时不必从头再来。
**一旦有一段重跑，它后面的段都会跟着重跑**（那些 restart 属于旧链条了），所以改了某一段的输入，按顺序从那一档重新接上即可。
想彻底从头跑：把这个勾去掉（脚本里 `resume=false`），或者删掉那些 `<段名>-1.restart`。

## restart_temperature_annealing

*标识*：`restart_temperature_annealing`

`&EXT_RESTART/RESTART_TEMPERATURE_ANNEALING T`：**接力的温度斜坡从 restart 里存下的目标温度接着走**，而不是退回本次输入里写的 `TEMPERATURE`。
为什么需要它：升温/降温会把「当前目标温度」写进 restart 文件，下一次启动时CP2K 默认忽略它、按新输入里的 TEMPERATURE 重新开始——多段接力就会每段都回到起点。勾上它，`input_cp2k_check.F` 才会把 `MOTION%MD%TEMPERATURE` 从老文件搬过来。
只有「接力 + 真的开了温度斜坡（TEMPERATURE_ANNEALING）」时才有意义；没给因子时 CP2K 自己会警告一句（校验器也会）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/EXT_RESTART.html#CP2K_INPUT.EXT_RESTART.RESTART_TEMPERATURE_ANNEALING>

## metadyn

*标识*：`metadyn`

**元动力学（metadynamics）**：一边跑 MD，一边沿着几个「集体变量」（CV）不断撒高斯丘陵，把体系从已探索过的区域推出去，最后用丘陵的累积反推自由能面。
CP2K 把这件事分在两处写：
• `&FORCE_EVAL/&SUBSYS/&COLVAR` —— **CV 的定义**（距离、键角、二面角、配位数、组合 CV、到一条参考路径的距离/RMSD）；
• `&MOTION/&FREE_ENERGY/&METADYN` —— **偏置怎么长**（丘陵高度 `WW`、间隔 `NT_HILLS`、井温参数、每个 CV 的 `&METAVAR`，以及打印）。
这一块就在 **MD 页上**、默认收起来（点箭头展开）：这两段只有 `RUN_TYPE MD` 才会被 CP2K 读，放到别处只会变成一排用不上的控件。勾上「启用元动力学」它也会自己展开；生成时两段一起写进右侧预览。
两条路线二选一：
• **CP2K 原生 `&METADYN`**（默认）—— CV 和丘陵都由 CP2K 自己算，写出来的就是上面那两个段，CP2K 单独跑就行；
• **PLUMED 驱动**（`USE_PLUMED T`）—— 偏置交给外部 PLUMED 库，CV 和丘陵都写在 PLUMED 的输入文件里，CP2K 只提供力和坐标。这条路线下本块的 CV/丘陵控件不生效（工具会在输入里写死 `USE_PLUMED T` 和 `PLUMED_INPUT_FILE`），而且要自己准备编好 PLUMED 的 CP2K。
**几件容易踩的事**：
• `DO_HILLS` 的 CP2K 默认是 **F**（不撒丘陵）——不写它就等于只有约束墙、没有偏置。工具默认写 T。
• `WW` 是**丘陵高度**（Hartree），CP2K 自己的默认 0.1 大得离谱；常用起点 0.5–2 mHartree（`1.0e-3`）。
• `NT_HILLS` 是**两次撒丘陵之间的 MD 步数**，不是丘陵个数。
• `&METAVAR COLVAR n` 里的 n 是 **1 起算的 CV 序号**，按 `&COLVAR` 在 `&SUBSYS` 里出现的先后数——所以 CV 表的行序就是序号。
• 要接着跑、要画自由能面，都必须有 `-1.hills` 文件（本块默认写它）。
**接着跑**有两条路：直接 `cp2k -i <项目>-1.restart` 跑（hills 就在那个文件里），或者生成一份新输入、在「生成输入」页的第 6 节里把续算文件填上并勾「取哪些量」里的 **metadynamics 的 hills**（`&EXT_RESTART RESTART_METADYNAMICS T`，它会把接力文件里的整段丘陵历史搬过来）。后者才是接力到新参数（换温度、换丘陵高度）时该用的方式。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html>

## metadyn_route

*标识*：`metadyn_route`

偏置由谁算：
• **CP2K 原生（`&METADYN`）** —— 工具把 CV 写进 `&SUBSYS/&COLVAR`、把丘陵参数写进 `&FREE_ENERGY/&METADYN`。这是最省事的一条：一个输入、一个 CP2K 可执行文件，`-1.hills` 和 `-1.colvar` 直接就是你要的结果。
• **PLUMED 驱动（`USE_PLUMED T`）** —— 偏置由外部 PLUMED 算，CV 定义和丘陵参数都写在 PLUMED 的输入文件（通常 `plumed.dat`）里；CP2K 只把每步的坐标/力交给它。这条路线下 CP2K **只从 `&METADYN` 读一个文件名**（`metadynamics_utils.F`："only get the file name and read nothing else"），所以工具把本块的 CV 表/丘陵参数**写成 `plumed.dat`**：预览切到那一页就能看到、「保存」就能存下（`--meta-plumed-draft` 是命令行的同一个东西）。`&COLVAR`/`&METAVAR` 不会进 CP2K 输入。
什么时候选 PLUMED：需要 CP2K 没有的 CV（配位数以外的、基于接触矩阵的、路径 CV 的多种变体），或者要在同一个输入里做 well-tempered + 多walker + 其它 PLUMED 功能。
**两条路线的 CV 是不是同一套规则？——概念同源，语法/单位/默认值不同，有几处语义也不一样**（以下都对着源码核过）：
• **语法** —— CP2K 是分节的 `&COLVAR` + `&DISTANCE ATOMS 1 2`；PLUMED 是一行一个动作 `d1: DISTANCE ATOMS=1,2`，后面的动作用标签引用它。
• **单位** —— CP2K 内部是 bohr / rad / Hartree（所以 `R0` 的默认 3.0 其实是 **3.0 bohr ≈ 1.59 Å**，工具写文件时用 `[angstrom]` 让它按 Å 读）；PLUMED 侧 CP2K 会设成 **nm / kJ/mol / ps**（`metadynamics.F:174-176`），工具生成的草稿已按此换算。
• **配位数** —— 切换函数**完全相同**（`(1-(r/r₀)^NN)/(1-(r/r₀)^ND)`，`colvar_methods.F:4249-4253`；PLUMED 同式），但参数名不同（CP2K `ND` = PLUMED `MM`）、`R_0` 在 PLUMED 里**必填**（CP2K 有默认），而且 **CP2K 会除以中心原子数**（`colvar_methods.F:4312`：`ss = ncoord/n_atoms_from`，是平均值）而 PLUMED 是**求和**——中心原子不止一个时，同一个设置两边差一个因子。工具的 `plumed.dat` 草稿会自动补一层 `COMBINE COEFFICIENTS=1/N` 把两者对齐。
• **丘陵宽度** —— CP2K 的 `&METAVAR SCALE`（别名 `WIDTH`）和 PLUMED 的 `METAD SIGMA` 是同一个量；但 CP2K 默认 0 = 「无限宽」（不产生任何力），PLUMED 的 `SIGMA` 是必填项。
• **井温** —— 两边都用体系温度 T：CP2K 取 `&MD TEMPERATURE`（`md_run.F:400`），PLUMED 的 `TEMP` 也是这个意思。但 CP2K 的关键字是 `DELTA_T`，PLUMED 的是 `BIASFACTOR=(T+ΔT)/ΔT`；CP2K 自己的 `WTGAMMA` 用的是另一个约定 `1+ΔT/T`，别混。
• **数值不完全等价的地方** —— 组合 CV 的表达式语言不同（CP2K `FUNCTION` 用 `CV1/CV2` + `PARAMETERS/VALUES`；PLUMED 是 `COMBINE`（线性）/`MATHEVAL`/`CUSTOM`），路径/RMSD 的参考构型要你自己拼成一个文件，`REFLECTIVE`/`GAUSSIAN` 墙没有直译，`LAGRANGE`/`MASS`/`GAMMA`/`MIN_DISP`/`HILL_TAIL_CUTOFF` 在 PLUMED 里没有对应设置（工具会把这些留成注释）。
代价：必须用**链接了 PLUMED 的 CP2K**（编译时的 `-D__PLUMED2`，否则 CP2K 直接中止），运行时还要能找到 PLUMED 库。工具生成的是一份**初稿**：距离/键角/二面角/配位数是一一对应的，组合 CV 的表达式、路径/RMSD 的参考构型、以及反射/高斯墙要你自己补（文件里用注释标出来了）；它也没法检查你手上那份已有的 `plumed.dat`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.USE_PLUMED>

## meta_plumed_file

*标识*：`meta_plumed_file`

`PLUMED_INPUT_FILE`：PLUMED 的输入文件（默认 `plumed.dat`）。
**相对路径是相对 CP2K 进程的工作目录**——也就是你提交作业的那个目录，不是输入文件所在目录。用 `run_program.sh` 跑时它就放在输出目录里，把 `plumed.dat` 一起放到那个目录最省事。
**这个文件的内容可以由工具生成**：PLUMED 路线下，本块的 CV 表 + 丘陵参数会写成一份 `plumed.dat` 初稿（右侧预览切到那一页 / 「保存」时选它，命令行是 `--meta-plumed-draft`）。生成的是初稿——距离/键角/二面角/配位数一一对应，组合表达式、路径/RMSD 的参考构型、反射/高斯墙会留成注释让你补。
两种方式二选一：把它指向你手上已有的 `plumed.dat`（工具只写这一行、不看内容），或者用工具生成一份初稿再改。原子序号是 1 起算、顺序 = `&COORD` 行序，和 CP2K 侧的 `&COLVAR` 一致。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.PLUMED_INPUT_FILE>

## meta_do_hills

*标识*：`meta_do_hills`

`DO_HILLS`：**这次运行到底撒不撒新的丘陵**。
**CP2K 自己的默认是 F（不撒）**——不写这一句，`&METADYN` 就只是一堆约束墙和打印设置，跑出来的自由能面永远是平的。工具默认写 T，只有确实想「只加偏置、不新增丘陵」（比如续算时已经有一批丘陵，只想让它们继续起作用）时才取消勾选。
注意它和 `NT_HILLS` 是两件事：`DO_HILLS F` 时连丘陵的高度/间隔都不写。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.DO_HILLS>

## meta_hill_height

*标识*：`meta_hill_height`

`WW`：**每次撒下去的高斯丘陵的高度**（Hartree）。
这是元动力学最需要试的参数：
• 太大 —— 自由能面被填得粗糙、误差大，还容易把体系推到没有意义的构型；
• 太小 —— 丘陵推不动体系，跑很久也没走过一个垒。
常用起点 0.5–2 mHartree（`5.0e-4`–`2.0e-3`），大约是 kT 的 1/10 （300 K 时 kT ≈ 0.95 mHartree）。体系越软（溶液、柔性分子）越小。
**CP2K 自己的默认是 0.1 Hartree**，比合理值大两个数量级——所以工具默认写 `1.0e-3`（1 mHartree），不要留空。
丘陵的**宽度**是 `&METAVAR` 里的 `SCALE`（别名 `WIDTH`，见「偏置项」那一格的说明），本工具用上面那个「默认丘陵宽度 σ」字段替你写进每个 `&METAVAR`。**CP2K 默认的宽度是 0，而 0 被特判成「无限宽」= 这一维不加任何偏置力**，所以宽度不是可选项。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.WW>

## meta_nt_hills

*标识*：`meta_nt_hills`

`NT_HILLS`：**两次撒丘陵之间隔多少个 MD 步**。
名字里的 NT 容易读成「丘陵个数」，其实是**间隔**——手册原文是 "Number of steps between two hills"。
怎么选：让体系在两次撒丘陵之间有机会真正动一动（CV 至少挪一个丘陵宽度的量级）。太小 → 丘陵之间高度相关，等于白填；太大 → 探索慢。手册里这是「两次丘陵之间的**最大** MD 步间隔」，CP2K 自己的默认是 **30**，AIMD 里常用 50–500，配合 `MIN_DISP` 用更稳。
一次 MD 跑的总步数 / `NT_HILLS` = 这次会撒多少个丘陵，先估一下够不够翻过垒。
**负数**（如 -1）有专门含义：不再撒任何新丘陵，只用接力文件带进来的那些（配合 `&EXT_RESTART RESTART_METADYNAMICS T`，等于给体系加一个固定的偏置势）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.NT_HILLS>

## meta_sigma

*标识*：`meta_sigma`

**高斯丘陵的宽度 σ**。这是两条路线**同一个**量：CP2K 原生的 `&METAVAR SCALE`（别名 `WIDTH`）和 PLUMED 的 `METAD SIGMA` 都是它——偏置写成 `V = WW·Σⱼ Πₖ exp(-½·((sₖ-sₖ(tⱼ))/σₖ)²)`（`metadynamics.F:1023-1031` 把 `SCALE` 读进 `metavar%delta_s`）。
所以这一格在两条路线下都有效：
• **原生路线** —— 工具把它写成每个 `&METAVAR` 的 `SCALE`；
• **PLUMED 路线** —— 写成 `METAD` 那一行的 `SIGMA=...`。
**为什么必须填**：CP2K 默认的 `SCALE` 是 **0**，而 0 被特判成「无限宽」（源码里写着 `trick: scale = 0 is interpreted as infinitely wide Gaussian hill`）——那一维既不产生偏置也不产生力。漏了宽度，整个 `&METADYN` 就是白跑。PLUMED 那边 `SIGMA` 是**必填项**（没有默认值），工具留空时会写 `0.1` 并在文件里标一行「这是占位，自己改」。
**单位**：跟 CV 走 —— 距离类这一格填 **Å**（工具写进文件时带 `[angstrom]`，给 PLUMED 的那份再换算成 nm）、角度类填 **rad**、配位数这类无量纲 CV 就是原值。同一个数字用在两类 CV 上时工具会警告（单位对不上）。
**怎么选**：先跑一小段看 CV 的涨落，取涨落标准差的 1/3–1/2。距离类常见 0.1–0.3 Å；太小 → 丘陵之间留缝、自由能面毛糙、要撒的丘陵多；太大 → 细节被抹平。单个 CV 想单独设宽度就用「加 &METAVAR」里那一格 `SCALE`（它优先于这里）。

## meta_wt

*标识*：`meta_wt`

`WELL_TEMPERED T`：**井温元动力学（WT-MetaD）**。
普通元动力学会一直用同一个高度撒丘陵，跑久了会「过填」——把自由能面填平甚至填反。井温方案让丘陵高度按已积累的偏置指数衰减：越是被填过的区域，新丘陵越矮，最后收敛到一个稳定的偏置势，**能给出自由能的估计值而不只是「跑出去了」**。
要配套两个数：
• `DELTA_T` —— 偏置因子 (T+ΔT)/ΔT 里的 ΔT，决定最终填多高；
• **参考温度 T 取的是「生成输入」页上 `&MD TEMPERATURE` 那个值**，不是本块的 `TEMPERATURE`（源码 `md_run.F:400`：`wttemperature = simpar%temp_ext`）。所以井温不要求你在本块填温度，MD 温度是几就是几；也**别把 MD 温度设成 0**（CP2K 会拿它做除法）。
本块的 `TEMPERATURE` 是**另一件事**：扩展拉格朗日/Langevin 方案里给 CV 自己的恒温温度（手册原话 "if a Lagrangian scheme is used the temperature for the collective variables is specified"）。没开 `LAGRANGE` 时填它不起作用，工具会提醒。
ΔT 怎么选：ΔT 越大偏置越接近普通元动力学（探索强、收敛差），越小填得越浅。常见做法是 ΔT 取 **5–10 倍模拟温度**（比如 330 K 的体系取 2000–4000 K）；想更保守可以取到 20 倍。
长时间采样、要定量自由能，就用它；只想快速把体系推过垒，用它也行——代价是同样的时间内探索得慢一些。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.WELL_TEMPERED>

## meta_delta_t

*标识*：`meta_delta_t`

`DELTA_T`（单位 K）：井温元动力学的**偏置因子**参数，实际使用的高度缩放是 `exp(-偏置/(kB·(T+ΔT)))`。
选法：ΔT = **5–10 × 模拟温度** 是最常见的范围（330 K 的体系 → 2000–4000 K）；
• ΔT → ∞ 退化成普通元动力学（丘陵不再变矮）；
• ΔT 越小，最终填的偏置越浅、收敛越快但探索越弱。
手册里没有默认值，**开了 `WELL_TEMPERED` 不写 DELTA_T 是没有意义的**（工具会当场警告）。CP2K 还提供另一种写法 `WTGAMMA`（γ），但它的定义是 `1+ΔT/T`，和 PLUMED 的 `BIASFACTOR=(T+ΔT)/ΔT` **不是同一个数**（两者互为 γ/(γ−1)）；一起写时 CP2K 会检查 `DELTA_T=(WTGAMMA-1)·T`，本工具只写 DELTA_T。
自由能面的重加权依赖这个数：分析 `-1.hills` 时要用同一个 ΔT，所以改它等于换了一次实验，别在接力中途改。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.DELTA_T>

## meta_min_disp

*标识*：`meta_min_disp`

`MIN_DISP`：**CV 至少要移动这么多，才允许撒下一个丘陵**。
它滤掉的是「CV 在原地抖」时撒出来的一堆几乎重合的丘陵——那些丘陵对自由能面毫无贡献，只是把偏置白白堆高。设了它以后，撒丘陵的节奏由 CV 的实际移动决定，而不是死板的步数间隔。
取值量级：**CP2K 这个关键字没有单位注解，所以它用的是 CV 的内部单位**——距离类 CV 是 **bohr**（1 Å = 1.889 bohr！别按 Å 填），角度类是 rad，配位数是纯数。按 Å 填会大约差一倍（数值偏大≈1.89 倍，等于要求 CV 移动得更多，撒的丘陵变少）。经验上取丘陵宽度的 1/3–1/2 比较稳。**留空就是不启用**（CP2K 默认 -1 = 关）。
和 `NT_HILLS` 是「与」的关系：两个条件都满足才撒。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.MIN_DISP>

## meta_tail_cutoff

*标识*：`meta_tail_cutoff`

`HILL_TAIL_CUTOFF`：给高斯丘陵的尾巴加一个截断，让远处那个「永远不为零」的指数尾巴更快归零。
为什么需要：所有丘陵的高斯尾部叠加起来是一个几乎恒定的背景，在 CV 空间的大范围内缓慢地推体系——那不是真实的偏置，会污染自由能面。截断后丘陵只在有限范围内起作用。
取值：以丘陵宽度的倍数计，常用 **2–3**（留空 = 不截断，CP2K 默认）。
注意它会改变丘陵的解析形式，所以 `-1.hills` 的重加权要一致地处理；同一个体系前后几次运行最好用同一个值。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.HILL_TAIL_CUTOFF>

## meta_lagrange

*标识*：`meta_lagrange`

`LAGRANGE T`：把 CV 当作**独立的动力学变量**（扩展拉格朗日 / extended Lagrangian）来积分，而不是每一步直接从坐标算出来。
好处：CV 的演化变得平滑，配位数这类对坐标非常敏感的 CV 也能稳定地跑（直接算的 CV 会因为相邻原子进出配位球而跳变）。
代价：**每个 CV 都要给质量 `MASS` 和（一般也给）摩擦 `GAMMA`**，它们是这个虚拟粒子的惯性/阻尼——`MASS` 太大的 CV 跟不上体系，太小会抖；`GAMMA` 控制 CV 自己的温度控制。不给 MASS 的扩展拉格朗日没有意义（工具会警告）。
`LAMBDA` 是质量加权的耦合强度（&METAVAR 里），常用做法是先跑短的一段确认 CV 能跟上真实值，再开始撒丘陵。
**`&METAVAR GAMMA` 需要配套勾上「CV 自己用 Langevin 积分」（`LANGEVIN T`）**——那是给这些虚拟 CV 的恒温器；不写 `LANGEVIN T` 时 CP2K 不读 GAMMA（工具会警告）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN.html#CP2K_INPUT.MOTION.FREE_ENERGY.METADYN.LAGRANGE>

## meta_print

*标识*：`meta_print`

写不写元动力学的两个结果文件（都在输出目录里，文件名前面带项目名）。**只对 CP2K 原生 `&METADYN` 生效**——PLUMED 路线下 CP2K 只从 `&METADYN` 读一个文件名（`metadynamics_utils.F`："only get the file name and read nothing else"），丘陵/colvar 文件由 plumed.dat 里的 `FILE=` 决定。
• **`-1.colvar`**（`&PRINT/&COLVAR`）—— CV 随时间的变化。想看「体系有没有沿着 CV 走过去、有没有卡住」就看它；扩展拉格朗日方案下它还会多几列（CV 的真实值、虚拟值、受力、丘陵/墙的势能、CV 自己的温度）。
• **`-1.hills`**（`&PRINT/&HILLS`）—— 撒过的每一个丘陵：CV 位置、宽度、高度。**接着跑和画自由能面全靠它**，丢了就只能重跑。
工具默认两个都写。CP2K 自己靠 `PRINT_LEVEL` 决定这两份文件的默认开关（HILLS 属于 `high` 级），所以显式写出来最保险。
想省磁盘只留 hills：把 `-1.colvar` 关掉——它每步都在写，长跑会很大。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN/PRINT.html>

## meta_print_every

*标识*：`meta_print_every`

`&PRINT/&COLVAR`（和 `&HILLS`）的 `&EACH MD n`：**每多少个 MD 步写一行**。
`1` = 每一步都写（默认）：曲线最密，文件也最大（几万步 × 几列文本，通常还能接受）。长跑（百万步）就把它调到 10–100。
注意丘陵文件按它的**语义**应该是「每撒一个丘陵记一条」，所以这里写大于 1 的间隔只影响记录频率；丘陵本身的撒放节奏由 `NT_HILLS` 决定，两者不要混。
画自由能面时，丘陵是否齐全比行数密集更重要：`-1.hills` 不能缺行。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/FREE_ENERGY/METADYN/PRINT/COLVAR.html>

## meta_cv

*标识*：`meta_cv`

**集体变量（CV）**：把体系的高维运动压成的一两个「反应坐标」，元动力学就是沿着它们撒丘陵。每个 CV 写成 `&SUBSYS` 里的一个 `&COLVAR` 块，**表里的第 1 行就是 `&METAVAR COLVAR 1` 说的那个 CV**。
支持的类型（CP2K 2026.2）：
• **距离 `&DISTANCE`** —— 两个原子间距（成键/断键、离子对、吸附距离）；
• **键角 `&ANGLE`** —— 三个原子的夹角；
• **二面角 `&TORSION`** —— 四个原子的二面角（构象变化、旋转）；
• **配位数 `&COORDINATION`** —— 中心原子周围某类原子的个数（用切换函数平滑计数，`R0` 是切换半径，`NN`/`ND` 是陡度）；
• **组合 `&COMBINE_COLVAR`** —— 把几个 CV 用表达式合成一个（`FUNCTION` 里用 `CV1`、`CV2`…指代嵌套的那些 CV）；
• **路径 `&DISTANCE_FROM_PATH`** —— 和一组参考构型的距离/RMSD（比如一条反应路径上的若干帧），适合用 RMSD 或路径坐标描述的反应。
原子序号都是 **1 起算**，按 `&COORD` 里的行号；扩展名带真空层不影响序号。
选 CV 的经验：
• 一个 CV 至少要能区分「初态」和「末态」，而且沿着它走要翻过那个垒；
• 先跑一小段短 MD 看 CV 的取值范围（`-1.colvar`），再定丘陵高度和墙的位置；
• 两个 CV 通常够了；再多就该考虑 PLUMED 或换个描述方式。
**弹窗里那些格子怎么填**（同一张表按类型只显示相关的几行）：
• **原子序号** —— 1 起算，按 `&COORD` 的行序（加真空层不改序号）；距离 2 个、键角 3 个（中间那个是顶点）、二面角 4 个。
• **KINDS_FROM / KINDS_TO** —— 配位数专用：可以写元素名（`O` 和 `H`）而不是原子号；同一类原子有多个时都算进去（PLUMED 路线的 `plumed.dat` 里会按你载入的结构展开成原子组）。
• **ATOMS_FROM / ATOMS_TO** —— 同样的意思，但直接给原子号（想只数某几个原子时用）。
• **R0 / NN / ND** —— 配位数的切换函数：`R0` 是切换半径，这一格按 **Å** 填（工具写文件时带 `[angstrom]`；**注意 CP2K 的默认值是 3.0 bohr ≈ 1.59 Å**，所以留空和填 3.0 不是一回事）；`NN`/`ND` 是分子/分母里的指数（越大越「硬」，CP2K 默认 6/12，PLUMED 里同一个指数叫 `MM`）。
• **FUNCTION / VARIABLES / PARAMETERS / VALUES** —— 组合 CV：表达式里用 `CV1`、`CV2`… 指代下面嵌套的子 CV，`PARAMETERS`/`VALUES` 是表达式里的常数（如 `P=4.0`）。
• **&FRAME 参考构型** —— 路径/RMSD CV 的参考帧，一个文件一帧；**文件的原子顺序必须和你的体系一致**。旁边那几个开关只对这类 CV 有意义。
• **SUBSET_TYPE / LAMBDA / RMSD / DISTANCES_RMSD** —— 路径 CV 的度量方式（RMSD 需要参考帧；LAMBDA 是路径积分的宽度）。
**PLUMED 路线下这张表会变成 `plumed.dat`**：距离/键角/二面角/配位数与 PLUMED 的 CV 一一对应（单位换算成 nm，配位数会自动补上 CP2K 那个 1/N 平均化），组合 CV 只把子 CV 写出来、表达式留成注释，路径/RMSD 给一行 `PATHMSD`/`RMSD` 占位（参考构型要你自己拼成一个文件）。两条路线的差异（单位、参数名、默认值、配位数是否取平均）在「偏置由谁算」那一项的说明里逐条列了。
**这个工具不生成的 CV**（2026.2 手册里还有一批更专用的）：`&POPULATION`、`&GYRATION_RADIUS`、`&CONDITIONED_DISTANCE`、`&BOND_ROTATION`、`&RING_PUCKERING`、`&HBP`、`&REACTION_PATH`、`&DISTANCE_POINT_PLANE`、`&DISTANCE_FUNCTION`、`&XYZ_DIAG`、`&U`…读一份用了它们的输入时，「读参数」会把它们列在「没读」清单里（不会悄悄丢）。要用这些 CV 就得自己写输入（工具生成的是**整份**输入，手写的段不会被保留），或者改成 PLUMED 路线。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/COLVAR.html>

## meta_cv_kind

*标识*：`meta_cv_kind`

CV 的类型。选哪一个取决于你想让体系越过的是什么：
• **距离** —— 最直观，比如「这个 O 到那个 H 的距离」。注意距离类 CV 在原子跑远时没有上界（要配墙）。
• **键角** —— 三个原子，适合弯曲/翻转类运动。
• **二面角** —— 四个原子，构象变化的标准选择；注意周期性（0/360° 附近会跳变，必要时配墙或用组合 CV）。
• **配位数** —— 「某个原子周围有几个某类原子」。它天然平滑、有上界，特别适合溶解/配位/相变这类过程（比如「这个离子周围有几个水氧」）。`R0` 选在成键和不成键距离之间（比如 O–H 取 1.2–1.3 Å）。
• **组合** —— 用表达式把上面的 CV 拼起来（例如把两个距离之差作为「质子转移」坐标）。表达式里 `CV1`、`CV2`… 按子 CV 的顺序编号。
• **路径** —— 和一组参考构型比 RMSD/距离，适合已经知道一条大致路径（NEB 的结果、实验构型）的情形；参考构型用 `&FRAME/&COORD_FILE_NAME` 一个个列出，文件的原子顺序必须和体系一致。

## meta_metavar

*标识*：`meta_metavar`

`&METAVAR`：**偏置作用在哪个 CV 上、怎么作用**。
• `COLVAR` —— 指向第几个 `&COLVAR`（1 起算，按 CV 表的行序）。指到不存在的序号时 CP2K 会读不到 CV，工具会当场警告。
• `SCALE`（别名 `WIDTH`）—— **这一维高斯的宽度 σ**，不是「缩放因子」（手册描述里写的 "scale factor" 容易误解，但公式`WW·Σⱼ Πₖ exp(-½·((sₖ-sₖ(tⱼ))/SCALEₖ)²)` 和源码 `metavar%delta_s` 都是宽度）。**CP2K 的默认是 0，而 0 表示「无限宽」= 这一维不加偏置也不加力**，所以每个 `&METAVAR` 都该有宽度：本格留空时工具会用上面「默认丘陵宽度 σ」那一格的值替你写上；两处都没填，工具会警告「等于白跑」。单位跟 CV 走（距离类在界面上填 Å，写进文件时带 `[angstrom]`；角度是 rad；配位数是纯数）。PLUMED 路线的同一个量叫 `SIGMA`。
• `MASS`、`GAMMA`、`LAMBDA` —— 只在开了**扩展拉格朗日**（`LAGRANGE T`）时有意义：CV 作为独立变量积分，`MASS` 是它的虚拟质量，`GAMMA` 是它的摩擦（相当于给 CV 一个 Langevin 恒温器，需要 `&METADYN LANGEVIN T`），`LAMBDA` 是它和真实值之间的耦合强度。PLUMED 没有对应设置。
• **墙（`&WALL`）** —— 把 CV 挡在一个范围内，防止它跑到没意义的区域（比如原子解离到很远、二面角转过 360°）。`POSITION` 是墙的位置，`TYPE` 决定形状：
  – `QUADRATIC`：`K·(CV-POS)²`，最常用；`K` 越大越硬；
  – `QUARTIC`：手册写的是 `K·(CV-POS∓K^-1/4)⁴`。**2026.2 的源码里这一支能量写成 `K·Δ·Δ⁴`（即 Δ⁵）而力是 `4K·Δ³`（`metadynamics_utils.F:928-929`），两者自相矛盾**，不建议用它；
  – `REFLECTIVE`：越界就把 CV 的速度反过来（不需要 `K`）；
  – `GAUSSIAN`：源码是 `WW·exp(-½·((CV-POS)/SIGMA)²)`（描述文字里漏了那个 ½）。
  `DIRECTION` 选 `WALL_PLUS`（墙在位置的**大**值一侧、挡住往大走）或 `WALL_MINUS`（反过来）。**墙的位置要选在物理上有意义的边界**（比如「键断了」的距离），太近会限制探索，太远等于没有。
  `POSITION` 和 `GAUSSIAN` 的 `SIGMA` 单位同样跟 CV 走（距离类界面填 Å），`K`/`WW` 是 Hartree。
工具默认给每个 CV 自动配一个 `&METAVAR`（「每个 CV 一个（自动）」），一个 CV 一个偏置项通常就是你要的。

## meta_wall

*标识*：`meta_wall`

`&METAVAR/&WALL`：给这个 CV 加一道**约束墙**，把它挡在一个范围内。
为什么常常要加：元动力学会主动把 CV 往没去过的方向推，而很多 CV 在物理上是有边界的——原子会解离、二面角会绕圈、配位数不会小于 0。没有墙，丘陵会把体系推到完全不物理的区域，自由能面在那一段也没意义。
各字段：
• `POSITION` —— 墙的位置。**单位跟 CV 走**：CP2K 声明它是 `internal_cp2k`，也就是长度类 CV 的 bohr；界面上按 Å 填、生成时带 `[angstrom]` （CP2K 自己换算），角度是 rad、配位数是纯数。
• `TYPE` —— 墙的形状（QUADRATIC 二次 / QUARTIC 四次 / REFLECTIVE 反弹 / GAUSSIAN 高斯软墙）。
• `DIRECTION` —— `WALL_PLUS` 表示墙从位置向**大**值方向延伸（挡住 CV 变大），`WALL_MINUS` 反之。**方向搞反等于墙加在了错误的一侧**。
• `K` —— 二次/四次墙的力常数，单位 **Hartree / CV单位²**（CV 是距离时就是 Hartree/bohr²，界面上虽然只填数字，PLUMED 草稿里会换算成 kJ/mol/nm²）。`GAUSSIAN` 墙用 `SIGMA`（宽度，单位跟 CV）和 `WW`（高度，Hartree），没有 `K`。
两个容易踩的坑（都写在源码里）：`QUARTIC` 的能量与力在 2026.2 里不一致（`metadynamics_utils.F:928-929`），不建议用；`GAUSSIAN` 墙的指数里有 ½（`WW·exp(-½·((CV-POS)/σ)²)`），和手册描述写的不是一字不差。
墙的位置可以从 `-1.colvar` 里的取值范围来定：先不带墙跑一小段，看 CV 正常涨落到哪里，再把墙放在比它略远一点的地方。
一个 CV 上可以放多道墙（比如二面角的 0° 和 360° 各一道），但每一道都要写成一个自己的 `&WALL`。

## plumed_cv

*标识*：`plumed_cv`

**PLUMED 专用表**：这一张写的不是 CP2K 的 `&COLVAR`，而是 `plumed.dat` 里的动作行 `标签: 动作 关键字=值 …`。
**为什么有两套表**：PLUMED 的 CV 语言和 CP2K 的不是一套规则——单位是 **nm/kJ/mol**（CP2K 内部是 bohr/Hartree）、配位数的指数叫 `MM` 而不是 `ND`、`R_0` 是必填项、很多动作是「多分量」的（标量要写成 `标签.mean`）。与其把一张表来回翻译（那正是出错的地方），不如各写各的：选「CP2K 原生」显示上面那套表，选「PLUMED 驱动」显示这一套。
**这一套不做任何翻译**：你填的参数原样进文件，所以拼错了 PLUMED 会直接告诉你哪一行不认。工具只做它能核对的检查：
• 标签是否合法、是否重复；
• 手册里标为必填的关键字有没有给（比如 `COORDINATION` 的 `R_0`、`AROUND`/`CAVITY` 的 `SIGMA`、`MATHEVAL` 的 `FUNC`）；
• `ARG=`/`DATA=`/`MATRIX=` 里引用的标签是不是前面定义过；
• 多分量动作有没有选「分量」（不然 `METAD ARG=` 用不了它）；
• 原子序号有没有超出体系范围；
• 动作所在模块是否默认不编译（`CONTACT_MATRIX` 这类在 **adjmat** 里，要 `./configure --enable-modules=adjmat`）。
动作清单和每个动作的示例参数都取自 PLUMED 2.9 手册里对应的页面。表里没有的动作（`PCAVARS`、`PIV`、`OPES_*`…）走「自由行」，一样能用。

## plumed_units

*标识*：`plumed_units`

**PLUMED 文件的长度单位**。PLUMED 自己的默认是 **nm**，而 CP2K 的一切都是 **Å**（它通过 `setMDLengthUnits(angstrom*0.1)` 告诉 PLUMED 这一点）。
选 **Å** 时工具会在文件最前面写一行
`UNITS LENGTH=A TIME=fs ENERGY=kj/mol`
——这一行必须出现在任何动作行**之前**（PLUMED 边解析边读它），所以工具把它放在紧跟注释块的位置。之后整个文件里的 `R_0`/`SIGMA`/`AT` 都按 Å 读，`HILLS`/`COLVAR` 也按 Å 写、时间列按 fs 写。
**单位串大小写敏感**（`src/tools/Units.cpp:42-57` 是逐字符比较的）：能量只能写 `kj/mol`（小写 k！）/`j/mol`/`kcal/mol`/`eV`/`Ha`，长度只能写 `nm`/`A`/`um`/`Bohr`，时间只能写 `ps`/`fs`/`ns`/`atomic`。**写成 `kJ/mol` 会让 PLUMED 在启动时直接报错**（problem with setting the energy unit … case sensitive）——这是手写 `plumed.dat` 最常见的低级错误，工具生成的草稿不会犯。
**工具不做任何换算**：你填的数就是文件里的数，只是文件声明了它是哪种单位。所以换单位以后，表格里已有的 `R_0`、`σ`、墙的 `AT` 都要自己改一遍（1 nm = 10 Å）。
另一个坑：墙的 `KAPPA` 是能量/**长度²**，而且 PLUMED **不会**替你换算它（`UWalls.cpp` 里 `KAPPA` 就是个普通数字）。同一个物理力常数，nm → Å 时数字要**除以 100**（1 kJ/mol/nm² = 0.01 kJ/mol/Å²），不是除以 10。

## plumed_multicolvar

*标识*：`plumed_multicolvar`

**多分量动作**（multicolvar：`COORDINATIONNUMBER`、`DISTANCES`、`AROUND`、`CAVITY`、`CONTACT_MATRIX`/`COLUMNSUMS`…）算的不是一个数，而是一组数——每个原子的配位数、每对原子的距离。
所以它们**不能直接写进 `METAD ARG=`**：要用同一行里的「约简关键字」把它压成一个标量，压出来的分量用 `标签.分量` 引用：
• `MEAN` → `标签.mean`（平均）
• `SUM` → `标签.sum`
• `MIN={BETA=500.}` → `标签.min`（最小值，`BETA` 是光滑化参数）
• `MORE_THAN={RATIONAL R_0=4}` → `标签.morethan`（有多少个大于 4）
• `LESS_THAN={RATIONAL R_0=0.1}` → `标签.lessthan`
• `BETWEEN={GAUSSIAN LOWER=1.0 UPPER=2.0}` → `标签.between`
工具帮你做两件事：把「分量」那一栏**补到动作行上**（`COORDINATIONNUMBER SPECIES=1-100 R_0=0.33 MEAN`——PLUMED 只有在这一行里看到 `MEAN` 才会有 `标签.mean` 这个分量），并在你没选分量时警告——因为 PLUMED 拿到一组数会报错。`MORE_THAN`/`LESS_THAN`/`BETWEEN` 这类分量必须带值，所以它们只能写在「参数」里，工具会提醒你补。
典型组合（溶解/界面/空腔这类问题）：
`cn: COORDINATIONNUMBER SPECIES=1-100 R_0=0.33 MEAN` → `cn.mean`（平均配位数）；
`d: DISTANCES GROUPA=1-10 GROUPB=11-20 MIN={BETA=500.}` → `d.min`（最近距离）；
`cav: CAVITY DATA=d1 ATOMS=1,4,5,11 SIGMA=0.1 MEAN MORE_THAN={RATIONAL R_0=4}`（盒子里的平均/超过阈值的个数，`DATA` 通常是 `DENSITY SPECIES=…` 或 `COORDINATIONNUMBER …`）。

## plumed_metad

*标识*：`plumed_metad`

**PLUMED 的偏置**（`METAD`）：这一栏填的是 PLUMED 自己的单位——`HEIGHT` 是 **kJ/mol**（不是 Hartree；1 mHartree ≈ 2.6255 kJ/mol）、`SIGMA` 是 **nm**（不是 Å）、`PACE` 是**MD 步**。
• `PACE` —— 每多少个 MD 步撒一个丘陵；
• `HEIGHT` —— 每个丘陵的高度；太小推不动、太大自由能面粗糙（0.5–2 kJ/mol 是常见的起点量级）；
• `默认 SIGMA` —— 丘陵宽度，给那些自己没有 σ 的 CV 用（CV 表里可以单独填）；它在 PLUMED 里是**必填项**，没有默认值；
• `TEMP` —— 体系温度，只有井温（`BIASFACTOR`）才需要；留空就用「生成输入」页的模拟温度；
• `BIASFACTOR` —— 大于 1 就打开井温元动力学（丘陵越填越矮）；留空 = 普通元动力学；
• `FILE` —— 丘陵文件（默认 `HILLS`）：**接着跑和画自由能面都靠它**；
• `RESTART` —— 从 `FILE` 里读回已有丘陵接着撒；
• `ADAPTIVE` / `SIGMA_MIN` / `SIGMA_MAX` —— 让 PLUMED 自己调整丘陵宽度；
• `METAD 其他关键字` —— 工具没建模的东西（`GRID_*`、`CALC_RCT`、`INTERVAL`、多 walker…）原样追加在这一行末尾；
• `偏置动作的标签` —— `m: METAD …` 里的 `m`，别的行用 `m.bias` 引用它；
• `COLVAR` 那两行 —— `PRINT` 输出的文件名与间隔。
想让 PLUMED 用别的偏置（`OPES_METAD`、`PBMETAD`、多 walker…）：那不属于 `METAD`，写在「自由行」里。

## plumed_wall

*标识*：`plumed_wall`

**PLUMED 的约束墙**：`UPPER_WALLS`（挡住 CV 变大）和 `LOWER_WALLS`（挡住变小）。
• `ARG` —— 作用在哪个标量 CV 上（多分量动作要写 `标签.分量`）；
• `AT` —— 墙的位置，用 CV 自己的单位（距离类就是 nm）；
• `KAPPA` —— 力常数，**kJ/mol/nm²**（距离类 CV）；越大越硬。PLUMED 的墙是二次势 `KAPPA·(CV-AT)²`，`KAPPA` 没给就没有意义；
• `EXP`（可选）—— 给了就是指数墙 `KAPPA·exp((CV-AT)/EXP)`，比二次墙软一些，撞上去更平缓。
和 CP2K 原生的墙不是一回事：那边还有 `REFLECTIVE`（反弹）和 `GAUSSIAN`（高斯软墙），PLUMED 这边只有二次/指数两种；一个 CV 要上下都挡就加两条（`UPPER_WALLS` + `LOWER_WALLS`）。

## plumed_args

*标识*：`plumed_args`

**METAD 作用在哪些 CV 上**（`METAD ARG=…`）。留空时工具按「叶子节点」自动填：只取**没有被任何别的动作引用**的那些标量 CV。
为什么不是「所有标量」：真实的 CV 表通常是一条链——比如 20 个「每个尿素分子的 C–N 配位数」再用 `COMBINE` 加成 1 个总量。全填进 `ARG=` 就变成 21 维偏置，几乎不可能收敛，也不是你想要的。取叶子节点，`ARG=` 就正好是那个总量。
想偏置另外几个（例如同时偏置两个独立 CV）：在这里写标签，用逗号或空格分开，顺序就是 `SIGMA=` 的顺序（多分量动作写 `标签.分量`）。
自动判定写在草稿文件的注释里（「偏置哪些 CV：…」），想确认它选了什么，看那一行就行。

## plumed_label

*标识*：`plumed_label`

**偏置动作的标签**：`m: METAD ARG=…` 里的 `m`。PLUMED 里别的行要用`标签.bias` 引用这个偏置的瞬时值，例如 `PRINT ARG=cv_uu,m.bias FILE=COLVAR`。
默认 `m`；从已有 `plumed.dat` 读回来时工具会保留文件里原来的标签，免得你别的行里的引用一下子指空。

## plumed_restart

*标识*：`plumed_restart`

**接着上次的丘陵继续撒**（`METAD RESTART=YES`）。
勾上以后 PLUMED 会先把 `FILE=`（默认 `HILLS`）里的丘陵历史读回来，在已有偏置的基础上继续加——所以**这个文件必须已经存在**：从 PLUMED 2.8 起找不到它会直接报错 `restart file … not found`（更早的版本只是警告，别按老笔记的写法试）。第一次跑不要勾。
井温的续跑也支持换参数：`TEMP`/`BIASFACTOR` 改了以后，旧丘陵会按新值重新缩放（手册明确说明），所以「先短跑摸地形、再加大 ΔT 长跑」是可行的。
**CP2K 那边的 `&EXT_RESTART RESTART_METADYNAMICS` 在这条路线上不起作用**：CP2K 只从 `&METADYN` 读一个文件名，丘陵完全由 PLUMED 管。不勾 `RESTART` 的话，重跑会从零开始，并且**覆盖** `HILLS`。

## plumed_adaptive

*标识*：`plumed_adaptive`

**让 PLUMED 自己调整丘陵宽度**（`METAD ADAPTIVE=`）。默认关：宽度由你给的 `SIGMA` 固定。
• `GEOM` —— 让每个丘陵在**笛卡尔空间**里覆盖一段距离（`SIGMA` 就是一个长度）；适合「CV 变化时构型变化不均匀」的情况；
• `DIFF` —— 让丘陵覆盖**一段时间**（`SIGMA` 就是步数），适合 CV 扩散快慢变化很大的情况。
打开以后有两条硬规矩（都来自 PLUMED 源码/手册）：
1. `SIGMA` 只能给**一个**数（`GEOM` 是长度、`DIFF` 是步数，而且 `DIFF` 时必须是**整数**，它就是要覆盖的步数），表格里给单个 CV 单独设的 σ 会被忽略——工具会在问题清单里提醒你；
2. 事后用 `plumed sum_hills` 重建自由能**必须加直方图修正**（`--histo`），否则曲线是错的。这个修正工具不做，得你自己在后期加上。
`SIGMA_MIN`/`SIGMA_MAX` 是宽度的上下限，**每个 CV 一个**（逗号分开，个数必须和 `METAD ARG=` 的个数一致；只填一个时工具会替你重复到每个 CV）。手册里负值表示不设限。不设上限时宽度会无界地漂，一般建议给一个；**如果把偏置存在网格上（`GRID_*` 写在「METAD 其他关键字」里），PLUMED 要求这时必须给 SIGMA_MIN**，否则直接报错。
反过来，**没打开 ADAPTIVE 时不要填这两项**：PLUMED 那时根本不读它们，会在启动时报 `cannot understand the following words`（所以生成的草稿在自适应关闭时不会写它们）。

## plumed_extra

*标识*：`plumed_extra`

**METAD 行上工具没建模的关键字**，原样追加在这一行末尾——和「自由行」是同一个思路，只是作用在 `METAD` 这一行。
常用：
• `GRID_MIN=-1 GRID_MAX=20 GRID_BIN=500` —— 把偏置存在网格上（丘陵多了以后快很多，也是 `CALC_RCT` 重加权的前提）；
• `CALC_RCT` / `CALC_WORK` / `ACCELERATION` —— 让 PLUMED 多输出`m.rct`/`m.work`/`m.acc` 这些分量；
• `INTERVAL={LOWER=… UPPER=…}` —— 只在某个区间里施加偏置力；
• `WALKERS_N=… WALKERS_DIR=…` —— 多 walker 跑法。
每个关键字之间用**空格**分开（值里本来就有逗号，所以不能用逗号分隔）。工具不检查这些字，写错了 PLUMED 会在启动时报出来。

## plumed_contact

*标识*：`plumed_contact`

**接触型 CV 生成器**：把「一批分子两两之间的配位数」批量写成 CV 表的行。
这类 CV 手写很容易错（每个分子要跟**其它所有**分子的受体原子配对），所以这里用分块描述体系：
• **体系分块** —— 按原子顺序写「每块的原子数 × 块数」，逗号分开，例如 `19x1,8x20` = 1 个 19 原子的分子（4-CPBA）+ 20 个 8 原子的分子（尿素）；
• **中心原子 / 受体原子** —— 块内第几个原子（**0 起算**），可写多个；受体留空 = 整块所有原子都算；
• **排除同一个分子** —— 勾上就只跟别的分子配对（分子间 CV 一般要勾）；
• **R_0 / NN / MM** —— 切换函数的三个参数（单位跟上面的长度单位走；`NN`/`MM` 越大越「硬」：未成键时贡献几乎为 0）；
• **命名** —— 每行的标签前缀与起始编号；勾上的「合计 CV 的标签」会再生成一行 `COMBINE` 把刚才那些配位数加起来（留空就不生成）。
生成的行会**追加**到 PLUMED 集体变量表末尾，可以再逐行编辑。工具会顺便核对：分块总数和体系原子数是否一致、`R_0` 是否超过半个盒子（周期体系里最小镜像会把「从另一侧绕过来」的原子也算成接触）。

## plumed_import

*标识*：`plumed_import`

**读回 plumed.dat**：把手写（或别人给）的 PLUMED 输入读进上面那张表，于是可以在界面里看它、改它，再让工具重新生成一份。
读写规则（重要）：
• 工具**认识**的东西归位：CV 行、`UNITS`、`UPPER_WALLS`/`LOWER_WALLS`、`METAD` 那一行、以及紧跟其后的 `PRINT`（`ARG=` 正好是 `<METAD 标签>.bias` + 那几维 CV 时）；
• 工具**不认识**的东西**原样保留**：动作行里的参数照抄，`METAD` 行上没建模的关键字进「METAD 其他关键字」，其他行进「自由行」——所以再生成的草稿不会丢掉你的写法；
• 注释**不会**保留（工具按自己的模板重写注释块，你写在里面的推导过程请另外存一份）；
• 读回时会把发现的问题列出来：单位串大小写写错（比如 `ENERGY=kJ/mol`，PLUMED 会直接报错）、`METAD` 行缺 `ARG`/`SIGMA`、`RESTART=AUTO` 这类工具不建模的取值、`PRINT` 对不上号等等。
读回是**只读操作**：它不碰磁盘上的那个文件，要覆盖也得你自己按「保存」。

## plumed_free

*标识*：`plumed_free`

**自由行**：这里写的行会**原样**插进 `plumed.dat`，工具一个字符都不改、也不做检查——它是「表里没有的东西也能用」的出口。
什么时候用它：
• 想用表里没列的动作（`PCAVARS`、`TICA`、`PIV`、`FUNCPATHMSD`…）；
• 想换偏置算法（`opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40`）；
• 想用 PLUMED 的其它功能（`INCLUDE`、`RESTART`、多 walker 的 `WALKERS_N`/`WALKERS_DIR`…）；
• 想给某个动作加一行注释或临时改点东西。
**注意两件事**：
1. 位置很重要——放在「METAD 之前」的行在文件上半部分（可以先定义动作，再用它）；放在「文件末尾」适合附加的 `PRINT`、`DUMP*`；
2. 这里定义的标签**不会**出现在 CV 表里，所以两边互相引用要自己留意，工具只检查 CV 表内部的引用。

## meta_preview

*标识*：`meta_preview`

右侧预览有两页（左上角那个下拉框切换）：
• **CP2K 输入** —— 完整输入文件，元动力学那两段（`&FORCE_EVAL/&SUBSYS/&COLVAR` 和 `&MOTION/&FREE_ENERGY/&METADYN`）也在里面；
• **plumed.dat** —— 只在 **PLUMED 路线**下出现：同一张 CV 表 + 同一批丘陵参数写成 PLUMED 的语法（距离/能量已经换算成 nm / kJ/mol）。
「保存」和「复制」作用于**当前这一页**——切到 plumed.dat 页保存，存下来的就是 `plumed.dat`（默认文件名就是它；记得和输入文件放在同一个目录，输入文件里的 `PLUMED_INPUT_FILE` 指向它）。
提示行里的黄字是工具在生成之前就发现的问题，它们也会一起出现在下面的校验结果里。

## save_button

*标识*：`save_button`

**保存**：把右边预览里**当前那一页**存成文件，所以它叫「保存」而不是「保存为 .inp」——CP2K 输入和 `plumed.dat` 共用这一个按钮。
• 停在「CP2K 输入」页 → 存 `.inp`（文件名按结构名/项目名预填，和「输出文件」那一格一致）；
• 停在「plumed.dat」页 → 存 `plumed.dat`（默认文件名就是它）。
每一次都会弹对话框（即使「输出文件」已经填了），只是**起点继承上一次的路径**：上次存到哪，这次就开在哪儿、文件名也预填上，所以「再存一遍」是回车，换名字/换目录是改一下。取消不写任何文件、也不改路径。
**「复制」**把同一页的内容放进剪贴板（贴到服务器上的 `vi` 里就是完整的 `plumed.dat`）；**「恢复默认」**把左侧所有控件清回命令行默认值（自测里逐项比对过，和 `cp2kbuild new` 不带参数时一致）。
这三个按钮固定在左栏底部，不随表单滚动——想确认存的是哪一页，看右下角预览左上角那个下拉框就行。

## 基组偏好

*标识*：`basis_target`

决定优先选哪个基组家族：
• 周期性/固体 → DZVP-MOLOPT-**SR**-GTH（短程，网格便宜，凝聚相首选）；
• 孤立分子 → DZVP-MOLOPT-GTH（带弥散，气相更准）；
• 省算力 → SZV（先跑通再升级）；
• 高精度 → TZV2P（做能量差、反应能时值得多花的时间）。
覆盖不到的元素会自动退到其它家族，并在下面的表里显示。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.KIND.BASIS_SET>

## SCF 路线

*标识*：`scf`

SCF 求解路线：
• OT —— 绝缘体/大体系首选，快且稳，占内存小；
• DIAGONALIZATION —— 金属、需要 smearing、要未占据轨道时用它；
• 自动 —— 带 k 点、开 smearing、能带/态密度时切 DIAGONALIZATION，其余用 OT。
⚠ **k 点只能用对角化**：CP2K 的 OT 遇到 `&KPOINTS` 会直接中止（`qs_scf_initialization.F`：`OT diagonalization not possible with kpoint calculations`）。界面里这两项联动：勾上 k 点会自动把路线改成对角化，选 OT 则会把 k 点关掉，并说明原因。
金属/磁性体系用 OT 常常不收敛，症状是能量忽高忽低。
**两条路线的参数是分开的**（下面「SCF 路线细节」）：选 OT 时只显示 OT 的旋钮，选对角化时只显示对角化/Mixing 的旋钮；「自动」会先算出实际走哪条路线再显示。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF.html>

## route_summary

*标识*：`route_summary`

这里显示**当前实际会走哪条路线**——「自动」不是一条路线，它按任务和开关现场决定：
• 能带 / 态密度 → DIAGONALIZATION（要未占据轨道）；
• 开了 SMEAR → DIAGONALIZATION（分数占据）；
• 其余 → OT。
下面的参数框只显示当前路线用得上的那些，换路线时会实时切换。
**选了 `METHOD FIST`（经典力场）时这一行会换成「经典力场」**：力场路线里整个 `&DFT`/`&SCF` 都不写，OT 和对角化都不存在，所以这一行不该再报一条 SCF 路线（以前它会留着上一次的 OT）。

## ot_minimizer

*标识*：`ot_minimizer`

`&OT MINIMIZER`：OT 用哪个最小化器。CP2K 默认 CG，本工具默认 DIIS。
• **DIIS**：通常最快，大体系/金属边界情况偶尔需要 SAFE_DIIS 兜底；
• **CG**：最稳，配合 LINESEARCH GOLD 一定能找到电子基态，代价是步数多；
• **SD**：最速下降，只在别的都不动时用（很慢）；
• **BROYDEN**：拟牛顿，介于 DIIS 和 CG 之间。
不收敛时的常用组合：先 `MINIMIZER DIIS`，还不行换 `CG` + `LINESEARCH 3PNT/GOLD`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.MINIMIZER>

## ot_preconditioner

*标识*：`ot_preconditioner`

`&OT PRECONDITIONER`：预条件子，决定每一步下降方向的质量。「预条件得好可以快几个数量级」（手册原话）。
• **FULL_SINGLE_INVERSE**（工具默认）：便宜且普适，把 ENERGY_GAP 当下界用，**低于 0.05 Ha 容易不稳**；
• **FULL_ALL**：最有效，但它真的会用 ENERGY_GAP，要给一个偏小的估计（比如 0.002）；
• **FULL_KINETIC**（CP2K 默认）：只用一个动能矩阵，最省内存；
• **FULL_SINGLE / FULL_S_INVERSE / NONE**：更便宜、更弱，大体系内存吃紧时才考虑。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.PRECONDITIONER>

## ot_algorithm

*标识*：`ot_algorithm`

`&OT ALGORITHM`：
• **STRICT**（默认）：标准 OT；
• **IRAC**：隐式旋转对齐约束，另一套收敛方案，有自己的一组参数（IRAC_DEGREE / MAX_IRAC / EPS_IRAC / ORTHO_IRAC 等，本工具没有全部暴露，需要时在文件里手加）。
拿不准就用 STRICT。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.ALGORITHM>

## ot_linesearch

*标识*：`ot_linesearch`

`&OT LINESEARCH`：一维线搜索算法，按鲁棒性和代价递增排列。
• **2PNT**（默认）：几乎总是够用；
• **3PNT**：连续几步 OT 步长都没让总能量下降时用它；
• **GOLD**：最稳，配合 `MINIMIZER CG` 一定能找到电子极小；
• **ADAPT**：让 CP2K 自己调；**NONE**：不做线搜索（不推荐，容易过冲）。
手册原话：「MINIMIZER CG 配 LINESEARCH GOLD 总能找到电子极小」。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.LINESEARCH>

## ot_stepsize

*标识*：`ot_stepsize`

`&OT STEPSIZE`：线搜索的初始步长（a.u.，无量纲）。
留空/负数 = 让 CP2K 按预条件子自己选（推荐）。
什么时候手动给：
• DIIS 一开始震荡 → 调**小**（比如 0.05–0.2）先稳住头几步；
• CG 头几步走得太慢 → 可以试稍大一点。
最优值取决于预条件子，所以它是一个「调试旋钮」，不是精度参数。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.STEPSIZE>

## ot_energy_gap

*标识*：`ot_energy_gap`

`&OT ENERGY_GAP`：HOMO-LUMO 能隙的估计值，单位 Hartree。
**只有 FULL_ALL 和 FULL_SINGLE_INVERSE 两个预条件子会看它**（换成别的预条件子时这一行会被工具灰掉）。
• FULL_ALL：给一个**偏小**的估计（0.002 这种小数字也行），它只是用来构造预条件子；
• FULL_SINGLE_INVERSE：它把该值当**下界**，低于 0.05 可能引起不稳定。
留空 = 让 CP2K 按预条件子自己选。金属/小带隙体系填个估计值常常能明显加速。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.ENERGY_GAP>

## ot_safe_diis

*标识*：`ot_safe_diis`

`&OT SAFE_DIIS`：DIIS 步如果指向**远离**极小值的方向，就丢弃它、改用最速下降（SD）走一步。CP2K 默认 **T**。
• T：更稳，代价是偶尔多走几步；
• F：只在你确认 DIIS 没被「吓住」、想榨性能时用。
只在 MINIMIZER 是 DIIS/BROYDEN 时有意义（换成 CG/SD 时这一行会被灰掉）。
「跟随 CP2K 默认」= 不写这一行。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.SAFE_DIIS>

## ot_rotation

*标识*：`ot_rotation`

`&OT ROTATION`：额外引入「占据子空间旋转」的自由度。
**只在能量对占据子空间旋转不不变时才需要**，也就是：
• 分数占据（开了 SMEAR）；
• 非单重态的 restricted（RKS）计算。
普通绝缘体（整数占据、UKS/RKS 都行）**不需要**，打开只会让计算变慢。
本工具在你强制「OT + SMEAR」时会自动加上 ROTATION T，并在校验里给一条 warning（CP2K 官方回归测试里没有这种组合，标准做法是 smearing 走 DIAGONALIZATION）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OT.ROTATION>

## outer_optimizer

*标识*：`outer_optimizer`

`&SCF/&OUTER_SCF OPTIMIZER`：外层 SCF 循环用什么优化器更新预条件子/轨道，CP2K 默认 NONE（只按 EPS_SCF 再跑一轮）。
• **DIIS**：最常用，外层也做 DIIS 加速；
• **BROYDEN / SD**：DIIS 不稳时的备选；
• **BISECT / NEWTON / SECANT / NEWTON_LS**：更昂贵的牛顿类方案，一般用于特别难收敛的体系。
「跟随 CP2K 默认」= 不写这一行（外层只重复内层设置）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/OUTER_SCF.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.OUTER_SCF.OPTIMIZER>

## diag_algorithm

*标识*：`diag_algorithm`

`&DIAGONALIZATION ALGORITHM`：对角化用什么算法。
• **STANDARD**（默认）：先正交化再做标准本征求解，中等体系最常用（EPS_ITER 控制本征解精度）；
• **DAVIDSON**：迭代求部分本征值，大体系/只要占据态时更快，有子段 `&DAVIDSON` 可调；
• **OT**：在 &DIAGONALIZATION 里再嵌一次 OT（少见，本质是「用 OT 求占据轨道再做对角化」）；
• **LANCZOS / FILTER_MATRIX**：特殊场景（后者适合大体系线性标度）。
拿不准就用 STANDARD。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/DIAGONALIZATION.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.DIAGONALIZATION.ALGORITHM>

## level_shift

*标识*：`level_shift`

`&SCF LEVEL_SHIFT`：能级平移，用来把占据态和空态「推开」以改善收敛，单位 Hartree（典型 0.1–0.5）。**只在对角化路线生效**（源码里只在 `qs_scf_diagonalization.F` 用到；OT 路线这一行会被灰掉）。
而且不是一开始就生效：CP2K 只在**第 2 个 SCF 步之后**（或者初猜是 CORE 时）才启用它。
典型用途：零带隙/近简并体系对角化时占据数乱跳。代价是要多算几轮才能把平移撤掉。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.LEVEL_SHIFT>

## mixing_method

*标识*：`mixing_method`

`&SCF/&MIXING METHOD`：对角化路线的密度混合方案。CP2K 默认 DIRECT_P_MIXING。
• **BROYDEN_MIXING**（工具默认）：最常用，配 ALPHA/NBUFFER/BETA；
• **PULAY_MIXING / NEW_PULAY_MIXING / MULTISECANT_MIXING**：用历史信息做准牛顿混合，难收敛的金属/表面可以试；
• **KERKER_MIXING**：老式简单混合，简单但不快；
• **NONE**：完全不混合（只做一步，用来调试）。
**只有对角化路线会写 &MIXING**：OT 路线用不到它（会被灰掉），开了 SMEAR 也不写（用 CP2K 自己的默认）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/MIXING.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.MIXING.METHOD>

## poisson_solver

*标识*：`poisson_solver`

`&DFT/&POISSON POISSON_SOLVER`：解静电 Poisson 方程的算法。**它和晶胞周期性是绑定的**——每种求解器只对某些周期性有效（下面这些是手册里逐个写明的）：
• `PERIODIC`：**只**能用于三维周期体系（XYZ）；
• `ANALYTIC`：0D/1D/2D，用解析格林函数（收敛慢）；
• `MT`（Martyna-Tuckerman）：0D 和 2D，只和最近邻镜像作用；**晶胞小于两倍团簇尺寸时结果是错的**；
• `MULTIPOLE`：**只**用于孤立团簇（0D）；
• `WAVELET`：0D/2D/3D 都行，要求密度在晶胞面上已趋于零；
• `IMPLICIT`：0D/1D/2D/3D 都行。
所以这个下拉框里的选项**跟着「周期性」实时变**：选 XYZ 时只剩 PERIODIC/WAVELET/IMPLICIT，选 XY（表面板）时 PERIODIC 和 MULTIPOLE 就消失了。
注意 `&POISSON PERIODIC` 与 `&CELL PERIODIC` 是两个设置，手册说通常应保持一致（几何/近邻表看 &CELL，静电看 &POISSON），校验器会检查这一点（`poisson-periodic-mismatch`）。
默认「自动」：三维周期体系不写 `&POISSON`（CP2K 默认的 PERIODIC 正好合适），非三维周期时自动写 `PERIODIC <你的周期性>` + `MT`（原子数 > 200 换 `WAVELET`）——只改 `&CELL PERIODIC` 而不改求解器，CP2K 会直接报错。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/POISSON.html#CP2K_INPUT.FORCE_EVAL.DFT.POISSON.POISSON_SOLVER>

## smear_method

*标识*：`smear_method`

`&SCF/&SMEAR METHOD`：占据数按哪种方式展宽。CP2K 默认 GAUSSIAN，本工具默认 FERMI_DIRAC（金属的标准选择，也是教科书里的 Mermin 方案）。
• **FERMI_DIRAC**：物理意义明确，电子温度直接对应 `ELECTRONIC_TEMPERATURE`；
• **METHFESSEL_PAXTON**：MP 方案，展宽小一些通常也能收敛，对态密度形状没 FERMI_DIRAC 那么敏感；
• **MARZARI_VANDERBILT**：冷展宽，能更快逼近零温极限，但要额外注意 ENTROPY 是否够小；
• **ENERGY_WINDOW / LIST / GAUSSIAN**：特殊用途（ENERGY_WINDOW 用于固定能量窗口，LIST 要自己给占据数）。
报告里要写清楚用的是哪种、展宽多少：它们给出的能量不是同一个数。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/SCF/SMEAR.html#CP2K_INPUT.FORCE_EVAL.DFT.SCF.SMEAR.METHOD>

## 强制基组

*标识*：`basis`

通常留空，让它按价电子数自动匹配。
只有在你明确知道要用什么基组时才手填，例如 DZVP-MOLOPT-SR-GTH 或 TZV2P-MOLOPT-GTH。
填了之后会和赝势的价电子数再核对一次。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.BASIS_SET_FILE_NAME>

## 基组文件

*标识*：`basis_file`

留空时自动选，一般会是 BASIS_MOLOPT（找不到再退到 BASIS_SET / BASIS_MOLOPT_UZH / BASIS_pob 等）。
CP2K 运行时按这个文件名去找数据文件：先找输入文件所在目录，再找 CP2K_DATA_DIR。
放在别的目录时可以写绝对路径。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.BASIS_SET_FILE_NAME>

## 赝势文件

*标识*：`potential_file`

留空时自动选，一般是 GTH_POTENTIALS。
注意它必须真的包含你用的那个赝势名字（GTH-PBE-q6 之类），否则 CP2K 报 “Potential not found in the potential file”。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.POTENTIAL_FILE_NAME>

## 电荷

*标识*：`charge`

体系总电荷（不是某个原子的电荷）。
带电体系要做电荷补偿：周期性带电体系的能量收敛很慢，能加中和背景电荷或显式反离子会更稳妥。
填错的最典型症状是 SCF 怎么都不收敛。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.CHARGE>

## 多重度

*标识*：`multiplicity`

自旋多重度 = 2S+1。1=单重态（闭壳层），2=双重态（一个未成对电子），3=三重态（O2、卡宾等）。
硬规则：**电子数与多重度必须同奇偶**，比如 9 个电子只能是 2、4、6…（这条工具会替你检查）。
过渡金属配合物、自由基、吸附在金属上的 O2 都要显式设。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.MULTIPLICITY>

## UKS

*标识*：`force_uks`

UKS = 自旋极化（alpha/beta 分开算）。
多重度 > 1 时新版 CP2K 会自动极化；老输入靠 UKS/LSD 显式打开。
想算开壳层单重态（多重度 1 但自旋不配对）才需要手动勾。
代价大约是闭壳层的 1.5–2 倍。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT.html#CP2K_INPUT.FORCE_EVAL.DFT.UKS>

## 真空层

*标识*：`vacuum`

给孤立分子/团簇/表面板包一层真空。
**盒子边长的算法是「分子跨度 + 2×真空」**，不是把真空设成边长：水的跨度约 1.5 Å，填 12 就得到约 25 Å 的盒子（体积约 1.5 万 Å³）。
填了真空层之后，工具会做三件事：① 建一个正交盒子；② 把分子平移到盒子正中心（**坐标因此会变**，分子内部的键长/角度不变）；③ 自动加上 `&CELL PERIODIC NONE` 和 `&POISSON PERIODIC NONE` + `POISSON_SOLVER MT`——只设 PERIODIC NONE 而不改 Poisson 求解器，CP2K 会直接报错。
原子数超过 200 时自动改用 WAVELET。
经验值：分子 10–15 Å，表面板 15–20 Å（看层厚和偶极）。MT 要求电荷密度被限制在盒子内，所以别给太小的真空层（< 8 Å）。
**结构文件自带晶胞时这个参数不起作用**（晶体、POSCAR/CIF 里已经有真空的表面板都属于这种）；要改那种体系的盒子，用上面的「晶胞」框。
代价大致随盒子的体积涨：12 → 20 Å 体积涨 4.5 倍，所以够用就好。

## 周期性

*标识*：`periodic`

晶胞的周期性方向：
• 自动 —— 结构文件带晶胞就用 XYZ，没有就不设；
• XYZ —— 体相晶体、溶液盒子；
• XY —— 表面板（z 方向非周期），记得配合真空层；
• NONE —— 孤立分子/团簇，会同时改 Poisson 求解器。
误设 XYZ 去算孤立分子，等于把分子复制成一排相互作用的镜像。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/CELL.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.CELL.PERIODIC>

## 晶胞覆盖

*标识*：`cell`

覆盖结构文件里的晶胞。格式：
• `a,b,c`（正交，单位 Å），或
• `a,b,c,α,β,γ`（角度制），或
• 9 个数 = 三个晶格矢量。
做体积扫描（比如 EOS 拟合）时用它替换晶胞。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/SUBSYS/CELL.html#CP2K_INPUT.FORCE_EVAL.SUBSYS.CELL.ABC>

## 项目名

*标识*：`project`

CP2K 用它给输出、restart、轨迹文件命名（PROJECT-1.xyz 之类）。
留空则用结构文件名。名字里别带空格和路径分隔符。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/GLOBAL.html#CP2K_INPUT.GLOBAL.PROJECT_NAME>

## 打印级别

*标识*：`print_level`

控制输出详细程度。
• LOW 最省 I/O，日常大体系用它；
• MEDIUM 默认，出错时够看；
• HIGH 打印每一步细节，调试不收敛时开；
• DEBUG 会写海量信息，只在开发时用。
不收敛的问题基本都能在 MEDIUM 的输出里看出来。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/GLOBAL.html#CP2K_INPUT.GLOBAL.PRINT_LEVEL>

## WALLTIME

*标识*：`walltime`

写进 &GLOBAL WALLTIME，单位秒。到点 CP2K 会干净地收尾并写 restart，而不是被调度器强杀。
建议设成队列时限的 95%，留出写文件的时间。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/GLOBAL.html#CP2K_INPUT.GLOBAL.WALLTIME>

## 输出文件

*标识*：`out`

生成的 .inp 保存到哪里。留空则用「项目名.inp」。
保存下来的是纯文本，你可以直接手改——用这个工具生成骨架，再手工加模板里没有的段（比如 EFIELD、CONSTRAINT）是很正常的用法。
「复制」按钮则把内容放进剪贴板，方便贴到服务器上的编辑器里。

## 打印受力

*标识*：`print_forces`

打印原子受力。做几何优化、频率、NEB 时本来就该有；做单点能时勾上可以顺便看结构是否合理（力大说明结构没优化好）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/PRINT/FORCES.html>

## Mulliken

*标识*：`print_mulliken`

Mulliken 布居分析。方便快速看电荷转移和自旋分布，
但基组依赖性强，**不要用它做定量结论**；要做定量分析请用 RESP 或 Hirshfeld。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MULLIKEN.html>

## 偶极矩

*标识*：`print_dipole`

偶极矩。只对非周期体系有意义，所以周期性体系不会生成这一项。
注意孤立分子要配合真空层，否则相邻镜像的偶极会互相干扰。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MOMENTS.html>

## 优化器（几何）

*标识*：`geo_optimizer`

优化算法：
• BFGS —— 默认，中小体系最省步数（本工具默认）；
• LBFGS —— 大体系内存友好，步数多一点；
• CG —— 起步猛，接近极小点时慢。
优化不收敛时先别换算法，先检查初始结构、SCF 收敛、以及 cutoff。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/GEO_OPT.html#CP2K_INPUT.MOTION.GEO_OPT.OPTIMIZER>

## 最大步数（几何）

*标识*：`geo_max_iter`

最大优化步数（每步一次 SCF）。
200 对大多数分子够用；大表面板可以到 500。
跑完还没收敛会给出提示，那时把上一步的结构当新输入继续跑即可。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/GEO_OPT.html#CP2K_INPUT.MOTION.GEO_OPT.MAX_ITER>

## 优化器（晶胞）

*标识*：`cell_optimizer`

晶胞优化默认用 LBFGS：它比 BFGS 便宜得多，因为每个自由度都要靠应力张量来定。
BFGS 在 CELL_OPT 里通常不划算。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/CELL_OPT.html#CP2K_INPUT.MOTION.CELL_OPT.OPTIMIZER>

## 最大步数（晶胞）

*标识*：`cell_max_iter`

最大晶胞优化步数。晶胞比原子难收敛，通常要 100–300 步。
注意：一定要配合收敛好的 CUTOFF/REL_CUTOFF，否则应力有噪声，晶胞会来回振荡。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/CELL_OPT.html#CP2K_INPUT.MOTION.CELL_OPT.MAX_ITER>

## 外压

*标识*：`cell_pressure`

外压（bar）。0 表示优化到零压；
做高压相、地幔条件时填实际压力（比如 100 GPa = 1e6 bar）。
注意单位是 bar 不是 GPa。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/CELL_OPT.html#CP2K_INPUT.MOTION.CELL_OPT.EXTERNAL_PRESSURE>

## 固定角度

*标识*：`keep_angles`

勾上之后只优化 a/b/c 和原子位置，晶胞角度固定。
做各向异性材料的单轴压缩、或者已知晶系只差长度时有用。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/CELL_OPT.html#CP2K_INPUT.MOTION.CELL_OPT.KEEP_ANGLES>

## 系综

*标识*：`ensemble`

系综决定哪几个量守恒：
• NVE —— 能量守恒，用来检查积分步长是否合适（总能量漂移要小）；
• NVT —— 控温，最常用；
• NPT_I —— 控温控压且晶胞各向同性变化（溶液、流体）；
• NPT_F —— 晶胞形状也变（固-固相变、各向异性材料）；
• LANGEVIN —— 摩擦 + 随机力的恒温动力学（手册的原话是 "langevin dynamics (constant temperature)"）。
先跑 NVE 看能量漂移，再换 NVT 取数据。
**要升降温就选 LANGEVIN**：`&MD TEMPERATURE_ANNEALING`（让目标温度在运行中连续移动）只在 Langevin 积分器里实现，手册写着 "works only for the Langevin ensemble"——NVT/NPT 下它一声不响什么都不做，而速度缩放那种（`ANNEALING`）和 NVT/NPT 的恒温器同时开会被 CP2K 直接中止。所以单段斜坡（`--temperature-ramp`）和多段温度程序都推荐 `LANGEVIN`（工具会在 LANGEVIN 下写一个 `&LANGEVIN GAMMA`，默认 0.01 fs⁻¹；**CP2K 自己的默认是 0 = 没有摩擦也没有噪声 = NVE**，那样温度不受控、斜坡也不会生效）。
代价要知道：摩擦会改变动力学——扩散系数、振动谱这类**动力学**量会被压小（要算它们就把 GAMMA 取小、或者改用 NVT + CSVR）。如果只是要一条顺滑的变温曲线、后续取的是热力学量，LANGEVIN + GAMMA 0.01 最省事；不想引入摩擦就用 NVT 把升温拆成很多个固定温度的小段（阶梯式，段边界靠 `RESTART_THERMOSTAT` 接住恒温器状态）。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.ENSEMBLE>

## 恒温器

*标识*：`thermostat`

恒温器类型：
• NOSE —— Nose-Hoover，正则系综严格，但体系小的时候温度会振荡；
• CSVR —— 随机速度重标定，平衡快、温度稳，取样常用它；
• GLE —— 广义 Langevin，可以调出量子核效应，需要自己提供 S/A 矩阵；
• AD_LANGEVIN —— 自适应 Langevin。
拿不准就用 CSVR；要严格正则就用 NOSE（并把 TIMECON 调大些）。
**工具不再写 `TEMP_TOL`**：那个"瞬时温度偏离目标就重标定速度"的保险是 CP2K 里**已标注过时（obsolescent）**的关键字，手册写得很明确——*"Using a CSVR thermostat with a short timeconstant is recommended as a better alternative"*（`src/motion/input_cp2k_md.F`），CP2K 自己的默认也是 0（关）。温度交给恒温器就够了：CSVR 用短一点的 TIMECON（几十 fs）就能起到同样的作用，而且不会像整体重标定那样把动力学搞坏。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/THERMOSTAT.html#CP2K_INPUT.MOTION.MD.THERMOSTAT.TYPE>

## 温度

*标识*：`temperature`

目标温度（K）。室温 300，生物体系 310，
表面反应常算 300–800 看趋势。
要跟实验对照的活化能，最好在多个温度各跑一遍。
控温靠恒温器（推荐 CSVR），**不靠 `TEMP_TOL`**——那是 CP2K 已过时的速度重标定开关，工具不再写它。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.TEMPERATURE>

## 时间步长

*标识*：`timestep`

积分步长（fs）。
• 含氢体系（H、OH、水）**不要超过 0.5 fs**，否则 O-H 键会炸；
• 不含氢的重元素体系可以到 1–2 fs。
判断是否太长：跑一段 NVE 看总能量漂移，漂移明显就把步长减半。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.TIMESTEP>

## 步数

*标识*：`steps`

总步数 = 步长 × 步数 是总模拟时间。
0.5 fs × 20000 步 = 10 ps，够看振动和快扩散；
做统计平均（扩散系数、RDF）至少要几十 ps，而且前 10–20% 要当平衡段丢掉。
先跑 2000 步确认稳定，再开长跑。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD.html#CP2K_INPUT.MOTION.MD.STEPS>

## 压力

*标识*：`md_pressure`

NPT 的目标压力（bar）。1 bar ≈ 常压。
只有改变晶胞的系综才启用。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/MD/BAROSTAT.html#CP2K_INPUT.MOTION.MD.BAROSTAT.PRESSURE>

## 随机种子

*标识*：`seed`

写进 &GLOBAL SEED，决定初始速度的随机数。
填一个固定值可以让同一次 MD 完全复现；
做系综平均时应当换几个不同的种子跑多组。
留空则由 CP2K 自己取。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/GLOBAL.html#CP2K_INPUT.GLOBAL.SEED>

## 红外强度

*标识*：`intensities`

红外强度：需要偶极矩对原子位移的导数（Born 电荷），所以只对非周期体系生成。
代价是普通频率计算的 2–5 倍，因为要额外算偶极导数。
注意：CP2K 2025.1 没有 &RAMAN 段，拉曼强度要自己后处理。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/VIBRATIONAL_ANALYSIS.html#CP2K_INPUT.VIBRATIONAL_ANALYSIS.INTENSITIES>

## 热化学

*标识*：`thermochemistry`

顺便给出热化学量（零点能、内能、熵、热容）。
注意 CP2K 的谐振近似只能用于孤立分子；算出来的熵没有考虑低频扭转的贡献，和实验比会偏小。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/VIBRATIONAL_ANALYSIS.html#CP2K_INPUT.VIBRATIONAL_ANALYSIS.THERMOCHEMISTRY>

## 晶格类型

*标识*：`lattice`

决定高对称点的定义（Γ、X、M、K…在不同晶格里的分数坐标不一样）。
「自动判断」按晶胞长度和角度猜 Bravais 类型：a=b=c 且 90° → 简单立方，a=b=c 且 60° → 面心立方原胞，等等。
如果你的原胞不是标准取法（比如六方用了非标准基矢），请手动指定，或者直接在下面写分数坐标。

## k 路径

*标识*：`kpath`

高对称路径。三种写法：
• 留空 —— 用该晶格类型的默认路径；
• 点名 —— `GAMMA-X-M-GAMMA`；
• 直接给坐标 —— `0,0,0;0.5,0,0;0.5,0.5,0`（分数坐标，B_VECTOR 单位）。
用竖线 `|` 表示断开的两段路径，例如 `GAMMA-X-M-GAMMA|M-R`。
面心立方的常用路径是 `GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.BAND_STRUCTURE.KPOINT_SET.SPECIAL_POINT>

## 路径点数

*标识*：`npoints`

整条路径上的总点数，会按各段长度自动分配。
40–60 点够看形状；要精确读带隙位置（比如判断直接/间接）用 100–200。
点数太密只是在浪费时间，带结构本身是平滑的。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.BAND_STRUCTURE.KPOINT_SET.NPOINTS>

## SCF k 网格

*标识*：`kgrid`

SCF 用的 k 点网格，格式 `4 4 4`（也可写 `4,4,4`）。
**要勾上面的组标题才能启用**：不勾就完全不写 `&KPOINTS`，CP2K 只用 Γ 点一个 k 点——分子、大盒子、宽禁带绝缘体都该这样。
⚠ **k 点只能用对角化（DIAGONALIZATION）算**：CP2K 的 OT 遇到 `&KPOINTS` 会直接中止（源码 `qs_scf_initialization.F`：`OT diagonalization not possible with kpoint calculations`）。所以这两件事在界面里是联动的——勾上 k 点会把「SCF 路线」从 OT 改成对角化，把路线改成 OT 则会把 k 点关掉，屏幕上都会写明原因；校验器对「OT + k 点」给的是 **error**。
什么时候必须给网格：
• 金属、小带隙半导体：**必须**（通常 8×8×8 起）并配合 SMEAR；
• 中等晶胞的绝缘体：4×4×4 这类常见；
• 真空层很厚（>15 Å）的 slab：真空方向的 1 就够（如 4 4 1）。
**网格要收敛**：它决定自洽密度，密度不对能量、力、能带都会整体偏；做法和 CUTOFF 一样——加密一档看能量变不变。
勾「按间距自动」时，工具按目标间距（默认 20 Å）给出网格：间距越小网格越密。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.SCHEME>

## kpoint_spacing

*标识*：`kpoint_spacing`

自动网格的目标间距：**每多少 Å 放一个 k 点**（默认 20 Å）。
算法：每方向 `round(间距 / 该方向边长)`，下限 1、上限 8（`suggest_kpoint_grid`），所以大盒子自动退化成 Γ 点。
怎么选：
• 20 Å 是常见的「够用」起点；
• 金属/需要精确费米面 → 10–15 Å 甚至更小；
• 只是做大盒子的粗略优化 → 25–30 Å 省机时。
**它是估计不是收敛证据**：真正的做法是把间距调小一档，看能量/受力的变化是否在你关心的量级以下。
自动网格写出来的就是 `SCHEME MONKHORST-PACK n m l`，可以直接把算出来的数字填回「按间距自动」旁边的网格框里固定住。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.SCHEME>

## kpoint_scheme

*标识*：`kpoint_scheme`

`&KPOINTS SCHEME`：k 点怎么生成。
• **自动（按网格/间距）**——工具按网格数字或目标间距生成`GAMMA` / `MONKHORST-PACK n m l`，这是常用做法；
• **GAMMA**——只用 Γ 点（等于「不给网格」）；
• **MONKHORST-PACK / MACDONALD**——均匀网格（后者可带偏移，写成 `MACDONALD 4 4 4 0.5 0.5 0.5`，用「自定义文本」输入）；
• **GENERAL**——用下面表里**显式列出**的 k 点（CP2K 会写成一串 `KPOINT x y z w`）。
选 GENERAL 时注意：`SYMMETRY` 还开着的话 CP2K 仍会做对称约化，要每个点都算就把它关掉。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.SCHEME>

## kpoint_points

*标识*：`kpoint_points`

显式 k 点：每一行一个点 `kx ky kz 权重`，单位由右边的 `UNITS` 决定（默认 `B_VECTOR`，即倒格矢倍数：Γ 是 `0 0 0`）。
CP2K 会写成 `SCHEME GENERAL` + 一串 `KPOINT x y z w` 行——这正是 CP2K 自己回归测试里的写法。
什么时候用：
• 只想算文献里点名的某个 k 点（比如某个电荷密度波矢量）；
• 自定义路径/权重，CP2K 的自动网格表达不了；
• 调试：只算 1 个点看某个本征值。
**权重**决定这个点在密度里的占比，一般全填 1.0；用 2.0 这种是手工加权，通常只在你清楚为什么时才做。
显式点 + `SYMMETRY T` 会被约化掉一部分，要保留全部就关掉 SYMMETRY。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.KPOINT>

## kpoint_gamma_centered

*标识*：`kpoint_gamma_centered`

`&KPOINTS GAMMA_CENTERED`：把 Monkhorst-Pack 网格**平移**，使网格里包含 Γ 点。
为什么有人要它：
• Γ 点在网格里时，总能带上「Γ 点性质」（例如某些对称性判断、`&KPOINTS` 与 `MO_CUBES` 搭配时更直观）；
• 手册也提醒：网格平移会改变点的位置，**和 Wannier 化/插值配合时要和你之前的设置保持一致**。
默认不勾：绝大多数 SCF 采样不需要平移，而且平移前后能量本来就应该一致（不一致说明网格还不够密）。
CP2K 的 `GAMMA_CENTERED` 默认是 F。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.GAMMA_CENTERED>

## kpoint_full_grid

*标识*：`kpoint_full_grid`

`&KPOINTS FULL_GRID`：**不做对称约化**，整个网格的点全算。
默认（不勾）CP2K 用晶体对称性把 k 点约化到不可约区，金属常常能省一大半机时。
什么时候必须用完整网格：
• 后面要做 Wannier 化 / 输运 / 插值，需要**每个** k 点都有波函数；
• 检查对称约化是不是引入了一致性问题（完整网格当基准）。
代价：k 点数按对称性倍数增长，能量不会因此更准——**它只影响「有没有那个 k 点」，不影响精度**。
CP2K 的 `FULL_GRID` 默认是 F。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.FULL_GRID>

## kpoint_symmetry

*标识*：`kpoint_symmetry`

`&KPOINTS SYMMETRY`：按晶体对称性把网格约化到不可约 k 点。
**工具默认勾上（SYMMETRY T）**——CP2K 自己的默认是 F，但约化后每条 k 点带各自的权重，结果与完整网格一致而机时少得多，所以是通常想要的。
什么时候关掉：
• 显式（GENERAL）k 点列表里你必须保留**每一个**点；
• 体系没有对称性、或你想逐个 k 点核对输出；
• 怀疑对称约化与某些后处理（Wannier、插值）冲突。
关掉只是变慢，不会算错。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.SYMMETRY>

## kpoint_wavefunctions

*标识*：`kpoint_wavefunctions`

`&KPOINTS WAVEFUNCTIONS`：波函数按实数还是复数存。
• **REAL**（工具默认）——能实时用实数，省内存和时间；CP2K 手册说只要 k 点允许就自动用实波函数；
• **COMPLEX**——CP2K 自己的默认。自旋轨道耦合、以及某些带非平凡相位的 k 点对称性必须用它。
不确定就先用 REAL；出现与相位/自旋轨道相关的怪问题再换 COMPLEX。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.WAVEFUNCTIONS>

## kpoint_verbose

*标识*：`kpoint_verbose`

`&KPOINTS VERBOSE T`：在输出里把生成/约化后的 k 点列表打出来。
排查时很有用：
• 确认网格到底给了哪些点、各自的权重；
• 检查 `FULL_GRID`/`SYMMETRY` 是否按预期生效；
• 核对显式 k 点的坐标有没有写错单位。
代价是输出长一点，长跑时可以关掉。CP2K 默认不打印。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS.html#CP2K_INPUT.FORCE_EVAL.DFT.KPOINTS.VERBOSE>

## fixed_atoms

*标识*：`fixed_atoms`

`&MOTION/&CONSTRAINT/&FIXED_ATOMS LIST`：**固定哪些原子**。
最典型的用法是 slab/表面：让上面几层和吸附物弛豫，把下面几层按住，既省机时又避免整块漂移。
填 CP2K 自己的列表写法（照原样写进输入）：
• `1..12` —— 第 1 到 12 号原子（原子编号就是结构文件里的顺序，从 1 开始）；
• `1 3 5..8` —— 混合写法，空格分隔；
• 留空 = 都不固定。
右边的「方向」是 `COMPONENTS_TO_FIX`：
• `XYZ`（默认）——三维都锁死；
• `Z` —— 只锁 z 方向（表面弛豫里最常见：横向可动、纵向按住）；
• `XY`/`XZ`/`YZ` 等按需。
只对**会移动原子**的任务有效（GEO_OPT / CELL_OPT / MD）；单点能量配它没有任何作用，校验器会提醒。
注意：约束≠把原子从体系里删掉，它们照样参与能量和受力计算。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/CONSTRAINT/FIXED_ATOMS.html#CP2K_INPUT.MOTION.CONSTRAINT.FIXED_ATOMS.LIST>

## print_mo_cubes

*标识*：`print_mo_cubes`

`&DFT/&PRINT/&MO_CUBES`：把**分子轨道**写成 Gaussian cube 文件（`<项目名>-HOMO-1_...cube` 这类命名），VMD / VESTA / gOpenMol 直接能看。
想看「HOMO 长什么样」「吸附发生在哪个轨道」时，这是最快的办法。
• `NHOMO 1` = 最高占据轨道（HOMO），`NLUMO 1` = 再加上最低空轨道（LUMO）；
• **格点**由 `STRIDE` 控制：CP2K 默认 `2 2 2`（每 2 个格点取一个，文件小、画图够用），`1 1 1` 是全网格、最精细也最大；
• 文件可能很大（几十 MB 起），长跑时留意磁盘。
**和 k 点一起用要当心**：cube 是实空间轨道，k 点采样下它对应的是某个k 点的轨道，做金属体系时通常不是你想要的——分子/Γ 点体系最合适。
想看电荷密度而不是轨道，勾下面的 `&TOT_DENSITY_CUBE`。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_CUBES.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_CUBES.NHOMO>

## mo_cubes_nhomo

*标识*：`mo_cubes_nhomo`

`&MO_CUBES NHOMO` / `NLUMO`：**围绕 HOMO-LUMO 间隙各取几条轨道**。
• `NHOMO 1`（默认）+ `NLUMO 0` → 只有 HOMO；
• `NHOMO 1` + `NLUMO 1` → HOMO 和 LUMO（最常用的一对）；
• 想看清楚一个带/一组简并轨道，可以给 2–3。
每条轨道一个文件，数量越多写得越慢。
（`HOMO_LIST` 那种按编号点名的高级用法不在界面上，需要时直接改生成的输入。）

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_CUBES.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.MO_CUBES.NLUMO>

## print_density_cube

*标识*：`print_density_cube`

`&DFT/&PRINT/&TOT_DENSITY_CUBE`：把**总电子密度**写成 cube 文件（`<项目名>-TOT_DENSITY.cube`）。
用途：看键、看孤对电子、看电荷转移；也可以和别的程序算的密度做差（要做差就**保持同一套格点**：`STRIDE` 和晶胞一致）。
格点同样用 `STRIDE` 控制（CP2K 默认 `2 2 2`；`1 1 1` 最精细）。
密度 cube 一般不大（一个格点一个数），但全网格的大盒子也会上 GB，所以长跑时先估一下：格点数 ≈ (a/sx)(b/sy)(c/sz)。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/TOT_DENSITY_CUBE.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.TOT_DENSITY_CUBE.STRIDE>

## NLUMO

*标识*：`pdos_nlumo`

PDOS 里额外算多少个空轨道（NLUMO）。
想看导带底就得给够，一般 20–50；给太少导带是空的。
位置随版本变：2026.2 起在 &PRINT &DOS 下，2025.1 在 &PRINT &PDOS 下。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/DOS.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.DOS.NLUMO>

## LDOS

*标识*：`pdos_ldos`

投影哪些原子/轨道的态密度，如 `1 2 3` 表示第 1、2、3 号原子的 LDOS。
留空则只出总 PDOS。
做表面吸附时很有用：只看吸附分子和它下面那几个金属原子的贡献。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/DOS/LDOS.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.DOS.LDOS.LIST>

## pdos_broaden

*标识*：`pdos_broaden`

把离散能级展宽成平滑的 DOS 曲线（CP2K 2026.2 新增）。
填的是半高全宽 FWHM，单位 eV：0.05 左右能分辨精细结构，0.2 左右看整体趋势；不填就不生成展宽段。
**展宽只影响画图观感，不改变任何计算结果**；所以拿它试不同宽度是零成本的。
（2025.1 没有这个功能，选了也不会写进输入。）

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.DOS.CURVE.BROADEN.WIDTH>

## pdos_broaden_type

*标识*：`pdos_broaden_type`

展宽函数形状：
• GAUSSIAN —— 默认，峰形对称，最常用；
• LORENTZIAN —— 尾巴长，适合看寿命展宽/金属体系；
• PSEUDO_VOIGT —— 两者混合，混合比例由 VOIGT_MIXING 控制。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN.html#CP2K_INPUT.FORCE_EVAL.DFT.PRINT.DOS.CURVE.BROADEN.TYPE>

## NEB 类型

*标识*：`band_type`

NEB 变体：
• CI-NEB —— 爬山弹性带，会精确收敛到鞍点，最常用；
• IT-NEB —— 改进切线，路径弯曲时更稳；
• B-NEB —— 只做普通弹性带，不给鞍点；
• D-NEB —— DIIS 加速；
• SM / EB —— 弦方法 / 能量带，特殊场合用。
拿不准用 CI-NEB。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/BAND.html#CP2K_INPUT.MOTION.BAND.BAND_TYPE>

## 弹簧常数

*标识*：`k_spring`

相邻镜像之间的弹簧常数。
0.01–0.1 是常用范围（本工具默认 0.05）。
太大路径会僵硬、鞍点被压歪；太小镜像会聚在一起。
调它之前先确认收敛判据（MAX_FORCE/RMS_FORCE）是否设得太松。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/BAND.html#CP2K_INPUT.MOTION.BAND.K_SPRING>

## 镜像文件

*标识*：`replicas`

按 反应物 → 中间态 → 产物 的顺序给出至少 5 个镜像，每个都是完整坐标文件（带晶胞）。
镜像是**线性插值**出的初始猜测，不是越多越好：5–9 个通常够，路径复杂再加。
每个镜像都要有晶胞，否则会落进默认的 1 Å 立方体（校验器会拦这个）。
生成输入时可以直接用 examples 里的文件先试通流程。

手册：<https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/MOTION/BAND/REPLICA.html#CP2K_INPUT.MOTION.BAND.REPLICA.COORD_FILE_NAME>

## 待校验文件

*标识*：`validate_files`

把要检查的 .inp 加进来（也可以直接拖文件到列表里），然后点「开始校验」。
检查分两层：一层对照手册数据库（段/关键字是否存在、枚举和类型是否合法、不可重复的段是否重复），一层是物理一致性（基组赝势价电子是否匹配、电荷与自旋是否矛盾、smearing 与 OT 是否冲突、MD 系综设置是否完整等）。
带 `@IF/@SET` 的输入不会被展开：这类值会被跳过而不是瞎猜。

## 严格模式

*标识*：`validate_strict`

勾上之后警告也当失败，适合放进脚本或 CI 里卡质量。
平时建议不勾：警告里既有真问题（cutoff 偏小、多重度与 UKS 不一致），也有只是提醒的（用了默认优化器），一刀切会让真正的错误被淹没。

## 搜索

*标识*：`keyword_search`

离线搜索手册关键字，不需要联网、不需要浏览器。
**中英文都可以**：输入 `cutoff` 或「截断能」都能找到。
中文部分是这样分工的：
• 蓝色块的【中文说明】是 cp2kbuild 自己写的高频关键字解释，覆盖 104 个常用关键字，**不是手册译文**；
• 没有中文说明的词条会显示英文原文，并在下面列出「术语对照」，把原文里出现的专业词逐个标出来（98 条术语）；
• 点「术语表」可以一次看完所有术语。
为什么不全篇翻译：手册有两万多个关键字，整篇机器翻译会把技术描述译错，而译错的描述比英文原文更危险。

## 限定 section

*标识*：`keyword_section`

限定搜索范围到某个 section，路径用 `/` 分隔，例如 `FORCE_EVAL/DFT/SCF`。
留空则在整本手册里搜。
点右边的「列出该 section 的全部关键字」可以看一个 section 的全部选项和默认值——比翻手册快，而且每个关键字后面会标出它的中文名。

## 数据目录

*标识*：`data_dir`

CP2K 的 data 目录（放 BASIS_MOLOPT、GTH_POTENTIALS 等）。
程序启动时会自动探测常见位置和 CP2K_DATA_DIR 环境变量。
**基组和赝势的价电子匹配检查依赖它**；指定不了就只能生成未经核对的输入。
打包成 exe 后这是唯一的运行时外部依赖。

## 元素

*标识*：`data_elements`

要查哪些元素，逗号分隔，例如 `H, C, O, Fe, Pt`。
难覆盖的元素（镧系、锕系、部分 4d/5d）会自动换基组家族，结果表里能看到换成了哪个。

## 基组偏好

*标识*：`data_target`

同「基组偏好」：周期性体系选 SR（短程），孤立分子选带弥散的。
同一个元素在不同偏好下会选到不同的基组名，比如 Fe：
• 周期性 → DZVP-MOLOPT-SR-GTH (q16)；
• 孤立分子 → 若该家族没有 Fe，自动退到 SR 版本。
下面那张表会显示它到底选了哪个、以及价电子数是多少——价电子数必须和赝势一致，这是 CP2K 最容易崩的地方。

## scan_source

*标识*：`scan_source`

模板输入从哪儿来，两条通道：
• **用「生成输入」页此刻的参数**——直接拿你正在调的那一套设置，工具渲染出一份新的 `template.inp`（文件名 `<project>.inp`，和生成页的产物同源）；
• **已有输入（`.inp`，原样保留）**——挑一个现成的 `.inp`，工具只动 `CUTOFF`/`REL_CUTOFF` 和 `RUN_TYPE`，其余字节一个不改。
两条通道都会：把 `&MGRID` 里的 `CUTOFF` / `REL_CUTOFF` 换成占位符 `LT_cutoff` / `LT_rel_cutoff`（输入里已经有 `LT_cutoff`/`@SET CUTOFF LT_cutoff`/`${CUTOFF}` 就沿用，只把值换成占位符）；把 `BASIS_SET_FILE_NAME` / `POTENTIAL_FILE_NAME` 指向的文件，以及 `@INCLUDE` 链上的文件一起复制到输出目录——生成的脚本要把它们 `cp` 进每个参数点目录。
为什么要占位符：官方脚本用 `sed -e "s/LT_cutoff/.../g"` 生成每一档。如果模板里没有可以替换的东西，`sed` 会一声不响地生成 N 份**一模一样**的输入——所以工具要么找到它、要么自己插上，并在结果里写清楚改了什么。
**`RUN_TYPE` 一定被改成 `ENERGY`**：网格收敛测试只算单点能，`MD`/`GEO_OPT`/`CELL_OPT` 那类跑法既慢又和收敛曲线无关。工具会连 `@SET RTYPE xxx` 这种预处理器写法一起改掉，并在结果里列出因此失效的设置（步数、系综、打印轨迹等）。
**`&EXT_RESTART` 一定被整段注释掉**：那一段是「接着上一次算」，而网格扫描的每一档必须是独立单点能。留着它，每档都会去读同一个 restart 文件——集群上通常没有那个文件，CP2K 在检查输入时就 `[ABORT] The specified OLD file <x-1_40000.restart> cannot be opened`（`common/cp_files.F:406`），一个点都跑不出来；文件就算在，后面的档也会读到前面档刚写的东西。注释（每行前加 `#`）而不是删除：原文还在，随时能改回来。藏在 `@INCLUDE` 文件里的那种工具改不了，会在结果里点名让你自己删。
两条通道都按官方教程的「省机时」协议补写：`SCF_GUESS ATOMIC` + `MAX_SCF 1` + `IGNORE_CONVERGENCE_FAILURE TRUE` + `PRINT_LEVEL MEDIUM`（`MEDIUM` 才打印 `MULTIGRID INFO`，没有它分析脚本数不出每个网格层落了几个高斯）；「生成输入」通道由工具直接写进去，「已有输入」通道只提醒一次、不擅自改你的文件。

## scan_cutoffs

*标识*：`scan_cutoffs`

**第 1 步**要扫的 CUTOFF（Ry），逗号或空格分隔。
默认是官方脚本那一行原样：**50 100 150 200 250 300 350 400 450 500**（50…500 Ry 每 50 一档，10 个点；官方教程说 1e-6 Ry 的精度就落在这个范围里，8 原子 Si 的例子里 250 Ry 起收敛）。
这一步的 REL_CUTOFF 固定不动（右边那个框）。
想省机时就把点删掉一些（按基组起手：SZV 300 起、DZVP 400 起、TZVP 600 起），或者干脆在服务器上改 `cutoff_inputs.sh` 里那一行 `cutoffs=`——但那是经验值，最终仍要按自己的数据看 `cutoff_data.ssv`。

## scan_fixed_rel_cutoff

*标识*：`scan_fixed_rel_cutoff`

**第 1 步固定住的 REL_CUTOFF**（Ry），默认 **60**——官方教程原话是「先把它设成一个相对高的值」，而 60 Ry 对大多数体系足够，第 2 步再回头确认。
它是绝对值、不随 CUTOFF 缩放，所以**必须**固定住才能扫 CUTOFF：同一个 REL_CUTOFF 在不同 CUTOFF 下会落到不同的网格档上。

## scan_rel_cutoffs

*标识*：`scan_rel_cutoffs`

**第 2 步**要扫的 REL_CUTOFF（Ry）。默认同样是官方脚本那一行：**10 20 30 40 50 60 70 80 90 100**（每 10 一档，10 个点；Si 的例子里 60 Ry 起收敛）。
REL_CUTOFF 决定每个高斯函数被放到哪一档网格上：太小，函数全挤到最粗档，CUTOFF 再大也白费；太大则是白花机时。判据在 `rel_cutoff_data.ssv` 里看，同时注意 `NG on grid N` 那几列——最细那一档应该还有函数，而且各档都该被用到。

## scan_fixed_cutoff

*标识*：`scan_fixed_cutoff`

**第 2 步固定住的 CUTOFF**：第 1 步从 `cutoff_data.ssv` 里定下来的那个值。
留空也可以——生成的三个 `rel_cutoff_*.sh` 里会先写 `CHANGE_ME`，**没改成真实值之前脚本会直接拒绝运行**（而不是安静地跑一堆目录名不对的算例）。
为什么必须换 CUTOFF 重扫：多级网格的每一档跟着 CUTOFF 缩放，REL_CUTOFF 却是绝对值，在别的 CUTOFF 上测出来的值不能直接搬过来。

## scan_outdir

*标识*：`scan_outdir`

七个文件写到哪里（目录不存在会自动建）：
`template.inp`、`cutoff_inputs.sh`、`cutoff_run.sh`、`cutoff_analyse.sh`、`rel_cutoff_inputs.sh`、`rel_cutoff_run.sh`、`rel_cutoff_analyse.sh`，外加要一起复制的基组/赝势/`@INCLUDE` 文件。
整个目录拷到服务器上就能用（脚本是 POSIX `sh`，`sh script.sh`、`bash script.sh`、`./script.sh` 都能跑）。

## scan_result

*标识*：`scan_result`

生成结果：写了哪些文件、要复制哪些文件，以及**工具自己做的判断**——
• 输入里有没有可替换的 `CUTOFF`/`REL_CUTOFF`（没有会点名）；
• 基组/赝势文件在不在本地（不在就提醒你自己放进去）；
• 输入是不是官方那种「省机时」写法（不是就给出要加的三行）；
• 第 2 步的 CUTOFF 有没有填。
内容和命令行 `cp2kbuild scan --input ... --outdir ...` 的输出**逐字节相同**：界面只是同一份代码的入口。
生成完就是：拷到服务器 → `sh cutoff_inputs.sh && sh cutoff_run.sh && sh cutoff_analyse.sh` → 看 `cutoff_data.ssv` 定下 CUTOFF → 填进三个 `rel_cutoff_*.sh` 再跑第二套。

