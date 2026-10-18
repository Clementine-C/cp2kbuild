# cp2kbuild — CP2K 输入文件生成 / 收敛 / 校验工具

一个纯 Python（零第三方依赖）的 CP2K 输入文件工具链：

- **生成**：从结构文件（xyz / pdb / POSCAR / cif / 已有 .inp / 上次运行写出的 `-1.restart`）一键生成完整可运行的 `.inp`
- **图形界面**：PySide6 写的六页签窗口，参数一改右侧预览立刻重算；可打包成免安装 exe
- **自动配基组与赝势**：扫描本机 CP2K 的 `data/` 目录，按**价电子数（`-qN`）匹配**选取基组和赝势
- **收敛测试**：写出 CP2K 官方教程那套 CUTOFF / REL_CUTOFF 脚本（`template.inp` + 6 个 `sh`），模板可以由工具从当前参数现场生成，也可以直接用你已有的 `.inp`（`template.inp` 里的 `RUN_TYPE` 一定是 `ENERGY`）；跑和看结果都在服务器上
- **校验**：内置手册数据库（CP2K **2026.2**：2843 个 section、25962 个关键字），可以校验生成的输入，**也可以校验你自己手写的 `.inp`**（仓库里带的就是这一份；想换 2025.1 等其他版本，用 `python tools/build_db.py --src <cp2k>/src` 自己生成一份放进 `cp2kbuild/data/`）
- **离线手册查询**：`cp2kbuild keyword`，不用开浏览器

核心部分零第三方依赖（只用标准库，Python ≥ 3.9）；只有图形界面需要 PySide6。

### 拿到代码

```bat
git clone https://github.com/Clementine-C/cp2kbuild.git
cd cp2kbuild
python -m cp2kbuild --version          :: 控制台版，不需要装任何东西
pip install -e ".[gui]"                :: 要窗口版才装 PySide6
python -m cp2kbuild gui                :: 装完也可以直接 cp2kbuild-gui
```

仓库里不带 `dist/*.exe`（打包产物，用 `python packaging/build_exe.py` 自己生成）；
`cp2kbuild/data/manual_db_2026_2.json`（26 MB 的手册库）是带上的，没有它就只能做
物理一致性检查，查不了手册。

---

## 0. 先回答"有没有现成的工具"

有，但它们的定位和本工具不同：

| 工具 | 做什么 | 缺什么 |
|---|---|---|
| [cp2k-input-tools](https://pypi.org/project/cp2k-input-tools/) | 官方生态：`cp2klint` 语法检查、`fromcp2k`/`tocp2k` JSON↔inp 互转、`cp2kgen` 批量改参数、LSP | 不做"从结构到输入"的生成；不选基组/赝势；不校验物理量一致性 |
| [ASE](https://wiki.fysik.dtu.dk/ase/) | `ase.io.write(..., format='cp2k-in')`，或者用 `CP2K` calculator | 需要自己搭 calculator；输入是"原子+参数"而非任务模板 |
| [pymatgen](https://pymatgen.org/) `pymatgen.io.cp2k` | 有 CP2K 输入集（input sets） | 面向材料/周期性体系，接口较重，默认值偏 Materials Project 风格 |
| [atomate2](https://materialsproject.github.io/atomate2/) | 工作流（WF）级别的 CP2K input generator | 面向自动化流程，单机手算用不上 |
| [aiida-cp2k](https://github.com/aiidateam/aiida-cp2k) | AiiDA 插件 | 需要先上 AiiDA |
| [Avogadro2](https://www.cp2k.org/tools:avogadro2) | 图形界面生成输入 | 交互式，不利于批量/脚本化 |
| PyCP2K | 生成输入 + 解析输出 | 老项目，维护少，价电子匹配要自己做 |

**它们共同的空缺**：没有一个工具会去看你机器上的 `data/BASIS_MOLOPT`、`GTH_POTENTIALS`，然后告诉你"你选的 Fe 基组是 q16、赝势却选了 q8，CP2K 会直接崩"。本工具的卖点就是这一层。

---

## 1. 用法

### 项目名与重启文件的"继承"

**`PROJECT_NAME` 默认就是 `cp2k`**（可以用 `--project` 或界面「项目名」改），原因就是上一条：
CP2K 的波函数文件叫 `<项目名>-RESTART.wfn`、运行续算文件叫 `<项目名>-1.restart`——**项目名固定，
重启文件才会被下一次运行继承**。这也是你们模板里的做法（`@SET SYSNAME cp2k`，
于是 cp2k-1.restart / cp2k-RESTART.wfn / cp2k-RESTART.kp 一直在同一套名字上滚）。

- 默认**输入文件名**仍然跟着结构文件走（`-s water.xyz` → `water.inp`，`-o` 可改），
  这样同一个目录里放两个体系不会互相覆盖输入文件；但**项目名要自己改**，
  否则两个体系会共用 `cp2k-RESTART.wfn`（CP2K 只在原子数不同时才会警告并回退）。
- 工具会**主动看一眼目录里已有什么**：
  - CLI：生成时若发现同名重启文件，会打印
    `note: cp2k-RESTART.wfn exists -> SCF_GUESS RESTART reads it (inherits the previous wave function)`；
  - GUI：重启/续算那一栏直接写出来
    「会写成：cp2k-RESTART.wfn、cp2k-1.restart｜这个目录里已经有：cp2k-RESTART.wfn（SCF 初猜会继承它）、cp2k-1.restart（可以「用它续算」）」，
    并给一个**「用它续算」按钮**：一键把 `&EXT_RESTART` 指到那个 `-1.restart` 上
    （同时打开 `RESTART_DEFAULT`，即位置/速度/计数器都以它为准）。

### 重启与续算

工具支持三种"接着上次算"的方式，都是 CP2K 里不同的机制，别混用：

| 想干什么 | 机制 | GUI | CLI |
|---|---|---|---|
| 复用上次的波函数（省 SCF 时间） | `SCF_GUESS RESTART`（**默认**） + `WFN_RESTART_FILE_NAME` | 「初始波函数 / 文件」 | `--scf-guess RESTART --wfn-restart-file …` |
| 接着跑 MD / 几何优化（接上位置、速度、计数器） | 顶层 `&EXT_RESTART` + `RESTART_FILE_NAME` | 「从上次续算」+「用它续算」 | `--restart run-1.restart` |
| **换任务**接力（只取速度/计数器/控温控压状态…） | `&EXT_RESTART` 的 `RESTART_DEFAULT F` + 点名 `RESTART_*` | 「从那个文件里取什么（任务接力）」 | `--restart run-1.restart --restart-take vel` |
| 让下一次能接着算：写波函数文件 | `&SCF/&PRINT/&RESTART`（**默认写**） | 第 5 节「输出」→「波函数文件」 | `--no-wfn-print` 关掉 |
| 让下一次能接着跑：写运行重启文件 | `&MOTION/&PRINT/&RESTART`（**默认写**） | 第 5 节「输出」→「重启文件」 | `--no-motion-restart` 关掉 |
| 跑的过程中多久写一次 | 两个 print key 的 `&EACH` | 第 5 节里各自的「每 N 步」 | `--wfn-restart-every` / `--restart-every` |
| 波函数那个间隔**按哪一级**计数 | `&EACH` 里的层级名（`MD` / `GEO_OPT` / `CELL_OPT` / `QS_SCF`） | 「波函数文件」那一行的层级下拉框 | `--wfn-restart-level` |
| 最后一步要不要也写 | 两个 print key 的 `ADD_LAST` | 第 5 节里各自的「最后一步也写」 | `--no-wfn-add-last` / `--no-restart-add-last` |
| 留几份旧的 | 两个 print key 的 `BACKUP_COPIES` | 第 5 节里各自的「备份 M 份」 | `--wfn-backup-copies` / `--restart-backups` |

> **界面上「写文件」和「读文件」分开了**：两个重启文件是**输出**，所以和第 5 节的打印级别、
> Molden、cube 放在一起；第 6 节「续算（`&EXT_RESTART`）」只剩"我从哪儿接着算"，
> 也就是 `&EXT_RESTART` 本身。这样不会再把"我要写什么"和"我要读什么"混在一组里。

**`&EACH` 和 `ADD_LAST` 是两个独立设置**，以前我把它们塞进一个输入框（用 `0` 兼作"只在最后写"），
现在分成两个控件，因为它们的组合才是真正的行为：

| 每 N 步 | 最后一步也写 | 结果 |
|---|---|---|
| 0（默认，波函数文件） | ✅（默认） | 只在结束时写一份——单点、几何优化最常用 |
| 20（默认，重启文件） | ✅（默认） | CP2K 的传统行为：过程中每 20 步 + 最后一步（掉机器最多丢 20 步） |
| N | ❌ | 只有过程中的第 N、2N… 步，最后那一步反而不写 |
| 0 | ❌ | **什么都不会写**（校验器会给 `restart-print-never` 警告：开关是开的，文件却永远不出现） |

两个文件的**默认值不一样**，因为用途不同：波函数文件（`.wfn`，一个可能上百 MB）用
「间隔 0 + `ADD_LAST NUMERIC`」，也就是**只在结束时写一次**；运行重启文件（`-1.restart`，
很小且是中断后唯一的依靠）保持 `MD 20` + `ADD_LAST NUMERIC`，和 CP2K 自己的默认一致。

**「每 N 步」里的"步"按哪一级数，是由 `&EACH` 里的层级名决定的**——CP2K 的 `&EACH` 把每个
迭代层级都做成了合法关键字（`MD`、`GEO_OPT`、`CELL_OPT`、`QS_SCF`、`PINT`、`BAND` …）。
同一个 `500`，在 `QS_SCF` 下是"某一次力计算里 SCF 的第 500 次迭代"，在 `MD` 下才是
"每 500 个 MD 步"：**MD 任务里写 `QS_SCF 500` 等于这个间隔永远不会触发**，真正写文件的
只有 `ADD_LAST`。工具现在按任务自动选层级（MD → `MD`，几何优化 → `GEO_OPT`，晶胞优化 →
`CELL_OPT`，单点 / 频率 / 能带 / PDOS → `QS_SCF`），界面上那一行还有一个下拉框可以手动
指定；读别人的输入时也按实际出现的层级取值，校验器还会在「层级和 RUN_TYPE 对不上」时
给出 `restart-print-level-mismatch` 警告。

**初猜默认就是 `RESTART`，而且第一次算不会因此失败**：CP2K 自己在 `qs_initial_guess.F`
里会先看文件在不在，不在就打一条 warning 然后回退成 ATOMIC 初猜；连"文件在但原子数
不一样"（换了体系、复用了同一个项目名）也会回退。所以默认值既能省 SCF 步数，又不会
让第一次运行报错。

真正要小心的只有一种情况：**同名、同原子数、但换了元素或基组**的陈旧 `.wfn`。CP2K 不
检查这些，会把一个形状对、内容错的初猜读进去——不会崩，但可能多算很多步或者收敛到别的
解。换体系时换个项目名，或者把旧 `.wfn` 删掉。k 点计算（`DIAGONALIZATION` + `&KPOINTS`）
读的是 `<项目名>-RESTART.kp`，不是 `.wfn`。

两个最容易踩的坑，工具都会主动提醒：

- **`RESTART_DEFAULT T` 会覆盖你输入里的坐标、速度、晶胞和步数计数器**，以续算文件为准。想只取其中几项就 `RESTART_DEFAULT F`（GUI 上那个勾去掉、命令行 `--no-restart-default`），再显式打开需要的项。
- **`&EXT_RESTART` 对单点/频率没有任何作用**（它续的是带迭代步的计算），用了会打印 `WARN`，校验时也会给 `ext-restart-ignored`。

`.wfn`（波函数，给 SCF 当初猜）和 `-1.restart`（整个运行的状态，给 `&EXT_RESTART` 续轨迹）
是两个不同的文件，可以只用其中一个。两个 print key 的 `&EACH` / `ADD_LAST` / `BACKUP_COPIES`
现在都显式写进文件，所以 `PRINT_LEVEL` 调成 `SILENT` 时也仍然会写。

> **要不要用 `@IF` 做"有 restart 文件就用 RESTART，没有就用 ATOMIC"？**
> 不需要，CP2K 已经内置这个回退。CP2K 的预处理器（`@SET` / `@IF` / `@INCLUDE` / `@ENDIF`）
> 只能做字符串比较和 `@INCLUDE`：**没有**"文件是否存在"的判断、**没有 `@ELSE`**、
> `@SET` 也不读环境变量。所以那种写法只能靠人手动改一行开关（例如
> `@SET RESTART TRUE` + `@IF ( ${RESTART} == TRUE )`），做不到自动回退。


### 任务接力：GEO_OPT → NVT → NPT

FIST 经典 MD 常见的一套流程是"先优化、再控温、再控压"三棒接力。CP2K 里接力就是
`&EXT_RESTART` 读上一棒写出来的 `<项目名>-1.restart`，**关键在"只取哪几样"**。

先记住一件事：**CP2K 写出来的 `-1.restart` 本身就是一份完整输入**，里面有那一棒最后的
`&COORD` 和 `&CELL`。所以工具把上一棒的 restart 当**结构文件**读进来以后，坐标和晶胞
已经在本次输入里了——**这时候 `&EXT_RESTART` 只值得用来接那些"内联输入给不了"的东西**：
速度（`&VELOCITY` 不会被内联）、步数计数器、恒温器/压浴状态。`pos`/`cell` 反而通常是多余的。

```bat
:: 第一棒：几何优化，从头算（默认就会写 1_geo-1.restart）
:: 力场路线不需要结构文件：原子和坐标都从 --mm-coord-file 读，盒子用 --cell 给
cp2kbuild new --task geo_opt --method fist ^
    --mm-parm-file mix.prmtop --mm-coord-file mix.pdb --cell 24.4,24.4,24.4 -o 1_geo.inp

:: 第二棒：NVT —— 上一棒的 restart 直接当结构传：坐标和晶胞被读成 &COORD/&CELL，
:: 输入自己就是完整的，连 &EXT_RESTART 都不需要（速度按 300 K 重新起、步数从 0 开始）
cp2kbuild new --task md --ensemble NVT --temperature 300 --steps 200000 ^
    --method fist --mm-parm-file mix.prmtop --structure 1_geo-1.restart ^
    -o 2_nvt.inp

:: 第三棒：NPT —— 结构还是从上一棒的 restart 读，另外把速度和控制状态接过来
cp2kbuild new --task md --ensemble NPT_I --pressure 1.0 --steps 500000 ^
    --method fist --mm-parm-file mix.prmtop --structure 2_nvt-1.restart ^
    --restart 2_nvt-1.restart --restart-take vel,thermostat,barostat -o 3_npt.inp
```

GUI 上是「2. 任务与参数」→「从上次续算」填文件名，下面「从那个文件里取什么（任务接力）」
选一个预设（**按实际用到的可能性排序**，越上面越常用）：

| 预设 | 写出来的 | 什么时候用 |
|---|---|---|
| 只取速度（MD → MD 接力，最常用） | `RESTART_DEFAULT F` + `RESTART_VEL T` | **MD → MD**：接着跑轨迹，坐标晶胞已随结构文件继承 |
| 速度 + 热浴/压浴 | `+ RESTART_THERMOSTAT/BAROSTAT T` | **MD(NVT) → MD(NPT)**：接着控温控压，压浴才有历史 |
| 只取步数计数器 | `+ RESTART_COUNTERS T` | 想让新任务从上一轮的步数接着编号（一般**不要**） |
| 全部（CP2K 默认） | `RESTART_DEFAULT T` | 同一套参数、同一个任务「像没中断过一样」接着跑 |
| 只取坐标 | `RESTART_DEFAULT F` + `RESTART_POS T` | 坐标通常已经随结构文件继承，多半用不上 |
| 坐标 + 晶胞 | `+ RESTART_CELL T` | 同上：盒子也随 `&CELL` 继承了；只有没把 restart 当结构时才需要 |

> 拖着 restart 文件进窗口（或把它填进结构框）**不会再自动打开第 6 节**：坐标和晶胞已经
> 通过工具的继承机制读进来了，凭空写一个只取 `pos` 的 `&EXT_RESTART` 只是重复一遍。
> 要接速度/计数器/控制状态时，自己把「从上次续算」填上——右侧会一直提示该填什么。

预设按**常用程度**排序（越上面越常用），但**默认选中的仍然是「全部」**——它和下面
「都以该文件为准」那个勾是一致的；只有你主动挑一个预设，工具才会改成
`RESTART_DEFAULT F` + 点名那几项。

选了"取哪些量"以后，「都以该文件为准」那个勾会自动关掉并变灰——因为这时工具写出的
就是 `RESTART_DEFAULT F`。

**说明会跟着系综走**，因为"MD"这个词本身说不清后果：

```
只取 VEL：写 `RESTART_DEFAULT F` + 这几项 `T`。作用：把 restart 里的 `&VELOCITY` 接上
（这是内联输入给不了的）。本次是 NPT_I：步数从第 0 步起、压浴从零开始（不取就没有压力历史）。
```

改系综、改温度时这行字实时重算，所以接力前就能看清"这次到底接上了什么、什么重新起"。

**填了 restart 文件为什么还没有预览？** 因为 `&EXT_RESTART` 只在 CP2K **运行时**用文件里
的坐标覆盖，输入自己仍然要定义体系。但 restart 文件本身就是一份完整输入，**里面有坐标和
晶胞**——文件填好后右边会出现「读它的坐标」，点一下就把体系读进来（结构面板会标明
"从 1_geo-1.restart 读来的"），生成的输入里直接写上 `&COORD`/`&CELL`，这就是本次要算的体系。
也可以照旧自己选结构文件；FIST 路线还可以只用「坐标文件」+「晶胞」。
命令行里的等价写法就是把它当结构文件传：`--structure 1_geo-1.restart`。

把文件填进去（或点「…」选）之后，下面那行提示会**直接念出这个 restart 文件里有什么**：

```
1_geo-1.restart：由 GEO_OPT 写（坐标 24 个原子、晶胞、GEO_OPT 迭代计数器）
```

如果选的取用项那个文件里根本没有（比如从几何优化的文件里取速度），提示会变成警告：
取了也没用，CP2K 会用本次输入里的值。这正是手写接力最容易静默出错的地方。

生成出来的就是这么几行，含义一目了然（这里以第三棒"只取速度 + 控温控压状态"为例）：

```
&EXT_RESTART
   RESTART_FILE_NAME 2_nvt-1.restart
   # 先全部关掉，下面只打开需要的几项
   RESTART_DEFAULT F
   # velocities：内联输入给不了这个（restart 里的 &VELOCITY），MD 接着跑就取它
   RESTART_VEL T
   # thermostat state（接着控温才需要）
   RESTART_THERMOSTAT T
   # barostat state（接着控压才需要）
   RESTART_BAROSTAT T
&END EXT_RESTART
```

为什么必须是"`RESTART_DEFAULT F` + 点名的几项 `T`"：`&EXT_RESTART` 有 38 个
`RESTART_*` 开关（位置、速度、晶胞、步数计数器、恒温器、压浴、随机数、metadynamics…），
**没写出来的全部由 `RESTART_DEFAULT` 兜底**（默认 `T`）。`T` = 文件里有什么就拿什么，
同一套参数接着跑正合适；换了任务就会出事——GEO_OPT 的文件里没有速度，但 MD 读进去
以后速度、步数都会被打乱。所以接力的正确姿势是：`F` 全关，再点名打开。

| 上一棒 → 这一棒 | 取什么 | 为什么 |
|---|---|---|
| GEO_OPT → MD | 什么都不取，或 `pos` | 把上一棒的 restart 当结构，构型已经内联进来；速度按设定温度重新起 |
| CELL_OPT → MD | 什么都不取，或 `pos` + `cell` | 优化后的盒子也在 restart 里，`&CELL` 一起继承；只有没把 restart 当结构时才要 `cell` |
| MD → MD（换温度/换系综） | `vel`（+ `thermostat`/`barostat`） | 轨迹要连续就得接速度；`&VELOCITY` 不会被内联，这是 `&EXT_RESTART` 真正不可替代的地方 |
| NVT → NPT | `vel` + `thermostat` + `barostat` | 接着控温控压，压浴才有历史 |
| 任何 → 任何 | **不要** `counters` | 取了步数计数器，新任务就从上一轮的步数接着数，轨迹编号、平均值、`STEP_START_VAL` 全都会怪 |

**不需要手改 restart 文件，也不一定要用 `&EXT_RESTART`。** CP2K 写出来的 `-1.restart`
本身就是一份完整输入，把它当结构文件读进来，坐标和晶胞就已经在本次输入里了；`&EXT_RESTART`
只剩"接速度和状态"这一件事。之所以不推荐手改文件：下一版 CP2K 加一个 `RESTART_*` 关键字、
或者文件里多出一个你没注意的段，手改就会静默接错东西。用"取哪些量"来声明意图更清楚，
生成的输入里也会把每个取用项的原因写成注释。

`cp2kbuild mmcheck` 之外，工具还能读一个 restart 文件并告诉你**它里面有什么可接力的**：
`restart_summary()`（库函数）返回"由 GEO_OPT 写（坐标 24 个原子、晶胞、GEO_OPT 迭代
计数器）"这样的说明，GUI 的「从上次续算」下面显示的就是它。

**把 `-1.restart` 拖进结构框（或载入它）时，第 6 节不会自动接上**：坐标和晶胞已经通过
`&COORD`/`&CELL` 继承了，凭空写一个只取 `pos` 的 `&EXT_RESTART` 只是把同一份坐标再覆盖一遍。
工具会在结构面板和续算那一栏写明原因，并告诉你"要接速度/计数器/恒温器状态时才需要填"。
已经自己填过的续算文件不会被改动。


### 读回一份已有输入的参数（「读参数」）

CP2K 输入文件——以及 `-1.restart`，因为 CP2K 把它写成一份完整输入——不只记着坐标，
还记着**这次是怎么算的**。结构框右边的「读参数」把它读回表单：

```bat
cp2kbuild new --task md --structure prev.inp --restart prev.inp --restart-take vel
:: GUI：结构框填 prev.inp → 点「读参数」 → 表单变成那份文件的样子 → 再改你想改的
```

读到的包括：任务（`RUN_TYPE`，能区分 band/PDOS 这两种同为 `ENERGY` 的跑法）、
`METHOD`（Quickstep/FIST 会切换页面）、SCF 路线与全部子参数、泛函与色散、
`CUTOFF`/`REL_CUTOFF`/`NGRIDS`、体系电荷与多重度、MD 系综/温度/步数/时间步/
恒温器压浴/随机种子、GEO_OPT 与 CELL_OPT 的优化器与外压、频率强度与热化学、
打印（受力/Mulliken/偶极/Molden 全套）、两个重启文件的 `&EACH`/`ADD_LAST`/
`BACKUP_COPIES`、`&EXT_RESTART` 的文件与取用项，以及 FIST 的力场路线、
参数/拓扑/坐标文件、1-4 缩放、Ewald、ε/σ 表、`&CHARGE`、组分表（`&MOL_SET`）。

- **表单与文件对齐**：读完再生成，同一个体系应当写出同一份输入——自测就是拿
  18 类输入（含 FIST 的 AMBER / `&MOL_SET` / 纯对势 / 显式 k 点 / 温度打印 / 氘代）跑"生成 → 读参数 → 再生成"，
  要求两份文件逐字节相同；
- **每一项都能跳**：读完后列出读了哪些参数，**点参数名直接滚到并高亮那个控件**；
- **不会悄悄丢东西**：界面里没有对应控件、或取值不在下拉框里的会单独列出来
  （"没对上：…"），文件里有而工具不建模的段（`&KPOINTS`、`&PROPERTIES`…）也会点名；
- **只读参数，不动结构**：体系始终来自上面那个文件（`&COORD`/`&CELL`）——
  参数和结构分别对，互不干扰。

非 CP2K 的文件（xyz/pdb/cif/POSCAR）没有参数可读，「读参数」按钮保持不亮。


### 写 Molden 文件（分子轨道 / 振动模式）

想让 Molden、Avogadro、Jmol 画出轨道或能级图，就在 DFT 输入里加
`&DFT/&PRINT/&MO_MOLDEN`：

```bat
cp2kbuild new --task energy --structure water.xyz --vacuum 12 --molden ^
    --molden-file MOS-PBE --molden-nlumo -1 --molden-write-pseudo --molden-write-cell
```

GUI 上是「2. 任务与参数」里的「写分子轨道（&MO_MOLDEN）」，勾上以后下面几个控件才可编辑：
文件名（默认 `MOS` → `<项目名>-MOS.molden`）、空轨道数 `NLUMO`、文件里的单位、GTO 形式、
要不要带 `[Pseudo]` 和 `[Cell]` 块。

几个和手册默认值不同的地方（工具按"打开就能看"来定默认）：

| 参数 | CP2K 默认 | 工具默认 | 为什么 |
|---|---|---|---|
| `UNIT` | `BOHR` | **`ANGSTROM`** | 观看器按 Å 理解坐标，用 Bohr 原子会错位 |
| `GTO_KIND` | `SPHERICAL` | `SPHERICAL` | 和 CP2K 一致，也是观看器期望的形式（手册点名 `CARTESIAN` 与 VMD 不兼容） |
| `NLUMO` | `0` | `0` | 只写占据轨道；要看导带就把 `NLUMO` 设成 `-1`（全部）或具体条数 |
| `WRITE_PSEUDO` / `WRITE_CELL` | `F` | 不勾 | 赝势块按需带上；`[Cell]` 只有周期性体系才有意义 |

两个**静默**的坑，工具都会提前提醒：

- **带 k 点的计算不写 Molden 文件**：CP2K 在 `qs_scf_post_gpw.F` 里直接
  `IF (.NOT. do_kpoints)`，不报错、不警告，文件就是不出现——校验器会给
  `molden-with-kpoints` 警告。想看轨道请用 Γ 点的分子/团簇模型。
- **`NLUMO > 0` 需要空轨道**：OT 路线在 SCF 后自己生成；对角化路线要有 `ADDED_MOS`
  （OT 关掉时才被读），工具会按需要补上，不需要你手动算该加几条。

频率任务还有 `&VIBRATIONAL_ANALYSIS/&PRINT/&MOLDEN_VIB`（**默认打开**），把简正模写成
`<项目名>-VIBRATIONS.molden`——在 Molden 里可以直接把某个模式动起来看。用
`--no-molden-vib` 关掉。


### 参数耦合：哪些参数"现在用不上"（反应式启用）

CP2K 里大部分参数在别处是**静默无效**的：`ADDED_MOS` 在 OT 路线下不被读、`&MIXING` 不写进 OT 的输入、
`TIMECON` 没有恒温器就没处放、`INTENSITIES` 对周期体系不支持、`&EXT_RESTART` 对单点毫无作用……
"我明明设了它却没生效"几乎都出在这里。所以工具把**条件集中成一张表**（`cp2kbuild/couplings.py` 的 `RULES`，
共 161 条，每条都附手册页面或 CP2K 源码文件作为依据，渲染版见 `packaging/couplings.md`），三处共用：

- **界面**：用不上的控件**灰掉**，并在该行的「?」里写清**当前**为什么用不上 + 依据；
  换任务 / 换 SCF 路线 / 换周期性 / 换系综时实时重算（不是一次性判断）。
  比如走 OT 时「空轨道数」和混合参数一起变灰；开 SMEAR 后 `ENERGY_GAP`/`SAFE_DIIS` 按预条件子/最小化器
  分别灰显；晶体上 「真空层」变灰（有晶胞就完全忽略它）、`MULLIKEN`/`MOMENTS` 变灰
  （周期性体系不请求）、`INTENSITIES` 变灰；分子 + 真空层时「分数坐标」反而**变亮**
  （真空层会生成 `&CELL`，分数坐标这时才有意义）。
- **命令行**：只对**你确实要求过**的参数发警告：
  - `--temperature 500` 配单点能 → `WARN --temperature 500.0 不会生效：目标温度是 MD 的设置。（依据：…）`
  - `--vacuum 20` 用在晶体上、`--mulliken` 用在周期体系、`--ot-energy-gap` 配 FULL_KINETIC、
    `--restart` 配单点能、`--thermostat NOSE` 配 NVE …… 都会点名；
  - **明确关掉的东西不提醒**：`--no-intensities` 不会警告 intensities。
- **自测**：`tools/selftest.py` 逐条走这张表——每条规则都要能找到一个让它失效的输入、
  给出对应标志时确实警告、而默认值不误报（163/163 通过，其中 9 条是**界面/库独有**的字段：
  命令行已冻结，自测会核对 CLI 解析器里确实没有它们的开关，`couplings.md` 里那一列写「—」）。
  元动力学那一条（`metadyn`，只在 MD 任务里生效）两侧都有开关：界面把整页变灰，
  命令行给 `--metadyn`/`--cv`/`--metavar`/`--meta-*` 时发 `WARN`。
  另外还会核对**警告里点名的开关在 CLI 里真的存在**——表里存的拼写有两种来源（`--no-forces`
  这类否定式、`--lj`/`--mm-charge` 这类和字段名不一致的别名），写错就会出现
  "请去掉一个解析器根本不认识的参数"这种骗人提示，所以这也有回归测试兜着。
  第一版表里就有两处错误
  （`restart_default` 字段名写错、`wfn_restart_print` 相关四条从未触发），是这条测试抓出来的。

判断"用不上"用的是**生效后的结构**（真空层/晶胞覆盖/周期性都算进去），不是磁盘上的原文件，
所以不会出现"文件里没有晶胞所以灰了，但实际会生成晶胞"这种错判。

### SCF 迭代次数：内层与外层

| 你模板里的名字 | CP2K 关键字 | GUI | CLI |
|---|---|---|---|
| `SCF_NCYCLES` | `&SCF/MAX_SCF`（内层，默认工具写 30、CP2K 默认 50） | 「内层迭代 MAX_SCF」 | `--max-scf` |
| `SCF_OCYCLES` | `&SCF/&OUTER_SCF/MAX_SCF`（外层，默认工具写 20、CP2K 默认 50） | 「外层 SCF（次数/EPS）」 | `--outer-max-scf` |
| — | `&SCF/&OUTER_SCF/EPS_SCF`（留空 = 与内层相同） | 同上第二个框 | `--outer-eps-scf` |

做截断能扫描那种"只迭代固定步数"的用法（内层 1、外层 0）**必须同时**打开 `IGNORE_CONVERGENCE_FAILURE`，否则 CP2K 会在 SCF 没收敛时直接中止——这条现在有校验规则守着（`scf-max-too-small`）。

### SCF 路线细节：OT 与对角化（参数是**分路线**的，界面会实时跟着变）

`OT` 和对角化 `DIAGONALIZATION` 用的是**两套不同的子参数**，而且有些参数只在特定取值下才有意义。
界面上单独开了「SCF 路线细节（OT / 对角化）」一块（**默认收起**，见「可折叠的块」），
按**实际路线**显示/隐藏，并按子参数实时灰显；走哪条路线的总结留在折叠外面：

| 什么时候 | 界面行为 |
|---|---|
| 「自动」+ 普通绝缘体 | 显示 **OT** 框（自动判定结果写在块上方：`当前路线：OT（「自动」按任务和 SMEAR 现场判定）`） |
| 「自动」+ 能带/态密度，或开了 SMEAR | 显示**对角化**框，OT 框消失 |
| 走 OT 时 | 「空轨道数 ADDED_MOS」、`&MIXING` 的四个框一起灰掉——**OT 路线根本不读它们**（`ADDED_MOS` 在 CP2K 的 `qs_environment.F` 里就是 `.NOT. use_ot` 才生效；`&MIXING` 只写在对角化路线） |
| 走对角化 + 关掉 SMEAR | 混合参数（方法/α/缓冲/β）恢复可编辑 |
| `PRECONDITIONER` 改成 FULL_KINETIC/FULL_SINGLE/… | `ENERGY_GAP` 灰掉——只有 **FULL_ALL** 和 **FULL_SINGLE_INVERSE** 会读这个值 |
| `MINIMIZER` 改成 CG/SD | `SAFE_DIIS` 灰掉（没有 DIIS 步要过滤） |
| `LINESEARCH` 改成 NONE/ADAPT | `STEPSIZE` 灰掉（不做线搜索） |
| `LEVEL_SHIFT` | 只在对角化框里出现（源码里只在 `qs_scf_diagonalization.F` 用到） |

每个灰掉的框，它那一行的「?」里会写明**为什么现在用不上**。

### 布里渊区采样：k 点控件（&KPOINTS）

以前只有 band/PDOS 任务能看到 k 网格，**金属、小带隙半导体想给 SCF 一个网格在界面上根本没法设**
（命令行 `--kpoint-grid` 在别的任务上也是被静默丢掉的）。现在第 4 节里有一块独立的
**「布里渊区采样（&KPOINTS）」**，对每个 Quickstep 任务都可用；整块是一个**可勾选的组标题**：

> **⚠ k 点只能用对角化（`DIAGONALIZATION`）算。** CP2K 的 OT 遇到 `&KPOINTS` 会直接中止
> （源码 `qs_scf_initialization.F`：`OT diagonalization not possible with kpoint calculations`），
> 所以界面里这两项是**联动**的：
> • 勾上 k 点 → 「SCF 路线」自动从 OT 改成 DIAGONALIZATION，并写明原因；
> • 把路线改成 OT → k 点组自动关掉，并告诉你"要看布里渊区就换回对角化"；
> • 「自动」+ k 点 → 判定结果直接是对角化（路线说明那行会显示出来），**不会**写出 OT + `&KPOINTS`；
> • 真写出这个组合时校验器给的是 **error**（`ot-with-kpoints`），不是提示。
> 反过来，OT 路线下 k 点控件的「?」也会写着这条原因（灰显但可查）。

| 控件 | CP2K 关键字 | 说明 |
|---|---|---|
| 勾选组标题 | 整个 `&KPOINTS` 段 | **不勾就完全不写** → CP2K 只用 Γ 点（分子、大盒子、宽禁带绝缘体就该这样，输出与以前逐字节相同） |
| 按间距自动 + 目标间距 | `SCHEME MONKHORST-PACK n m l` | 每多少 Å 一个 k 点（默认 20 Å），下限 1、上限 8；大盒子自动退化成 `GAMMA` |
| 手工网格 | 同上 | 直接填 `4 4 4`（也可 `4,4,4`）；下面选 `MONKHORST-PACK`/`MACDONALD` 时会自动取消「按间距自动」，因为这个框就是那三个数字的来源 |
| SCHEME | `SCHEME` | 自动 / `GAMMA` / `MONKHORST-PACK` / `MACDONALD` / `GENERAL` / 自定义文本（`MACDONALD 4 4 4 0.5 0.5 0.5` 这种带偏移的用自定义） |
| 显式 k 点表格 | `KPOINT x y z w` | 每行一个点 + 权重，配 `SCHEME GENERAL`——和 CP2K 自己的回归输入写法一致 |
| UNITS | `UNITS` | `B_VECTOR`（默认）/ `CART_ANGSTROM` / `CART_BOHR` |
| GAMMA_CENTERED | `GAMMA_CENTERED` | 把网格平移到含 Γ 点 |
| FULL_GRID | `FULL_GRID` | 用整个网格、不做对称约化（Wannier/输运才需要；**只影响有没有那个 k 点，不影响精度**） |
| SYMMETRY | `SYMMETRY` | 工具默认 `T`（CP2K 默认 `F`）：约化后机时少得多，结果一致 |
| 波函数形式 | `WAVEFUNCTIONS` | `REAL`（工具默认）/ `COMPLEX`（自旋轨道耦合） |
| VERBOSE | `VERBOSE` | 在输出里列出 k 点与权重 |

下面的说明行会**算出将写出什么**：自动网格会写"按间距 20 Å → `MONKHORST-PACK 4 4 4`（共 64 个点，
对称约化后更少）"；显式 k 点还会提醒"SYMMETRY 还开着会做约化"。校验器另外会给
`kpoint-full-grid`、`kpoint-explicit-list`、`kpoint-general-without-points`（`GENERAL` 却一个点都没有）
这些提示。

两条"写出来的东西必须自洽"的规则（GUI 和 CLI 都适用）：

* **`MONKHORST-PACK` / `MACDONALD` 必须带三个数字**。手册原文是 "For MONKHORST-PACK the number of
  k points in all 3 dimensions has to be supplied along with the keyword"，所以只写了方案名时
  （命令行 `--kpoint-scheme MONKHORST-PACK` 很容易这样写）工具会按间距估算网格补上，
  而不是留一行 CP2K 读不了的 `SCHEME MONKHORST-PACK`；GUI 里网格框空着时说明行会直接告诉你
  估成了什么。
* **显式 k 点表格压过 SCHEME**。CP2K 只在 `SCHEME GENERAL` 下读 `KPOINT` 行，所以"表格里有行 +
  SCHEME 选了 MONKHORST-PACK"这种冲突会写成 `SCHEME GENERAL` + `KPOINT …`（并在说明行里点出来），
  不会写出两个互相矛盾的关键字。不想要那些点就把表格清空。

CLI（**暂时不再加新开关**）对应的写法是 `--kpoint-spacing/--kpoint-grid/--kpoint-point/…`，
自测里 GUI 与 CLI 逐字节对比过这几组组合。

### 另外补上的常用控件

| 控件 | CP2K 关键字 | 为什么常用 |
|---|---|---|
| 固定原子（第 4 节「体系」） | `&MOTION/&CONSTRAINT/&FIXED_ATOMS LIST` + `COMPONENTS_TO_FIX` | slab/表面：按住底下几层、只让上面弛豫（也避免整块漂移）。填 CP2K 自己的列表写法 `1..12`、`1 3 5..8`；单点/频率任务下这个框会自动变灰——那时它没有任何作用 |
| 轨道立方体（第 5 节） | `&DFT/&PRINT/&MO_CUBES` | 看 HOMO/LUMO 长什么样（VMD/VESTA/gOpenMol）。`NHOMO`/`NLUMO` 是"围绕间隙取几条"，`STRIDE` 控格点（默认 `2 2 2`） |
| 密度立方体（第 5 节） | `&DFT/&PRINT/&TOT_DENSITY_CUBE` | 总电子密度，做差、看键、看电荷转移 |
| EPS_DEFAULT（第 4 节） | `&QS EPS_DEFAULT` | **一个总精度旋钮**：没单独写的那些阈值都跟着它缩放（CP2K 默认 1e-10）。留空 = 不写 |
| 外推（第 4 节，EPS_DEFAULT 右边） | `&QS EXTRAPOLATION` | 默认「自动」：MD/优化用 `ASPC`，单点/频率用 `USE_GUESS`（没有"上一步"可外推）。SCF 莫名难收敛时可以设 `USE_GUESS` 排除干扰 |
| `QS 方法` 一行现在写全了 | `&QS METHOD` | **QS = Quickstep**，所以标签写成「Quickstep 方法（&QS）」，不再只写"QS 方法"让人猜 |
| MD 温度打印（第 2 节 MD 页下半） | `&MD TEMP_KIND`、`&MD/&PRINT/&TEMP_KIND`/`&ENERGY`、`&THERMOSTAT/&PRINT/&TEMPERATURE`、`&THERMOSTAT REGION` | 每个 kind 的温度、温度-时间曲线（`-1.ener` 的 Temp 列）、每个热浴区域的温度；**CP2K 里这是三件不同的事**，详见下面「MD 运行中的温度打印」 |
| 氘代（第 1 节） | `&KIND H MASS 2.0141017781` | 只改核质量、不改电子结构：X–H 伸缩周期 ~10 → ~14 fs，**AIMD 步长可以从 0.5 fs 提到 1.0 fs**（省一半机时）。力场路线用不上（质量在 prmtop/PSF 里），详见「氘代（H→D）」一节 |


新增的参数（默认值都等于工具原来硬写的值，所以默认输出一个字节都没变）：

| 参数 | CP2K 关键字 | 工具默认 | 可选值 | GUI | CLI |
|---|---|---|---|---|---|
| OT 最小化器 | `&OT MINIMIZER` | DIIS | SD / CG / DIIS / BROYDEN | 「MINIMIZER」 | `--ot-minimizer` |
| 预条件子 | `&OT PRECONDITIONER` | FULL_SINGLE_INVERSE | FULL_ALL / FULL_SINGLE_INVERSE / FULL_SINGLE / FULL_KINETIC / FULL_S_INVERSE / NONE | 「PRECONDITIONER」 | `--ot-preconditioner` |
| OT 算法 | `&OT ALGORITHM` | STRICT | STRICT / IRAC | 「ALGORITHM」 | `--ot-algorithm` |
| 线搜索 | `&OT LINESEARCH` | 2PNT | ADAPT / NONE / 2PNT / 3PNT / GOLD | 「LINESEARCH」 | `--ot-linesearch` |
| 初始步长 | `&OT STEPSIZE` | 不写 | 任意实数（负数=自动） | 「STEPSIZE」 | `--ot-stepsize` |
| 能隙估计 | `&OT ENERGY_GAP` | 不写 | 任意实数（Ha） | 「ENERGY_GAP」 | `--ot-energy-gap` |
| DIIS 兜底 | `&OT SAFE_DIIS` | 不写（CP2K: T） | T / F / 不写 | 「SAFE_DIIS」三态 | `--ot-safe-diis` / `--no-ot-safe-diis` |
| 占据旋转 | `&OT ROTATION` | 不写（CP2K: F） | T / F / 不写 | 「ROTATION」三态 | `--ot-rotation` / `--no-ot-rotation` |
| 外层优化器 | `&OUTER_SCF OPTIMIZER` | 不写（CP2K: NONE） | SD / DIIS / NONE / BISECT / BROYDEN / NEWTON / SECANT / NEWTON_LS | 「外层 OPTIMIZER」 | `--outer-optimizer` |
| 对角化算法 | `&DIAGONALIZATION ALGORITHM` | STANDARD | STANDARD / DAVIDSON / FILTER_MATRIX / LANCZOS / OT | 「ALGORITHM」（对角化框） | `--diag-algorithm` |
| 能级平移 | `&SCF LEVEL_SHIFT` | 不写 | 任意实数（Ha） | 「LEVEL_SHIFT」 | `--level-shift` |
| 混合方法 | `&MIXING METHOD` | BROYDEN_MIXING | NONE / DIRECT_P_MIXING / KERKER / PULAY / BROYDEN / MODIFIED_BROYDEN / MULTISECANT / NEW_PULAY | 「混合（方法/…）」第一个框 | `--mixing-method` |
| 展宽方法 | `&SMEAR METHOD` | FERMI_DIRAC | FERMI_DIRAC / METHFESSEL_PAXTON / MARZARI_VANDERBILT / ENERGY_WINDOW / GAUSSIAN / LIST | 「展宽方法」 | `--smear-method` |

下拉框里的取值不是手抄的：GUI 自测会拿**手册数据库**里该关键字的枚举值逐个核对（多一个就报错），
所以换 CP2K 版本重建数据库时不会留下 CP2K 不认的取值。

**一条被修掉的静默丢弃**：以前 `--scf OT --smearing` 会**悄悄不写 `&SMEAR`**（SMEAR 只写在对角化分支里）。
现在 SMEAR 一定写，并且会按手册补上 `ROTATION T`（`&OT ROTATION` 的说明就是"分数占据/非单重态 RKS 才需要"）。
校验规则也跟着分了两级：**OT + SMEAR 但没有 ROTATION → error**（OT 描述不了分数占据）；
**OT + SMEAR + ROTATION T → warning**（合法但不常见——CP2K 官方回归测试里一个这种输入都没有，
标准做法还是让 smearing 走对角化）。

### MD 运行中的温度打印（三种机制，别再混）

MD 里"温度"不是一个开关，CP2K 有三处会写温度，各写各的文件、各回答不同的问题。
界面上这三个控件都在 **第 2 节 MD 页的下半部分**（「运行中的温度 / 能量打印」），
旁边的说明行会实时念出"这次会写出哪些文件"：

| 控件 | CP2K 关键字 | 写出来的文件 | 什么时候用 |
|---|---|---|---|
| 每个原子种类的温度 | `&MD TEMP_KIND T` **+** `&MD/&PRINT/&TEMP_KIND` | `<项目名>-1.temp`：步号 / 时间 / 每个 kind 一个温度 | 混合物有没有**真热平衡**（全局 300 K 里水 350 K、溶质 250 K 也看不出来） |
| 能量文件（含 Temp[K] 列） | `&MD/&PRINT/&ENERGY` | `<项目名>-1.ener`：步号 / 时间 / 动能 / **Temp[K]** / 势能 / 守恒量 / 用时 | 画温度-时间曲线、看守恒量漂不漂（`plot 'cp2k-1.ener' u 2:4 w l`） |
| 热浴区域温度 | `&THERMOSTAT/&PRINT/&TEMPERATURE` | `<项目名>-1.PARTICLES.temp`：全局温度 + 每个区域的实际温度 | 热浴区域设成「每个分子」时，看**每个组分各自被控到多少** |
| 热浴区域（同页上面） | `&THERMOSTAT REGION` | —（决定上面那个文件里有几列） | `GLOBAL` 一个热浴 / `MOLECULE` 每种分子 kind 一个 / `MASSIVE` 每个自由度一个 |

**第一个坑：`&PRINT/&TEMP_KIND` 单独写没有任何用。** CP2K 只会在输出里警告：

```
The print_key MD%PRINT%TEMP_KIND has been activated but the calculation of the
temperature per kind has not been requested. Please switch on the keyword MD%TEMP_KIND.
```

所以工具把 `&MD TEMP_KIND T` 和打印键**当成一个开关一起写**，校验器也会对"只开了一半"的
手写输入报 error（`tempkind-print-without-keyword`）；反过来只开关键字不写打印键，会提示
"算完就扔了"。文件扩展名是 **`.temp`**（不是 `.tempkind`）：`md_energies.F` 传的是
`extension=".temp"`。

**第二个坑：`-1.ener` 不勾也会写。** `&MD/&PRINT/&ENERGY` 的默认打印级别是 LOW，而工具
默认 `PRINT_LEVEL MEDIUM`，所以 CP2K 本来就会写它，**每步一行**。勾上这个框的意义是
"显式打开并控制间隔/文件名"：间隔填 100 就是每 100 步一行（长跑想留稀疏的温度历史），
文件名按 `<项目名>-文件名-<运行号>.ener` 拼（以 `=` 开头则完全按写的来）。

**第三个坑：热浴区域是 GLOBAL 时，热浴温度文件就是全局温度**，和 `-1.ener` 的 `Temp[K]`
列是同一个数（校验器会给一句 info）。要看出组分差异必须先把「热浴区域」改成
**每个分子**——那才是每种 molecule kind 一个恒温器，每个区域一列目标值+实际值。
`DEFINED`/`THERMAL` 两种区域需要在输入里另写 `&DEFINE_REGION`/`&THERMAL_REGION`，
工具不做区域编辑器，所以下拉框里不提供。

生成的片段长这样（三个一起开、区域设成每分子）：

```
&MD
   ...
   TEMP_KIND T
   &THERMOSTAT
      TYPE CSVR
      REGION MOLECULE
      &CSVR
         TIMECON [fs] 50.0
      &END CSVR
      &PRINT
         &TEMPERATURE ON
            &EACH
               MD 20
            &END EACH
         &END TEMPERATURE
      &END PRINT
   &END THERMOSTAT
   &PRINT
      &ENERGY ON
         FILENAME run
         &EACH
            MD 50
         &END EACH
      &END ENERGY
      &TEMP_KIND ON
         &EACH
            MD 25
         &END EACH
      &END TEMP_KIND
   &END PRINT
&END MD
```

**默认一个字节都没变**：三个开关默认都关着，不勾就不写 `&MD/&PRINT` 这一段，生成的输入
和以前完全一样（自测逐字节比对过）。这三个是**界面/库独有**的字段（CLI 已冻结，没有新增
命令行开关）；"读参数"能把它们从已有输入里读回表单，`generate → 读参数 → 再生成`
是逐字节相同的。


### 可折叠的块：长而少用的设置默认收起来

参数面板是**一整条长表单**（结构 → 任务 → 方法 → 体系 → 输出 → 续算）。里面有几块
又长又只在特定时候才动，于是它们做成了**可折叠的分组**：标题那一行始终在，点旁边的
箭头（▸/▾）展开或收起，**收起状态不占高度**。

默认收起的七块：

| 块 | 什么时候才需要展开 |
|---|---|
| **元动力学**（在 MD 页上） | 要跑 metadynamics 时；勾上「启用元动力学」它会自己展开 |
| **运行中的温度 / 能量打印**（MD 页） | 要 `&MD/&PRINT` 的 `-1.ener` / 每 kind 温度 / 每热浴区域温度文件时 |
| **温度程序（多段：保持 / 升温 / 降温）**（MD 页） | 要多段升温/降温（一段一个输入 + `run_program.sh`）时。它是「升温 / 降温」的另一半：上面那一行只管**单段**、只影响「保存」的那一份输入；这一块一次生成**一整串**输入和驱动脚本。收起后 MD 页矮 363 px（1120 → 757，自测里量的） |
| **3.2 SCF 路线细节（OT / 对角化）** | 要调 OT 的 MINIMIZER/PRECONDITIONER 或对角化的算法时（**当前走哪条路线**那句总结留在折叠外面，一眼能看到） |
| **Molden 文件** | 要把轨道导出到 Molden/Avogadro/Jmol 时 |
| **立方体文件** | 要给 VMD/VESTA 画轨道或密度时 |
| **6. 续算（`&EXT_RESTART`）** | 要接着上一次算（接力）时 |

实现是一个 `CollapsibleBox`（`QGroupBox` + 一个箭头按钮 + 一个 `body` 子控件）：
`cp2kbuild/gui.py`。**展开/收起状态按块记在 QSettings 里**（键 `fold/<名字>`），
所以你把哪块打开过，下次打开界面它还是开着的；折叠**只影响显示**，不改变生成的输入
（自测里有一条专门断言这一点），「恢复默认」也不会把折叠状态一起重置。
经验力场页的三个子块（组分表、写进输入的力场条款、高级选项）用的是同一个类。


### 元动力学（metadynamics）：`&COLVAR` + `&METADYN`，或者交给 PLUMED

它就在 **「生成输入」→ MD 页**上，是一个**默认收起来的块**（点标题旁的箭头展开，
状态会记住）——这两段只有 `RUN_TYPE MD` 才会被 CP2K 读，放在别的任务页上只会变成一排
用不上的控件。勾上「启用元动力学」它也会自己展开，改完直接看右侧的完整预览。
这里一次配齐 CP2K 拆在**两个地方**的东西：CV 的**定义**在
`&FORCE_EVAL/&SUBSYS/&COLVAR`，偏置的**长法**在 `&MOTION/&FREE_ENERGY/&METADYN`：

| 页面上的东西 | 写出来的 | 命令行 |
|---|---|---|
| 「启用元动力学」 | `&FREE_ENERGY METHOD METADYN` + `&METADYN` | `--metadyn` |
| 路线选 PLUMED | `USE_PLUMED T` + `PLUMED_INPUT_FILE`；**同一套 CV/丘陵参数再写成 `plumed.dat`**（预览第二页 / 保存 / `--meta-plumed-draft`） | `--meta-plumed FILE`、`--meta-plumed-draft FILE` |
| 丘陵高度 `WW` | `WW`（Hartree） | `--meta-hill-height` |
| 两次丘陵之间的 MD 步数 | `NT_HILLS`（**是间隔，不是个数**） | `--meta-nt-hills` |
| 井温 + `DELTA_T` | `WELL_TEMPERED`、`TEMPERATURE`、`DELTA_T` | `--meta-wt`、`--meta-delta-t` |
| 最小位移 / 尾部截断 | `MIN_DISP`、`HILL_TAIL_CUTOFF` | `--meta-min-disp`、`--meta-tail-cutoff` |
| 扩展拉格朗日（+ Langevin） | `LAGRANGE`、`LANGEVIN`、各 `&METAVAR` 的 `MASS/GAMMA/LAMBDA` | `--meta-lagrange`、`--meta-langevin` |
| CV 表 | 一个 CV 一个 `&COLVAR`（`&DISTANCE`/`&ANGLE`/`&TORSION`/`&COORDINATION`/`&COMBINE_COLVAR`/`&DISTANCE_FROM_PATH`/`&RMSD`）；**PLUMED 路线下变成 `plumed.dat` 里的 CV** | `--cv "distance:1,2"` 等（组合/路径 CV 只在界面上加） |
| `&METAVAR` 表 | `COLVAR n` + `SCALE` + 墙 `&WALL/&QUADRATIC…` | `--metavar "1:SCALE=0.5:WALL=QUADRATIC,2.4,WALL_MINUS,0.5"` |
| 输出文件 + 间隔 | `&PRINT/&COLVAR`、`&PRINT/&HILLS` 的 `&EACH MD n` | `--meta-print-every`、`--no-meta-print` |

**只有 MD 任务能用**：偏置是靠 MD 的步数累积的，`geo_opt`/`energy`/`freq`/… 下
CP2K 根本不读这两段。所以**别的任务页上这一块不存在**（它跟着 MD 页一起不出现，
比"变灰"更干脆），生成器也不会把那两段写进去
（就算在库里直接给 `Spec.metadyn`，`build()` 也只会给一条
"metadynamics only applies to an MD run" 的警告）。

**几个 CP2K 自己的坑，工具替你挡住了**：

- **`DO_HILLS` 的默认是 `F`**（不撒丘陵）。不写它，`&METADYN` 就只剩墙和打印，
  自由能面永远是平的。工具默认写成 `T`，只有明确"只用接力带进来的丘陵"时才取消勾选；
  **取消勾选时如果还留着丘陵参数（`NT_HILLS`/`WW` 不是默认值），生成前会提示这两项不会
  被写进输入**（`NT_HILLS` 写成负数表示"不撒新丘陵"，那种组合不算矛盾）。
- **`WW` 的默认是 0.1 Hartree**，比合理值大两个数量级（300 K 时 kT ≈ 0.95 mHartree）。
  工具默认写 `1.0e-3`，帮助里给了 0.5–2 mHartree 的常用区间。
- **`NT_HILLS` 是"两次丘陵之间的最大 MD 步数"**，CP2K 自己的默认是 30；
  **负数**表示"不撒新丘陵，只用 `SPAWNED_HILLS_*` 里的"（配合接力）。
- **`&METAVAR GAMMA` 只在 `&METADYN LANGEVIN T` 下被读**，而 `MASS` 只在
  `LAGRANGE T` 下有意义——这两条都是生成前就提示的错误组合，不是跑起来才发现。
- **`&METAVAR SCALE`（别名 `WIDTH`）就是这一维高斯的宽度 σ**，不是"缩放因子"
  （手册描述里的 \"scale factor\" 容易误解，但公式 `WW·Σⱼ Πₖ exp(-½·((sₖ-sₖ(tⱼ))/SCALEₖ)²)`
  和源码 `metavar%delta_s` 都说明它是宽度）。**CP2K 的默认值是 0，而 0 被特判成
  「无限宽」**（`metadynamics.F:1023-1031`：那一维 `ddp = 0`、不加任何力）——
  所以「漏写 SCALE」等于整段 `&METADYN` 白跑。界面上的「默认丘陵宽度 σ」就是替
  每个没写 SCALE 的 `&METAVAR` 补上它（命令行 `--meta-sigma`），漏了会当场警告。
  单位跟 CV 走：距离类这一格填 **Å**（写文件时带 `[angstrom]`，CP2K 自己换算），
  角度类是 rad，配位数是纯数。
- **`&METADYN TEMPERATURE` 不是井温的参考温度**。它只属于扩展拉格朗日/Langevin 的
  CV 恒温器（手册原话 \"if a Lagrangian scheme is used …\"）；井温的参考温度是
  **`&MD TEMPERATURE`**（`md_run.F:400`：`wttemperature = simpar%temp_ext`），
  CP2K 还会拿它做除法（温度写 0 会得到无穷大）。所以工具**只在勾了
  `LAGRANGE` 时才写这个关键字**，温度框没勾 LAGRANGE 时是灰的。
- **CP2K 的 `WTGAMMA` 和 PLUMED 的 `BIASFACTOR` 是同一个约定**：两边都是
  `γ = 1+ΔT/T`（CP2K `md_run.F:414` 的检查写 `DELTA_T=(WTGAMMA-1)*TEMPERATURE`；
  PLUMED `src/bias/MetaD.cpp` 里丘陵高度 `h0·exp(-V/(k_B·T·(γ-1)))`，
  输出时再乘 `γ/(γ-1)`）。工具只写 `DELTA_T`，给 PLUMED 的草稿算
  `BIASFACTOR = 1 + ΔT/T`，并在文件里把那次的 ΔT 直接写进注释。
- **`MIN_DISP` 没有单位注解**，用的是 CV 的内部单位（长度类是 **bohr**，1 Å = 1.889 bohr），
  按 Å 填会偏大近一倍；`HILL_TAIL_CUTOFF` 是 SCALE 的倍数。
- **墙的 `POSITION`、`GAUSSIAN` 的 `SIGMA` 也是 `internal_cp2k`**（长度类 = bohr），
  界面按 Å 填、写文件时带 `[angstrom]`；`K`/`WW` 是 Hartree。
- **`QUARTIC` 墙在 2026.2 里能量与力自相矛盾**（`metadynamics_utils.F:928-929`：
  能量 `K·Δ·Δ⁴`、力 `4K·Δ³`），不建议使用；`GAUSSIAN` 墙的指数里其实有 ½
  （`WW·exp(-½·((CV-POS)/σ)²)`），和描述文字写的不是一字不差。
- **`&METAVAR COLVAR n` 里的 n 是 `&COLVAR` 在 `&SUBSYS` 里的顺序**，所以 CV 表
  **不排序**（行序就是序号），指到不存在的序号会当场警告。

- **墙的每个类型只吃自己的关键字**：`REFLECTIVE` 只有 `DIRECTION`，
  `QUADRATIC`/`QUARTIC` 是 `DIRECTION`+`K`，`GAUSSIAN` 是 `SIGMA`+`WW`。
  界面按类型只显示对应的输入框，写错类型（或该给的没给）会警告。
- **两个打印键按 MD 步计数**：`&EACH` 写成 `QS_SCF`（或者整段落在 `geo_opt` 里）
  时校验器给 `restart-print-level-mismatch` 警告——那个间隔几乎不会触发，
  真正写文件的是 `ADD_LAST`。

**接着跑怎么办**：要画自由能面、要续算，`-1.hills` 必须留着（默认写）。续算有两条路——

1. 直接 `cp2k -i <项目名>-1.restart`（丘陵历史就在那个文件里）；
2. 生成一份**新输入**接力（换温度、换丘陵高度都该这么干）：在「生成输入」页第 6 节
   填上续算文件，并在「取哪些量」里勾上 **metadynamics 的 hills**——那就是
   `&EXT_RESTART RESTART_METADYNAMICS T`，
   `input_cp2k_check.F` 会把 `SPAWNED_HILLS_POS/SCALE/HEIGHT/INVDT` 整段搬过来。

**PLUMED 路线**（`USE_PLUMED T`）：CP2K 在这条路上**只从 `&METADYN` 读一个文件名**
（`metadynamics_utils.F:129`：*"Check if using plumed. If so, only get the file name
and read nothing else"*），CV、丘陵、墙、输出全都以那个文件为准。所以元动力学这一块
**有两套控件**，由「路线」那个下拉框切换：

- **CP2K 原生** → `&COLVAR` + `&METAVAR` 那两张表（就是 CP2K 自己的语法）；
- **PLUMED 驱动** → **PLUMED 专用表**：`标签: 动作 参数`（动作清单和示例参数取自
  PLUMED 2.9 手册）、`METAD` 那一栏、墙表，外加一个「自由行」文本框。
  界面上同时给出这个文件：右边预览左上角多一个下拉框
  **`CP2K 输入` / `plumed.dat`**（实时重算），「保存」和「复制」作用于
  **当前那一页**，在 plumed.dat 页保存时默认文件名就是 `plumed.dat`；
  命令行是 `--meta-plumed-draft FILE`（`-` 打到 stdout），
  对应开关是 `--plumed-cv` / `--plumed-wall` / `--plumed-line` +
  `--plumed-pace` / `--plumed-height` / `--plumed-sigma` / `--plumed-biasfactor` /
  `--plumed-temp`，以及下面这些（每一项在界面上都有对应的控件）：

  | 控件 / 开关 | 写进文件的东西 | 为什么要它 |
  | --- | --- | --- |
  | 长度单位 / `--plumed-units nm\|A` | 选 **Å** 时文件第一行是 `UNITS LENGTH=A TIME=fs ENERGY=kj/mol` | CP2K 的一切都是 Å；PLUMED 默认 nm。**单位串大小写敏感**（`src/tools/Units.cpp:42-57` 逐字符比较），写成 `kJ/mol` 会在启动时直接报错——工具不会犯这个错，手写时最容易犯 |
  | 偏置哪些 CV / `--plumed-args` | `METAD ARG=` | 留空时取「**叶子**」标量：没被任何别的动作引用的那些。20 个中间量 + 一行 `COMBINE` 的表，`ARG=` 就正好是那个总量，而不是 21 维偏置 |
  | 偏置动作的标签 / `--plumed-label` | `bias: METAD …` | 别的行用 `标签.bias` 引用它；读回文件时保留原来的标签 |
  | RESTART / `--plumed-restart` | `METAD … RESTART=YES` | 从已有 `FILE=` 丘陵接着撒。**文件不存在时 PLUMED 2.8+ 直接报错**（更早只是警告）；CP2K 的 `RESTART_METADYNAMICS` 在这条路线上不起作用 |
  | ADAPTIVE + SIGMA_MIN/MAX / `--plumed-adaptive` `--plumed-sigma-min` `--plumed-sigma-max` | `ADAPTIVE=GEOM\|DIFF SIGMA_MIN=… SIGMA_MAX=…` | 让 PLUMED 自己调丘陵宽度（`GEOM` 覆盖一段距离、`DIFF` 覆盖一段时间/整数步）。这时 `SIGMA` 只能一个数，而 `SIGMA_MIN/MAX` 是**每个 CV 一个**（只填一个，工具替你铺开）；没开 `ADAPTIVE` 时写它们会让 PLUMED 报 `cannot understand the following words`；存网格（`GRID_*`）时手册要求必须给 `SIGMA_MIN`。事后 `sum_hills` 必须加 `--histo` 直方图修正 |
  | METAD 其他关键字 / `--plumed-extra KEY=VAL` | 原样追加在 `METAD` 行末 | 工具没建模的东西：`GRID_MIN/GRID_MAX/GRID_BIN`、`CALC_RCT`、`INTERVAL`、`WALKERS_N`… |
  | 接触型 CV 生成器 / `--plumed-contact SPEC` | 每个分子一行 `COORDINATION` (+ 一行 `COMBINE`) | 手写「每个分子跟其它所有分子的受体原子配对」最容易错位。按「分子分块」写：`blocks=19x1,8x20;center=2;center_atoms=1;acceptor=2;acceptor_atoms=0,2;r0=1.55;nn=12;mm=24;combine=cv_uu`（`;` 分隔字段，分块里的逗号保留），界面里还带实时预览和原子数/半个盒子的核对 |
  | 读回 plumed.dat… / `--plumed-import FILE` | 把文件读进表里 | 手写或别人给的文件可以在界面里看和改。**工具认识的归位、不认识的逐字保留**（参数照抄、`METAD` 行上的额外关键字进「其他关键字」、其他行进「自由行」），注释不保留；读回时点出问题（`ENERGY=kJ/mol` 这种大小写、缺 `ARG`/`SIGMA`、`RESTART=AUTO`…）。它是只读操作，不碰磁盘上的文件。**续行写法（`ACTION ...` 一行一个关键字再 `...` 收尾，手册 "Continuation lines"）也读得对**：先把每段折成一行再解析，所以 `SWITCH={RATIONAL …}` / `NLIST` / `NL_CUTOFF` 这些一个都不丢；`SWITCH={RATIONAL R_0=… NN=… MM=…}` 顶掉 `R_0`/`NN`/`MM`/`D_0` 这件事也照 PLUMED 的源码认（`Coordination.cpp`："When this keyword is present you no longer need the NN, MM, D_0 and R_0 keywords"），不会误报「没写 R_0」 |
  | 每个 CV 的 σ（CV 表第五列）/ CV 对话框的「该 CV 的 SIGMA」 | 一行 `SIGMA=…`，**每个 `ARG` 一个值** | PLUMED 的 `SIGMA` 是逐 ARG 的（`MetaD.cpp`：个数和 `ARG` 对不上就报 `number of arguments does not match number of SIGMA parameters`），所以二维偏置是 `SIGMA=0.15,0.5`。表里那一列写着每个 CV 现在用的是自己的 σ 还是「METAD 的默认 σ」；「默认 SIGMA」那一格只对没有自己 σ 的 CV 生效，**当所有 CV 都带 σ 时它会明说"这一格现在用不上"**，不再是改了不起作用的空操作。开了 `ADAPTIVE` 就相反：只能一个数，表里单独设的 σ 会被忽略并点出来 |
  | 「把选中的相加…」 | 一行 `COMBINE ARG=… COEFFICIENTS=… PERIODIC=NO` | 选中两行以上再点它。**`SUM ARG=a,b,c` 会被 PLUMED 拒绝**——它的 `SUM`/`MEAN` 只吃一个向量（`src/function/Sum.cpp`：`getNumberOfArguments()!=1` 直接报错） |

**为什么是两套而不是一套**：PLUMED 的 CV 语言和 CP2K 的不是一套规则（单位 nm/kJ/mol、
`R_0` 必填、指数叫 `MM`、多分量动作要写 `标签.mean`），把一张表来回翻译正是最容易出错
的地方。所以新表**一个字都不翻译**：你填的参数原样进文件，工具只做它能核对的检查
——标签合法/唯一、手册标为必填的关键字、`ARG=`/`DATA=`/`MATRIX=` 引用的标签是否存在、
多分量动作有没有选「分量」、原子序号是否越界、动作所在模块是否默认不编译
（`CONTACT_MATRIX` 这类在 **adjmat** 里，要 `./configure --enable-modules=adjmat`）。

**多分量动作**（multicolvar：`COORDINATIONNUMBER`、`DISTANCES`、`AROUND`、`CAVITY`、
`CONTACT_MATRIX`/`COLUMNSUMS`）算的是一组数，不能直接进 `METAD ARG=`：要在同一行用
`MEAN`/`MIN={BETA=500.}`/`MORE_THAN={RATIONAL R_0=4}` 之类的约简关键字压成标量，
然后引用成 `标签.mean`、`标签.min`……「分量」那一栏不只是用来拼 `ARG=` 的：
**工具会把这个关键字补到动作行上**（PLUMED 只有在这一行看到 `MEAN` 才会有
`标签.mean` 这个分量），已经写在参数里的不会重复；`MORE_THAN`/`LESS_THAN`/`BETWEEN`
这类必须带值的分量只能写在参数里，没写值会被点出来。没选分量也会警告。
典型写法（溶解/界面/空腔）：

```
cn: COORDINATIONNUMBER SPECIES=1-100 R_0=0.33 MEAN          # → cn.mean
d:  DISTANCES GROUPA=1-10 GROUPB=11-20 MIN={BETA=500.}      # → d.min
cav: CAVITY DATA=cn ATOMS=1,4,5,11 SIGMA=0.1 MEAN MORE_THAN={RATIONAL R_0=4}
m: METAD ARG=cn.mean,d.min SIGMA=0.3,0.05 HEIGHT=1.2 PACE=500 TEMP=330
```

**自由行**是出口：`OPES_METAD`、`PCAVARS`、`PIV`、多 walker……表里没有的东西都写在那里，
原样插进文件（可选插在 `METAD` 之前还是文件末尾）。表里没有、自由行也不想写的，
还可以完全手写：把 `PLUMED 输入文件` 指向你自己的文件（比如 `my.plumed.dat`），
工具只写文件名、一个字节都不碰——注意别用默认名 `plumed.dat`，否则点「保存」会盖掉它。

**只在专用表为空时才会走翻译**：那时草稿退回"把上面 CP2K 的 CV 表翻译成 PLUMED"的
老做法，界面会明说这一点。翻译规则与单位对照如下（**只在这条退回路径上适用**）：CP2K 用
原子单位跑，并通过 `setMDEnergyUnits=kjmol` / `setMDLengthUnits=angstrom*0.1` 把换算
因子告诉 PLUMED（`metadynamics.F:174-176`），所以 `plumed.dat` 里必须是
**PLUMED 的单位：nm / kJ/mol / ps**——CP2K 自己的测试文件就是这个规矩
（`tests/Fist/regtest-plumed2/water.plumedinp` 里 `HEIGHT=5.251` kJ/mol 正好是 2 mHartree）：

| 你在原生表里填的 | 翻译出来的草稿里 |
|---|---|
| `R0`、墙的 `POSITION`（Å） | **nm**（×0.1） |
| `WW` 丘陵高度（Hartree） | **`HEIGHT`（kJ/mol）**（×2625.5） |
| 墙的 `K`（Hartree/bohr²） | **`KAPPA`（kJ/mol/nm²）**（×937582.8；注意 K 是每 **bohr²**，用 Å 的因子会差 3.57 倍） |
| `DELTA_T` + **`&MD TEMPERATURE`** | **`BIASFACTOR` = (T+ΔT)/ΔT**（CP2K 的 `WTGAMMA` 是 `1+ΔT/T`，不是这个数） |
| `NT_HILLS` / 打印间隔 | `PACE` / `STRIDE`（都是 MD 步） |
| **`&METAVAR SCALE`（或界面的默认 σ）** | **`METAD SIGMA=`**——两者本来就是同一个量（丘陵宽度），只是单位要换算：距离类 Å→nm，角度 rad 不变，配位数是纯数不变。`SCALE 0`（CP2K 的"无限宽"）等价于把这个 CV 从 `ARG` 里去掉 |
| 配位数的 `ND` | PLUMED 里叫 **`MM`**（同一个指数，名字不同） |

**哪些 CV 是一一对应的、哪些要你自己补**（草稿里也用注释标出来了）：
`DISTANCE`/`ANGLE`/`TORSION`/`COORDINATION` 直接对应（配位数的 `KINDS_FROM/TO` 会按你
载入的结构展开成原子组，非周期体系自动加 `NOPBC`；**中心原子不止一个时草稿会补一层
`COMBINE COEFFICIENTS=1/N`**，因为 CP2K 的配位数是除以中心原子数的平均值
（`colvar_methods.F:4312`）而 PLUMED 是求和）；组合 CV 只把子 CV 写出来、**表达式留成注释**
（PLUMED 的 `COMBINE`/`MATHEVAL` 语法和 CP2K 的 `FUNCTION` 不一样，不猜）；
路径/RMSD CV 给一行 `PATHMSD`/`RMSD` 占位（参考构型要自己拼成一个文件）；
`QUADRATIC` 墙变 `UPPER_WALLS`/`LOWER_WALLS`，`REFLECTIVE`/`GAUSSIAN` 墙没有直译、
只给注释。`LAGRANGE`/`MASS`/`GAMMA`/`MIN_DISP`/`HILL_TAIL_CUTOFF` 在 PLUMED 里也没有
对应设置。文件末尾还有一段「要你自己确认的地方」清单。

**另外**：这条路要一个**编译时带 PLUMED2** 的 CP2K（`metadynamics.F:331`：
*"Requested to use plumed for metadynamics, but cp2k was not compiled with plumed
support."*），运行时还得找得到 PLUMED 库（否则 *"NO PLUMED IN YOUR
LD_LIBRARY_PATH?"*）。生成的输入文件头部就会写上这两句提醒。

**读参数也能读回来**：`&METADYN` 的丘陵参数、每个 CV 的定义（含嵌套的子 `&COLVAR` 和
`&FRAME` 参考构型）、每个 `&METAVAR` 的 SCALE/质量/墙都会填回这一块，
`生成 → 读参数 → 再生成` 逐字节相同（「读参数」列表里的元动力学那一项点一下会
**自动展开这一块并高亮**）。手写输入里 CP2K 的**别名**（`R_0`、`EXPON_NUMERATOR`、
`EXPON_DENOMINATOR`）按 `R0`/`NN`/`ND` 读；工具**不建模**的东西（`KINDS_TO_B`、
`&MULTIPLE_WALKERS`、`SLOW_GROWTH`、`NCOLVAR`…）会列在「读参数」的"没读"清单里，
不会悄悄消失。

界面截图（我自己看不到图，得你眼过一遍）：`packaging/shots/metadyn_folded.png`（收起时）、
`metadyn_open.png`（展开 + 井温元动力学 + 2 个 CV + 一张墙）、`metadyn_plumed.png`（PLUMED 路线）、
`metadyn_plumed_native.png` / `metadyn_plumed_native_draft.png`（PLUMED 专用表：动作、分量、
`METAD`/墙/自由行，以及右边 `plumed.dat` 预览那一页）、
`metadyn_plumed_advanced.png` / `metadyn_plumed_advanced_draft.png`（单位选 Å + `ADAPTIVE=GEOM`
+ `SIGMA_MIN/MAX` + `RESTART` + `GRID_*`/`CALC_RCT` + 自定义偏置标签，以及它生成的
`UNITS` 行和 `METAD` 行长什么样），以及另外几个折叠块 `fold_*.png`。

`examples/plumed_urea_cn.dat` 是一份真实用例（4-CPBA + 20 尿素、179 原子：
20 行分子间 C–N 配位数 + 一行 `COMBINE` 求和，`UNITS LENGTH=A`）——
它同时是**回归夹具**：`tools/selftest.py` 会把它读回来（21 个 CV、单位 Å、
`ARG=cv_uu`、0 问题），再原样写出去比对动作行；`tools/selftest.py` 里那条
「接触型 CV 生成器」的断言就是用它当答案的（同一组参数生成的 21 行与文件逐字节相同）。


### 氘代（H→D）：把 AIMD 的步长从 0.5 fs 提到 1.0 fs

第 1 节「基组与赝势」下面有一个勾选 **「氘代（H→D：氢用氘的质量 2.0141 u）」**，它只做一件事：
把 `&KIND H` 写成

```
&KIND H
   ELEMENT H
   BASIS_SET  DZVP-MOLOPT-SR-GTH
   POTENTIAL  GTH-PBE-q1
   MASS       2.0141017781      # deuterium (H -> D)
&END KIND
```

**为什么这不是化学替换**：Born–Oppenheimer 近似下势能面只依赖核电荷、不依赖核质量，所以
**元素仍是 H**，基组、赝势、电子结构一个字都不变，只有核的动力学变。换来的好处是**步长**：

| | 伸缩频率 | 周期 | 常用 TIMESTEP |
|---|---|---|---|
| O–H | ~3400 cm⁻¹ | ≈ 9.8 fs | **0.5 fs** |
| O–D | ~2400 cm⁻¹（ν ∝ 1/√m） | ≈ 13.9 fs | **1.0 fs** |

AIMD 的开销 ≈ 步数 × 每步 SCF，所以同样模拟时长**省一半机时**。校验器两头都会提醒：
勾了氘代却还写 0.5 fs → `deuterated-timestep`（提示可以提到 1.0 fs，别浪费）；
没氘代却写 ≥1.0 fs → `timestep-vs-xh-stretch`（X–H 周期只有 ~10 fs，偏粗）。

**要记住它是"另一个体系"**：经典核 MD 里玻尔兹曼分布与质量无关，所以**静态结构（RDF、配位数）
几乎不变**；变的是时间尺度上的量——扩散系数（D₂O 比 H₂O 慢约 20%）、振动谱、H 键寿命、
反应速率。想研究 H 本身（质子转移、量子核效应）不能拿它顶替，那需要路径积分分子动力学（`&PINT`）。
实验大多在氘代样品上做（中子衍射/NMR/IR），模拟 D 才能直接对比；算动力学同位素效应时 D 就是你要的体系。

**和"质量放大"不是一回事**：有人为了省机时把 H 设成 2.0/3.0、连 O 一起放大（CP2K 自己的
`tests/QS/regtest-gpw-3/dynamics.inp` 就是 `TIMESTEP 1.0` + `MASS 2.` + O `18.0855`），
那只是个数值加速器，结果不对应任何真实体系；工具只写氘的准确质量，`MASS` 也不会在"读参数"时
被误认成氘代。

**什么时候控件是灰的**（依据见 `packaging/couplings.md`）：体系里没有氢（没有可氘代的原子）、
单点能/能带/态密度（质量不进入能量和力）、以及 **METHOD FIST**（力场路线的质量来自拓扑文件
——AMBER 的 prmtop `%FLAG MASS`、CHARMM 的 PSF，`topology_amber.F:526`；`&KIND MASS` 只在
`CONNECTIVITY GENERATE/OFF/USER` 的路线里覆盖拓扑质量，见 `topology.F:427/444`）。经典 MD 想改
质量得改拓扑文件本身。

这是**界面/库独有**的选项（CLI 已冻结）：库里就是 `Spec(h_to_d=True)`；"读参数"能从
`&KIND H / MASS 2.0141017781` 读回来，`生成 → 读参数 → 再生成` 逐字节相同。

### 用 `-1.restart` 当结构：坐标文件那一格会被清空（第三个真实问题）

用户把跑完 FIST/GEO_OPT 得到的 `cp2k-1.restart` 当成结构载入，反馈两件事：
**读进来的 `&COORD` 没进预览**，而且**坐标文件控件没有被处理**。查下来是三个叠在一起的问题：

1. **`&COORD` 里的原子名被当成元素读了。** AMBER 跑出来的 restart，`&COORD` 的第一列是
   **拓扑里的原子名**（`CA3`、`OH2`、`HO3`、`NT1`…），不是元素符号：
   `CA3` 被读成钙、`HO3` 被读成钬，而 `OH2`/`HA3`/`NT1` 这些元素表里没有的名字**整个原子被丢掉**——
   179 个原子的体系读成 52 个、化学式 `Ca6BC21O21Ho3`。
   现在：**一个原子都不会因为名字不认识而丢**；名字按分层规则解析
   （① 本身就是元素符号，如 `Ca`/`Cl`/`C`；② 在 AMBER 输入里查原子类型表，`CA3`→C、`HO3`→H、
   `NA+`→Na、`CL`→Cl、`CA2+`→Ca；③ 再退到首字母），并且**把 CP2K 自己的原子名原样写回 `&COORD`**
   （FIST 不关心这些名字，参数从拓扑来）。
2. **restart 里的 `&COORD` 没被写进新输入。** 界面的自动填充把结构文件同时填进了「坐标文件」
   （`.restart` 在后缀表里被当成 AMBER 的 CRD），于是生成的是 `COORD_FILE_NAME cp2k-1.restart`
   ——让 CP2K 把一个**输入文件当 CRD 坐标读**。现在：`reads_as_cp2k_input()` 按**内容**区分
   （CP2K 输入 vs AMBER CRD 文件），这种文件绝不会被填进坐标文件；而力场路线在**没给坐标文件**时
   直接写 `&SUBSYS/&COORD`——这正是 CP2K 自己的做法（它写 restart 时会
   *"Remove COORD_FILE_NAME and COORD_FILE_FORMAT keywords from restart"* 并把坐标内联进去）。
3. **坐标文件控件**：按你的要求，**保持可用**（要不要用文件由你决定），但载入 restart/inp 的坐标后
   把它**清空**（残留的 restart 路径不是坐标文件）；格子里空着就写 `&COORD`，
   **填了就以文件为准**（那时不写 `&COORD`）。力场页的说明会讲清这一点。

界面上现在的行为：

| 载入的结构 | 「坐标文件」 | 生成的输入 |
|---|---|---|
| `-1.restart` / `.inp`（自带 `&COORD`） | 自动清空（控件仍可用） | 写 `&COORD`，原子名保持 CP2K 原样（`CA3`…） |
| pdb / xyz / inpcrd | 自动填成该文件 | 写 `COORD_FILE_NAME`，不写 `&COORD` |
| 先载入 restart，再手动填一个 inpcrd | 用你填的 | 以文件为准（不写 `&COORD`） |

自测覆盖：AMBER 原子名映射 18 例、真实 restart 读入 **179 个原子一个不丢**、
「接力输入只写 `&COORD`」、`.restart` 按内容区分、以及界面里「控件保持可用但被清空」+
「填上坐标文件后文件重新生效」。


### `&EWALD GMAX` 没有默认值（第二个真实崩溃）

同一位用户接着遇到的：

```
[ABORT] Value requested, but no value set getting value from keyword GMAX
        of section EWALD                    input/input_section_types.F:1132
                          ← fist_init / fist_create_force_env
```

`EWALD_TYPE EWALD` 和 `SPME` **必须**给 `GMAX`：CP2K 源码里就写着
*"we have no defaults for gmax, gmax is only needed for ewald and spme"*
（`ewald_environment_types.F`），然后**直接取值**——没有默认值可回退，于是 `fist_init` 阶段中止。
（`PME` 用的是 `NS_MAX`，那个有默认值 11；`NONE` 不需要。）

原来的工具只在用户**手填**了 GMAX 时才写它，界面上「GMAX」默认是「自动」——
也就是**不写**，于是周期体系的 SPME 一跑就崩。修法：

- 留「自动」时工具**替你估一个并写出来**，用的是 CP2K 自己那条 TB-Ewald 公式
  （`cutoff = 45·α`，`gmax_i = 2·floor(√(2·cutoff)·|a_i|/2π)+1`，α 默认 0.35 Å⁻¹）：
  24.4 Å 盒子 → `GMAX 59 59 59`，30 Å → `73 73 73`，60 Å → `147 147 147`；
- 填了 ALPHA 就按**你填的**那个估（α=0.5、24.4 Å → 71），所以两者始终自洽；
- 校验器新增 **`mm-ewald-gmax-required` error**：手写输入里 `EWALD`/`SPME` 缺 GMAX 会被点名
  （正是这次崩溃）；`GMAX > 128` 另给一条 info，提醒大盒子可以调小 ALPHA 一起降下来。

**一条被修掉的错误说明**：`GMAX` 的帮助原来写着"留空时 CP2K 按 `EWALD_ACCURACY` 自己估一个，
通常就够用"——那是错的（没有这个机制），现在改成讲清"必须给、工具替你估"。


### 单位标注：CP2K 是在**读输入**时解析 `[unit]` 的（第一个真实崩溃）

有位用户把生成的输入拿去跑，CP2K 在读输入阶段就中止了：

```
[ABORT] Unknown unit: a.u.                       common/cp_units.F:384
```

原因：工具给 `LEVEL_SHIFT` / `ENERGY_GAP` / `MAX_FORCE` / `RMS_FORCE` 写了
`[a.u.]` 这个标注。手册正文里确实把内部原子单位写作 "a.u."，**但 CP2K 的单位解析器不认识这个拼法**
——它只认 `AU_E`/`AU_F`/`AU_T`/`AU_L`… （而且光秃秃的 `AU` 会被故意拒绝：
*"au unit without specifying its kind not accepted"*）。手册是给人看的，`[unit]` 是给解析器的。

修法：这四个关键字**不再写单位标注**——不写时 CP2K 用关键字自己声明的单位，正是内部单位，
数值一个都不用改（比如 `MAX_FORCE 0.00045`）。CP2K 自己的回归输入也从不写 `[a.u.]`：
它们用的是 `au_l`、`au_e`、`hartree`、`angstrom`、`ev` 这类真正的单位名。

顺带做了三件防止复发的事：

1. `cp2kbuild/units.py`：把 `string_to_unit`（`common/cp_units.F`）的**完整单位表**搬进来（64 个），
   并按它的语法解析标注（支持 `nm^-2kjmol`、`angstrom^-2kcalmol` 这种幂次与连写）；
2. 校验器新增 **`unknown-unit` error**：任何 `[a.u.]`、`[au]` 或别的解析器不认识的标注都会被点名，
   提示里给出近似单位；**只对数值关键字生效**——CP2K 只对数值走 `get_r_val`，
   `ELECTRON_CONFIGURATION [Rn] 6d1 7s2` 里的方括号是普通文本，不能误判（4000 个 CP2K 回归输入零误报）；
3. 自测新增一组检查：单位表与 CP2K 源码逐条对照、18 种合法写法都收 / 6 种错写法都拒、
   我们自己 8 类生成的输入里没有任何非法标注、手写的 `[a.u.]` 与 `[au]` 都被抓成 error、
   `[Rn]` 不被误判。**原来那两条断言 `"ENERGY_GAP [a.u.] 0.002"` / `"LEVEL_SHIFT [a.u.] 0.1"` 也是错的**
   ——它们把这个 bug 固定住了，现在改成正确的写法。


### exe 的命名跟着 CP2K 版本走

打包出来的文件名里带着**内置手册对应的 CP2K 版本**：

```
dist\cp2kbuild-2026.2.exe        控制台版
dist\cp2kbuild-gui-2026.2.exe    窗口版（双击即用）
```

版本号是从打包时选中的手册数据库里读出来的（`branch: cp2k-2026_2-branch` → `2026.2`），
所以给另一个 CP2K 版本打一份包（`--database .../manual_db_2025_1.json`）会得到
`cp2kbuild-2025.1.exe`，两个版本可以并排放着不会搞混。

程序自己也会说清楚：`cp2kbuild --version` 打印 `cp2kbuild 0.1.0 (CP2K 2026.2 manual)`，
窗口标题栏和「关于」里同样写着内置手册的版本。

### 图形界面（PySide6）

```bat
python -m cp2kbuild gui                 :: 源码运行
cp2kbuild-gui-2026.2.exe                :: 打包后的 exe，双击即可
```

五个页签：

| 页签 | 干什么 |
|---|---|
| **生成输入** | 选结构（支持拖拽，也认已有的 `.inp`/`-1.restart`）→ 选任务 → 调参数，右侧**实时**显示最终 `.inp` 和校验结果；下面还有一张表实时告诉你每种元素选了哪个基组/赝势、价电子数是多少。载入 `.inp`/`.restart` 后可以点「读参数」把文件里的设置读回表单（见「读回一份已有输入的参数」）。**长而少用的块默认收起来**（见「可折叠的块」），元动力学就在 MD 页上 |
| **收敛测试** | 选模板来源（「生成输入」页此刻的参数 / 已有 `.inp`）→ 填扫描范围 → 生成官方教程那 7 个文件（`template.inp` + `cutoff_inputs/run/analyse.sh` + `rel_cutoff_*.sh`），拷到服务器上跑（见「收敛测试：只生成官方那套脚本」） |
| **温度程序** | 不在页签里：MD 页上一张表：一行一段（起始温度 / 目标温度 / 时长 ps）→ 「生成温度程序」写出每段一个 `.inp` + `run_program.sh`（串行接力）+ `program.ssv`（计划曲线），拷到服务器上 `sh run_program.sh`（见「多段温度程序」） |
| **校验输入** | 把若干 `.inp` 加进来批量校验，按严重程度排序，含错误类型、位置和建议 |
| **手册查询** | 离线搜关键字（**中英文都可以**）；也可以"列出某 section 的全部关键字"；带 98 条术语对照表 |
| **基组 / 赝势** | 扫描 `data/` 目录，给任意元素列表推荐配套的基组+赝势 |

生成和校验一共只要几十毫秒，所以参数一改预览就立刻重算——**屏幕上看到的就是会保存下来的文件**。

**右下角的「校验结果」里，两类问题都在同一张表上**：校验器找到的（手册 schema + 物理一致性）和
**生成器自己发现的**（`build()` 的 warnings，比如"开了元动力学却没有定义任何集体变量"、
"氘代用在没有氢的体系上"）。后者以前只挂在摘要那行的悬浮提示里，于是会出现
"1 项生成期问题"下面看着像空表——现在它们是表里一行 `build-warning` 警告，
摘要里的项数和行数永远对得上（`cp2kbuild new` 也是同样几条 `warning:` 打出来）。

**「保存」每一次都会弹对话框**（即使「输出文件」那一格已经有值），只是**起点继承上一次的路径**：上一次存到哪，这一次对话框就开在哪儿、文件名也预填上，所以"再存一遍"是回车，换名字/换目录是改一下。在对话框里点取消不会写任何文件，也不会把路径改掉。所有浏览对话框（结构、prmtop、坐标、输出…）共用同一个「上次打开的目录」，所以力场那几个文件通常连着选完。按钮叫**「保存」**而不是「保存为 .inp」：它存的是**右边预览里当前那一页**——停在「CP2K 输入」页就存 `.inp`，停在「plumed.dat」页就存 `plumed.dat`（默认文件名就是它）。

**「保存」/「复制」/「恢复默认」固定在左栏底部**（另一个真实反馈："这三个按钮能固定住吗"）。它们以前在滚动区**里面**、排在整张表单的最后，参数一多、窗口一矮就得先滚到底才能点；现在它们是滚动区外面的动作栏，表单怎么滚都不动。同一轮还修了"左栏有时候显示不完全"：面板需要 **710 px**，而默认栏位 700 px 减去竖滚动条只剩 **686 px**，于是横向滚动条一直在、每行右端被藏住。现在初始宽度是**算出来的**（`form_host.minimumSizeHint()` + 滚动条 + 余量，见 `GenerateTab._needed_form_width`），并把几处"一行塞太多控件"的地方改窄了：`&EACH`/`ADD_LAST` 那两行（九个控件要 650 px）改成**每行两段**、表格不再按列宽和向父面板要宽度（表格要 720 px 的列宽是自己横向滚动的事，`ExcelTable.minimumSizeHint` 覆写成 260）、按钮排成两行、几个长标签搬进「?」。自测里现在钉住了这件事：四种状态下都要求"视口 ≥ 内容最小宽度且没有横向滚动条"，并断言动作栏不在滚动区里、滚到顶或底位置不变。

**每个参数旁边都有一个「?」**（共 198 个），鼠标悬浮显示这个参数该选什么、典型值是多少、最常见的坑是什么。
提示**只挂在「?」上**：悬浮输入框、下拉框、按钮本身不会弹出任何东西——否则那一条长提示会正好盖住你要填的那几个框。例如 CUTOFF 会告诉你"这是最需要自己收敛的参数，SZV≈350、DZVP≈400、TZVP≈600"，色散会告诉你"分子晶体/吸附/层状材料必须加，不加会算成几乎没有结合"。

其中 150 个「?」**可以直接点击**打开对应的手册页（`&FORCE_EVAL/DFT/MGRID/CUTOFF` 之类），因为帮助里写的手册位置是拿内置手册数据库核对过的——写错会 404，所以自测里有一条专门检查这个。

不悬浮也能通读：`python packaging\dump_help.py` 会把全部帮助导出成 `packaging/help.md`。

#### 「收敛测试」页

一个用途：**写出一份模板输入 + 官方教程那套脚本**。命令行 `cp2kbuild scan` 与它共用同一份代码
（`cp2kbuild.cutoffkit`），生成的文件**逐字节相同**（自测里有等价性检查）：

- **1. 用哪份输入**——两条通道：选「用「生成输入」页此刻的参数」就直接拿你正在调的那套设置
  渲染模板（`<project>.inp`，并按教程写上省机时协议）；选「已有输入（`.inp`，原样保留）」
  就挑一个现成的 `.inp`，它会被原样复制成 `template.inp`，只换那两个值。
  **两条通道都会把 `RUN_TYPE` 改成 `ENERGY`**（网格扫描比的是总能），
  来源下面那行灰字会随你选的通道和当前任务变（在 MD 页上会额外提醒 MD 选项不会进模板）；
- **2. 扫描范围**——第 1 步的 CUTOFF 列表 + 固定的 REL_CUTOFF（默认 60 Ry）、
  第 2 步的 REL_CUTOFF 列表 + 固定的 CUTOFF（留空就先写 `CHANGE_ME`）；
- **3. 生成**——输出目录 + 一个按钮，右边显示写了哪些文件、工具自己做的判断，
  以及拷到服务器后要跑的三条命令。

### 命令行

不需要安装（无第三方依赖），在项目根目录：

```bat
cp2kbuild.cmd new --help
:: 或者
python -m cp2kbuild new --help
```

八个子命令：`new`（按任务生成输入）、`ramp`（写多段温度程序：保持/升温/降温的分段接力）、`scan`（写官方那套 CUTOFF/REL_CUTOFF 脚本，模板来自现有参数或已有输入）、`mmcheck`（经典 MD 的坐标/拓扑核对）、`validate`（校验已有输入）、`info`（看本机基组/赝势）、`keyword`（查手册关键字）、`db`（手册库信息）。

也可以 `pip install -e .` 之后直接用 `cp2kbuild`。

### 这些参数以前只能在命令行改，现在界面上也有

按 CP2K 官方回归语料（`TEST_DIRS` 的 2989 个输入）里"多少个文件用到它"排出来的高频项，逐条补进了界面与命令行：

| 参数 | GUI | CLI | 一句话建议 |
|---|---|---|---|
| `SCF/EPS_SCF` | ✅ | `--eps-scf` | 能量/力 1e-6；频率、应力、能量差很小的反应收紧到 1e-7 |
| `SCF/MAX_SCF` | ✅ | `--max-scf` | 30 通常够；一直靠加大它是治标，先看混合参数 |
| `QS/METHOD` | ✅ | `--qs-method` | 只在全电子或研究芯电子时才用 GAPW（贵 2–5 倍） |
| `MGRID/NGRIDS` | ✅ | `--ngrids` | 默认 4；改了要重做 CUTOFF/REL_CUTOFF 收敛 |
| `SCF/ADDED_MOS` | ✅ | `--added-mos` | 对角化（k 点/SMEAR/能带/PDOS）时必须够，否则费米面附近不准 |
| `SCF/&SMEAR` + 电子温度 | ✅ | `--smearing` `--electronic-temperature` | 金属/小带隙必用；开了会自动切对角化、且不写 `&MIXING` |
| `SCF/IGNORE_CONVERGENCE_FAILURE` | ✅ | `--ignore-convergence-failure` | 只比较**差值**时能用；要绝对能量、力、应力、MD 时不能用 |
| `SCF/&MIXING` 的 α/缓冲/β | ✅ | `--mixing-alpha` `--mixing-nbuffer` `--mixing-beta` | 振荡就调小 α；金属常要 β；只在纯对角化路线生效 |
| `&COORD/SCALED`（分数坐标） | ✅ | `--scaled-coords` | 文献/CIF 给的分数坐标直接贴，不用换算；需要晶胞 |
| `&THERMOSTAT/&NOSE\|&CSVR` TIMECON | ✅ | `--thermostat-timecon` | 100–1000 fs；太小会把真实振动动力学搅乱 |
| `&BAROSTAT` TIMECON | ✅ | `--barostat-timecon` | 取恒温器的 5–10 倍；太小把晶胞压塌 |
| `&TRAJECTORY/&EACH`（轨迹间隔） | ✅ | `--trajectory-stride` | 相邻帧的间隔要明显小于最快运动周期 |
| `&MD` 升温/降温（退火） | ✅ | `--temperature-ramp` `--temperature-annealing` `--annealing` `--annealing-cell` `--langevin-gamma` `--restart-temperature-annealing` | CP2K 只有"每步乘一个因子"；填起始/目标温度 + 步数，工具算因子并卡住系综（详见「AIMD 升温 / 降温」） |
| 元动力学（`&COLVAR` / `&METADYN`） | ✅ MD 页上的可折叠块（PLUMED 路线另有 `plumed.dat` 预览页） | `--metadyn` `--meta-plumed` `--meta-plumed-draft` `--meta-hill-height` `--meta-nt-hills` `--meta-wt` `--meta-delta-t` `--meta-min-disp` `--meta-tail-cutoff` `--meta-lagrange` `--meta-langevin` `--meta-print-every` `--no-meta-print` `--cv` `--metavar` | 只在 MD 任务里生效（别的任务页上它不出现）；`DO_HILLS` 默认 F、`WW` 默认 0.1 都改成合理值（详见「元动力学（metadynamics）」） |
| ↳ PLUMED 专用表（同一条路线） | ✅ 长度单位 / 偏置哪些 CV / 标签 / RESTART / ADAPTIVE+SIGMA_MIN/MAX / METAD 其他关键字 / 接触型 CV 生成器 / 把选中的相加 / 读回 `plumed.dat` | `--plumed-cv` `--plumed-wall` `--plumed-line` `--plumed-free-where` `--plumed-pace` `--plumed-height` `--plumed-sigma` `--plumed-biasfactor` `--plumed-temp` `--plumed-units` `--plumed-label` `--plumed-restart` `--plumed-adaptive` `--plumed-sigma-min` `--plumed-sigma-max` `--plumed-extra` `--plumed-contact` `--plumed-import` `--plumed-args` `--plumed-print`/`--no-plumed-print` | 单位串大小写、`SUM` 只吃一个向量、`ADAPTIVE` 的单 σ 与每-CV 的 `SIGMA_MIN/MAX`、`RESTART` 找不到 hills 会致命——这些都替你写对或当场点出来（详见上面那张表） |

两个刻意的设计：

- **默认输出一个字节都没变**。新参数只在被显式改动时才写进输入（`ALPHA 0.4`、`NBUFFER 8`、`TIMECON 100/500` 这些原来的默认值原样保留），所以老输入、示例夹具、以及"GUI 与 CLI 逐字节一致"那组测试全部不受影响。
- **会静默失效的组合直接告诉你**。`&MIXING` 只在纯对角化路线写：开了 SMEAR 时那两个框会变灰并说明原因，命令行则打印一条 `WARN`——而不是让你以为改了就有用。

### 指定 CP2K 数据目录

生成时会扫描基组/赝势文件，推荐固定下来：

```bat
set CP2K_DATA_DIR=E:\desktop\cp2k\cp2k-2026.2\data
```

或者每次 `--data-dir E:\desktop\cp2k\cp2k-2026.2\data`。找不到也可以跑，但会失去价电子匹配校验。

### 生成输入文件

```bat
:: 分子几何优化，气相，加 DFT-D3(BJ)
python -m cp2kbuild new --task geo_opt --structure examples\water.xyz ^
    --vacuum 12 --functional PBE --dispersion d3bj --out water_geo.inp

:: 周期性体系晶胞优化
python -m cp2kbuild new --task cell_opt --structure examples\silicon.xyz ^
    --cutoff 600 --out si_cell.inp

:: AIMD，NPT，400 K
python -m cp2kbuild new --task md --structure slab.xyz --ensemble NPT_I ^
    --temperature 400 --steps 20000 --timestep 0.5 --out slab_md.inp

:: 过渡态搜索（CI-NEB）
python -m cp2kbuild new --task neb --structure cell.xyz ^
    --replica r0.xyz --replica r1.xyz --replica r2.xyz ^
    --replica r3.xyz --replica r4.xyz --out neb.inp

:: 能带：Γ-X-W-K-Γ-L-U-W-L-K
python -m cp2kbuild new --task band --structure si.cif --lattice fcc ^
    --kpath "GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X" --npoints 120 --out si_band.inp

:: 态密度 + 投影
python -m cp2kbuild new --task pdos --structure si.cif ^
    --pdos-ldos "1 2 3" --pdos-nlumo 15 --out si_pdos.inp

:: 频率 / 红外
python -m cp2kbuild new --task freq --structure examples\water.xyz ^
    --vacuum 12 --intensities --out water_ir.inp
```

不想落盘就加 `--stdout`。生成后会自动跑一遍校验，有 error 会返回非零退出码。

`--structure` 认的格式：xyz / pdb / POSCAR(CONTCAR) / cif / 已有的 CP2K `.inp` /
**上一次运行写出的 `<项目名>-1.restart`**。扩展名认不出来时会看内容（文件里有 `&COORD`
就按 CP2K 输入读）——所以要**接力**时不必再准备一份结构文件，直接把
`--structure 1_geo-1.restart` 给它（要接速度就再加 `--restart 1_geo-1.restart
--restart-take vel`）：坐标和晶胞从 restart 里读成 `&COORD`/`&CELL`，这就是本次要算的体系。
GUI 上对应「从上次续算」右边的「读它的坐标」。

### 经典 MD（METHOD FIST，力场）

分子动力学在 CP2K 里是**两条完全不同的路**，由 `FORCE_EVAL METHOD` 决定：

| | AIMD（默认） | 经典 MD |
|---|---|---|
| METHOD | `QUICKSTEP` | `FIST` |
| 力的来源 | 每一步现算 DFT | 力场（prmtop / PSF / .pot） |
| 典型步长 | 0.5 fs | 1–2 fs（刚性水可到 2–4 fs） |
| 时间尺度 | ps | ns–μs |
| 是否需要力场文件 | 不需要 | **必须** |

命令行（AMBER 路线，prmtop + inpcrd）：

```bat
:: 纯经典 MD：NVT，1 fs，10 万步
python -m cp2kbuild new --task md --method fist --mm-route AMBER ^
    --mm-parm-file system.prmtop --mm-conn-file system.prmtop ^
    --mm-coord-file system.inpcrd --cell 40,40,40 ^
    --ensemble NVT --temperature 300 --steps 100000 --timestep 1.0 ^
    --out classical_md.inp

:: 混合体系：每种分子一个拓扑文件（CONNECTIVITY MOL_SET）
python -m cp2kbuild new --task md --method fist --mm-route AMBER ^
    --mm-parm-file mixture.prmtop --mm-coord-file mixture.inpcrd ^
    --mm-molecule AMBER:water.prmtop:500 --mm-molecule AMBER:ion.prmtop:20 ^
    --cell 40,40,40 --ensemble NPT_I --steps 500000 --timestep 2.0 ^
    --out mixture.inp

:: 纯对势（Ar 那种）：只要 ε/σ
python -m cp2kbuild new --task md --method fist --mm-route PAIR ^
    -s examples\argon.xyz --lj Ar:119.8:3.405 --lj-rcut 8.4 --mm-ewald NONE ^
    --cell 15.78,15.78,15.78 --ensemble NVE --temperature 85 --timestep 5.0 ^
    --out argon.inp
```

界面里在「任务与计算方法」选**经典力场 FIST**，DFT 那几页（泛函与精度 / SCF 路线细节 / 基组表）
会整块收起来，换成 `&MM` 页——因为 `METHOD FIST` 下 CP2K **不算电子**，
基组、赝势、CUTOFF、SCF、OT、k 点、Smearing 全部无意义，
而且 `METHOD FIST` 旁边出现 `&DFT` CP2K 会直接报错。

**力场路线**（`--mm-route`，对应 CP2K 的 `PARMTYPE` / `CONNECTIVITY` / `COORDINATE` 三个枚举）：

| 路线 | PARMTYPE | 拓扑 | 坐标 | 参数 |
|---|---|---|---|---|
| `AMBER`（推荐） | `AMBER` | prmtop | `CRD`（inpcrd/restart） | prmtop（**同一个文件**） |
| `CHARMM` | `CHM` | PSF | PDB/CRD | `.pot/.prm`（另一个文件） |
| `GROMOS96` / `GROMOS87` | `G96` / `G87` | `.top/.gtop` | `.g96` | 拓扑文件（**同一个**） |
| `IN_INPUT` | `OFF` | `GENERATE` | 输入里的 `&COORD` | 力场条款文件 |
| `PAIR` | `OFF` | `GENERATE` | 输入里的 `&COORD` | ε/σ 表 |

**AMBER/GROMOS 的参数文件和拓扑文件是同一个文件**，CP2K 却要求写两个关键字
（`PARM_FILE_NAME` 给参数、`CONN_FILE_NAME` 给连接性——它俩内部是两次独立的读取）。
所以只要填 `--mm-parm-file` 就够了：`--mm-conn-file` 不给时**自动继承**，
写出来的输入和 CP2K 自己的 AMBER 例子一致（官方每个 AMBER 测试都把两个关键字指向同一个 `.top`）。
界面里对应的是「拓扑文件」行右边的 **「同参数文件」** 勾选框（AMBER/GROMOS 默认勾上、
框会变灰；切到 CHARMM 自动取消——PSF 和 `.pot` 本来就是两个文件）。
头部注释会写明 `CONNECTIVITY AMBER  a.prmtop（与参数文件同一个文件）`，
GUI 与「只给 `--mm-parm-file` 的命令行」逐字节一致。

（这五个 `PARMTYPE` 值和 `CONNECTIVITY`/`COORD_FILE_FORMAT` 的取值都是照 `input_cp2k_mm.F`／
`input_cp2k_subsys.F` 里的 `enum_c_vals` 抄的，不是猜的。CP2K 自带的经典 MD 回归测试里
341 个带 `PARMTYPE` 的输入有 329 个是 CHARMM，但 prmtop/inpcrd 这套在 AMBER 生态里最顺手，
所以默认给 AMBER。）

几个**会静默算错**的地方，工具都替你填了或会点出来：

- **`EI_SCALE14` 的默认值是 0.0**（CP2K 的默认），也就是**把 1-4 静电全部去掉**——
  AMBER 和 CHARMM 都不是这么定义的。所以生成时按路线写死推荐值：
  AMBER `VDW_SCALE14 0.5 / EI_SCALE14 0.8333`（PARM94 及以后；PARM91 是 0.5/0.5），
  CHARMM `1.0 / 1.0`。用 `--mm-vdw-scale14/--mm-ei-scale14` 或界面两个框可以覆盖。
- **`STRESS_TENSOR` 默认是 `NONE`**，而 NPT/CELL_OPT 必须有应力才能压晶胞。
  FIST 下工具会自动写 `STRESS_TENSOR ANALYTICAL`。
- **`TEMPERATURE` 不只是 NVT/NPT 的目标温度**：手册写明它「用来给 init/pos restart 的速度定温」，
  所以 NVE 也要写（CP2K 官方的 `argon-nve.inp` 就写了）；不写就是 300 K 的初速度。
- **`EPSILON` 的单位是 K**（`unit_str="K_e"`），`SIGMA`/`RCUT` 是 Å。写成 kJ/mol 是经典错误。
- **文件路线不再写 `&COORD`**：两处都给坐标时 `COORD_FILE_NAME` 会静默覆盖输入里的坐标。
  同理 `--vacuum` 对文件路线会被忽略（坐标和盒子来自坐标文件，加真空层反而和拓扑对不上）。
- **`EWALD_TYPE SPME` 需要周期晶胞**（要在网格上插值），非周期体系自动退成 `NONE`。
- **`CONNECTIVITY MOL_SET` 的坐标仍然只有一个文件**：`NMOL` 的份数乘单体原子数要等于坐标文件里的原子数，
  顺序也要一致。整个体系已经有合并好的 prmtop 时就不要用这张表。

#### 拓扑/坐标/参数文件的路径怎么写（给集群跑）

输入多半在 Windows 上生成、拿到 Linux 上跑，所以**把 `E:/desktop/...` 原样写进去是没用的**——
集群上根本不存在那个路径。**默认只写文件名**：

| `--mm-path-style` | 行为 |
|---|---|
| `name`（默认） | 无论文件在哪都只写文件名——等于你保证它们和输入同目录 |
| `auto` | 文件真的**和输入同目录**才缩写成文件名，否则按你给的原样写 |
| `absolute` | 无论在哪都写完整路径 |

界面里是「3. 经典力场」页的 **「文件路径写法」**，默认「只写文件名（都在一个文件夹里）」。
`auto` 判断「同目录」用的目录：界面取「5. 输出」里那个保存路径所在目录，命令行取 `-o` 的目录。

于是「拓扑、坐标、输入都放一个文件夹」这个最常见的工作流会得到：

```
PARM_FILE_NAME mixture.prmtop
CONN_FILE_NAME mixture.prmtop
COORD_FILE_NAME mixture.pdb
```

整个文件夹拷到集群就能跑。**文件分散在几个目录时**（比如参数文件在 `../ff/`）请换成
`--mm-path-style auto`（同目录的缩写、别处的保留路径）或 `absolute`——
`name` 那个默认是「一切都在一个文件夹」的约定，它不会去猜。

注意这只是**写法**：文件是否存在仍然按你给的原路径检查，所以文件真的不在那儿时照样会提示
（提示里带的是完整路径，便于你去核实）。

#### 力场路线**不需要结构文件**

「每种分子一个拓扑」这条路线的原子*和*盒子都来自文件，所以**第 1 节的「结构」可以整个空着**：

```bat
:: 完全不给 --structure：原子来自 mixture.pdb，盒子用 --cell 给
python -m cp2kbuild new --task md --method fist --mm-route AMBER ^
    --mm-parm-file mix.prmtop --mm-coord-file mixture.pdb ^
    --cell 24.4,24.4,24.4 --ensemble NVT --steps 100000 --out classical_md.inp
```

界面里也一样：不选结构文件，直接选 FIST、把参数/拓扑/坐标填上，
**再在「4. 体系」的「晶胞覆盖」里填 `24.4,24.4,24.4`** 就会出预览。
少了晶胞时预览位置会明确写「还差一个晶胞」，而不是让你去找结构文件。

**为什么一定要给 `--cell`**：CP2K 会**忽略坐标文件里的盒子**
（`read_coordinate_crd` 里明写 "BOX information ... will be ignored.
Please provide the BOX information directly from the main CP2K input!"），
盒子只能从 `&CELL` 来。给了结构文件时工具会从它的 `CRYST1`/晶胞读；
不给结构文件时就用你填的那个数。缺晶胞时生成器**报错**（不是警告）：CP2K 只在
`CELL_FILE_NAME`/`ABC`/`A,B,C` 都没有时才去坐标文件里找盒子，而那条路只认
CIF/CP2K/PDB/EXTXYZ 四种格式（`cell_methods.F` 的 `case 4`），CRD/inpcrd
会直接中止；坐标格式是这四种之一时同一条降为警告（CP2K 会去读 CRYST1，
但文件里没有盒子时它中止在 "No meaningful cell information is read from parser!")。

顺带一个好处：这条路**不需要先有坐标文件就能出预览**——原子数由 `COORD_FILE_NAME`
决定，工具不读它，所以文件还不存在时也能先把输入框架和校验意见看清楚
（校验器会提示文件不存在）。头部注释会写明
「原子来自坐标文件（本工具不读它，只写 COORD_FILE_NAME）」，并指向
`cp2kbuild mmcheck` 去核对拓扑与坐标的对应关系。

校验器（`cp2kbuild validate`）会点名这些经典 MD 的错误：`fist-without-mm`、`fist-without-forcefield`、`mm-parmtype-without-file`、
`mm-connectivity-without-file`、`mm-mol-set-empty`、`mm-lj-without-parameters`、
`method-mismatch`（FIST + `&DFT`），以及 `mm-ei-scale14-default`、
`npt-without-stress-tensor`、`npt-without-periodicity`、`mm-ewald-needs-periodic`、
`mm-coord-format-suspect` 这些 warning。

#### 混合体系的坐标文件：packmol 的 PDB 能直接用

**不需要 inpcrd，也不需要把 packmol 的 PDB 转成别的格式。** CP2K 的 `COORD_FILE_FORMAT`
里 `PDB` 和 `CRD` 是**两个独立的读取器**（`topology.F` 里分别调 `read_coordinate_pdb` /
`read_coordinate_crd`），跟用哪种连接性无关。CP2K 自己的混合体系例子就是这么配的：

```
tests/Fist/regtest-1-2/multi_psf.inp   CONNECTIVITY MOL_SET + COORDINATE pdb
tests/Fist/regtest-14/lamol.inp        CONNECTIVITY MOL_SET + COORDINATE XYZ
```

所以在界面上把「坐标格式」改成 **PDB**、坐标文件填 packmol 的输出，就行了。

**为什么有人觉得 packmol 的 PDB「不能直接用」**，通常是下面三条之一：

1. **原子数对不上。** `read_topology_conn`（`topology.F:551`）里是
   `IF (SIZE(id_molname) /= topology%natoms) CALL cp_abort(...)`——**不相等就 abort**，
   报的是 "Number of atoms in connectivity control is larger than ..."。
   也就是 `Σ NMOL×单体原子数` 必须**完全等于**坐标文件里的原子数。
2. **顺序不对。** 坐标按 `&MOLECULE` 出现的顺序**按位置**分配，CP2K 不看残基名/分子名
   来决定哪个原子属于哪个分子。packmol 输出里分子的排列顺序必须和 `&MOLECULE` 一致。
3. **格式选错。** 选了 `CRD` 却给了 PDB（或反过来）。`CRD` 是 AMBER 的定宽卡片格式
   （inpcrd/restart），不是「通用坐标」。

另外两条容易踩的：

* **坐标文件里的盒子会被 CP2K 丢掉。** `read_coordinate_crd` 里明写着
  "BOX information found in CRD file. They will be ignored. Please provide the BOX
  information directly from the main CP2K input!"——盒子必须用 `&CELL`
  （界面「晶胞」/`--cell`）给出。
* **原子名不一样不是错。** CP2K 按位置套拓扑、元素靠它自己的 AMBER/CHARMM 映射表推；
  官方 `multi_psf.inp` 里 PDB 写 `1HH3`、PSF 写 `HC1`，照样跑。

**跑之前先查一遍**——`cp2kbuild mmcheck` 就是干这个的，而且它会**从坐标文件本身推测组成**：
PDB 的残基/链号把分子分段标出来了，**段大小就是分子大小、同尺寸段的个数就是 `NMOL`**，
所以 packmol 的输出除了拓扑文件名之外什么都告诉你了。

```bat
:: 混合体系：每个 &MOLECULE 一条 --mol
python -m cp2kbuild mmcheck ^
    --mol AMBER:mol1.prmtop:100 --mol AMBER:mol2.prmtop:50 --coord mixture.pdb

:: 单一拓扑的体系
python -m cp2kbuild mmcheck --conn-file mixture.prmtop --coord mixture.inpcrd
```

它把拓扑（prmtop 的 `%FLAG POINTERS`/`%FLAG ATOM_NAME`、PSF 的 `!NATOM`）和坐标
（PDB 原子名、inpcrd 的 `NATOM`、XYZ、G96）都读出来，然后给出：

```
坐标文件 : mixture.pdb （PDB，179 个原子）
按分段推测: 19 原子 ×1 + 8 原子 ×20（179 个原子）
          --mol AMBER:<19 原子的拓扑文件>:1
          --mol AMBER:<8 原子的拓扑文件>:20
&MOLECULE: solute1.psf  第 1 个原子起，1 × 22 = 22 个
&MOLECULE: solvent.psf  第 23 个原子起，145 × 12 = 1740 个
合计需要 : 1762 个原子
注意     : 原子名有 1762/1762 处不同（…）。**这本身不是错**：…
结论     : 对得上，CP2K 可以按这个顺序读
```

对不上时报出差额（`相差 +50`）并以退出码 1 结束。

**界面里这套是自动的**：载入一个多组分的 PDB 再选 FIST，
「**组分表：每个组分一个拓扑文件（&MOL_SET）**」会**自己填好行数和 `NMOL`**
（连接性列按当前路线预填，拓扑文件那列留空等你填），提示行说明「这是从残基/链号读出来的推测」。
**再拖一个别的 PDB，表会跟着重算**；已经填过的拓扑文件路径按行位置保留。
旁边两个按钮：

* **「按坐标文件识别组分」**——重新识别一遍（清空后重来、或想强制重算时用）；
* **「检查坐标与拓扑是否对得上」**——拿真拓扑核对，结果显示在表下方；改过输入后旧结果不再显示。

> **这张表不叫「混合体系」是有原因的**：`&MOL_SET` 的语义是**每个组分一个拓扑文件**，
> 和体系里有几种分子无关。**只有一种分子也常常需要它**——手里是 1 个水分子的 prmtop、
> 盒子里有 200 个水，就填一行 `NMOL 200`。反过来，`.xyz` 坐标没有残基信息识别不出组成；
> 或者同一尺寸的段**交错**（A,B,A,B——`&MOL_SET` 是按种类依次列出的，这种顺序它表达不了），
> 工具都会明说而不乱填。只有当你有一个**整个体系合并好的拓扑**时，才不用这张表——
> 直接在「拓扑文件」里填那一个文件。

识别不出来时的几种情况都会明确说出来：只有一种分子（提示用上面那格、或点按钮复制 N 份）、
XYZ/CRD 没有残基信息、段交错表达不了；改了坐标文件而表里还有你填过的路径时，**不会清空**，
而是提示那张表已经和当前坐标文件对不上了。

#### ⚠ 但是：`PARM_FILE_NAME` 仍然要填，而且它是**参数的唯一来源**

这一点很容易想当然，所以单独说清楚：**「每个组分一个 prmtop」省掉的是「连接性的来源」，
不是「参数的来源」。**

* `PARM_FILE_NAME` **不能空**。`PARMTYPE` 是 `CHM/AMBER/G96/G87` 时，CP2K 在
  `force_fields_input.F:138` 发现文件名为空就直接
  `CPABORT("Force Field Parameter's filename is empty! Please check your input file.")`。
* 每个 `&MOLECULE CONN_FILE_NAME` 是以 **`do_forcefield=.FALSE.`** 读的
  （`topology_amber.F:306`）：它只给**连接性**和**电荷**。
* 键长/键角/二面角的力常数、LJ 参数**全部来自 `PARM_FILE_NAME`**
  （`force_fields_ext.F:705`），并且是按**原子类型名**套到整个体系的原子上。
  某个键/角在参数表里没有对应项时，`force_fields_util.F` 直接
  `CPABORT("Missing critical ForceField parameters!")`（关键项 = Bonds、Bends、Charges；
  Torsions/Impropers/Opbends/1-4 属于非关键项）。

所以**两个不同组分各有一个 prmtop 时，把 `PARM_FILE_NAME` 指向其中一个是通常不够的**——
另一个组分的项不在那张表里。CP2K 自己的 AMBER `&MOL_SET` 例子
（`tests/Fist/regtest-14/lamol.inp`）之所以能把 `PARM_FILE_NAME` 和 `CONN_FILE_NAME`
指向同一个 prmtop，是因为它只有**一种**分子（`NMOL 4`）。

校验器现在会在这种情况下点名：

```
warning  mm-parm-file-covers-all-components
  PARMTYPE AMBER + CONNECTIVITY MOL_SET：2 个组分各有自己的拓扑，而 PARM_FILE_NAME 只有 a.prmtop
  （那就是其中一个组分自己的拓扑）。AMBER 的参数只从 PARM_FILE_NAME 来……
  否则 force_fields_util.F 会直接 abort：Missing critical ForceField parameters!
```

（`PARM_FILE_NAME` 是第三个、独立的文件时降为 **info**：它可能正是那个覆盖全体系的文件，
提醒你确认一下。官方 `lamol.inp` 那种「一个组分、同一个文件」的模式完全不提示——
全量扫过 CP2K 语料的 5412 个输入，0 命中。）

**实践上怎么办**：最省事的还是让 tleap 把两个分子 `combine` 起来 `saveAmberParm`
生成一个覆盖整个体系的 prmtop——有了它连 `&MOL_SET` 都不需要（直接 `CONNECTIVITY AMBER`
+ `PARM_FILE_NAME` = 它，坐标用合并的 inpcrd 或 PDB）。只有当两个组分的参数确实同源、
需要的项恰好都被同一个文件覆盖时，「每组分一个 prmtop」才成立，而且那也要先跑一次
`RUN_TYPE ENERGY` 确认 CP2K 没有报 missing parameters，再开 MD。

> 顺带说一句：CHARMM 不同。它的 `PARM_FILE_NAME` 是**参数库**（`.pot`），
> 被所有拓扑按类型名共享，所以「一个 `.pot` + 每个分子一个 PSF」天然成立——
> 官方 `multi_psf.inp` 就是这么做的。AMBER 的 prmtop 把参数和连接性装在一个文件里，
> 这个模式搬不过来。

拓扑文件那一列**留空也照样生成 `&MOLECULE`**：生成器会写一个带注释的空块，
校验器于是**逐行点名**（`&MOLECULE 1 没有 CONN_FILE_NAME`），而不是给一句笼统的
「CONNECTIVITY AMBER 要从文件里读拓扑」——报错直接指到该填的格子上。

如果确实想要 inpcrd（比如要和别的工作流对齐），它得由**整个体系**的拓扑生成：

```bash
cpptraj -p mixture.prmtop -y mixture.pdb -x mixture.inpcrd   # 需要合并好的 prmtop
tleap -f build.in    # 或干脆在 tleap 里一步搭好：combine + solvateBox + saveAmberParm
```

注意 `cpptraj` 要的是**合并好的 prmtop**；只有每种分子的 prmtop 时，转 inpcrd 并不能
解决顺序问题——该对齐的还是 `&MOLECULE` 的顺序。

例子都在 `examples/generated/`：`fist_amber_nvt.inp`（AMBER 模板）、
`fist_mixed_molset.inp`（混合体系）、`fist_water_in_input.inp` + `fist_water_ff.inc`
（自带的 `&FORCEFIELD` 条款，照 CP2K 官方 `H2O-32_NPT.inp` 的形式）、
`fist_argon_nve.inp`（对势，配 `examples/argon.xyz`）。
用 `python tools/make_examples.py` 可以一次全部重生成。

### 收敛测试：只生成官方那套脚本

这一块现在只做一件事：**写出一份模板输入 + CP2K 官方教程
[How to Converge the CUTOFF and REL_CUTOFF](https://manual.cp2k.org/trunk/methods/dft/cutoff.html)
用的那 7 个文件**，剩下的都在服务器上做：

```
cutoff_test/
  template.inp               ← 模板输入：&MGRID 里的 CUTOFF / REL_CUTOFF 是
                               占位符 LT_cutoff / LT_rel_cutoff，RUN_TYPE 一定是 ENERGY
  cutoff_inputs.sh           ← 一档一个目录（cutoff_50Ry/ …）
  cutoff_run.sh              ← 跑（mpirun 或串行，几个算例同时跑）
  cutoff_analyse.sh          ← 写 cutoff_data.ssv
  rel_cutoff_inputs.sh       ← 第 2 步的三个脚本
  rel_cutoff_run.sh
  rel_cutoff_analyse.sh
  （+ 你的 BASIS_SET / GTH_POTENTIALS / @INCLUDE 文件，脚本要 cp 它们）
```

模板输入有**两条通道**，界面和命令行是同一份代码：

| 通道 | 命令行 | 界面 | 模板长什么样 |
|---|---|---|---|
| 用工具现有参数 | `--structure system.xyz --vacuum 12 …`（和 `new` 一样的选项） | 来源选「用「生成输入」页此刻的参数」 | 现场生成一份 `<project>.inp`，并按官方教程写上省机时协议 |
| 已有输入，原样保留 | `--input system.inp` | 来源选「已有输入（.inp，原样保留）」 | 你的文件**逐字节**变成 `template.inp`，只动 `CUTOFF`/`REL_CUTOFF` 和 `RUN_TYPE` |

两条通道都**强制 `RUN_TYPE ENERGY`**：网格扫描比的是总能，一个 `MD`/`GEO_OPT` 输入会让每个
参数点跑成别的东西（而且慢几百倍）。字面写法（`RUN_TYPE MD`）和教程那种
`@SET RTYPE xx` + `RUN_TYPE ${RTYPE}` 都认，改完会在结果里写明改了哪一行；展开前改不动的
（`RUN_TYPE ${RTYPE}` 但找不到 `@SET`）只提醒、不瞎猜。

两条通道也都会**掐掉 `&EXT_RESTART`**：那一段是"接着上一次算"的，网格扫描每档必须是独立的
单点能。留着它，每档都会去读同一个 restart 文件，而集群上通常没有那个文件——CP2K 在检查输入时
就 `[ABORT] The specified OLD file <x-1_40000.restart> cannot be opened`
（`common/cp_files.F:406`），一个参数点都跑不出来；就算文件在，后面的档也会读到前面档刚写的
东西。做法是**整段注释**（每行前面加 CP2K 的注释符 `#`）而不是删掉：原文还在，想恢复随时改回来。
`&EXT_RESTART` 藏在 `@INCLUDE` 的文件里时工具改不了，会在结果里点名让你自己删。
（`scan` 还因此把 `--restart` / `--restart-take` / `--wfn-restart-file` 标成"这里不起作用"，
给了会打印一行说明。）

```bat
:: 通道 1：从工具现有参数（跟 new 一样的选项）
python -m cp2kbuild scan --structure water.xyz --vacuum 12 --outdir cutoff_test

:: 通道 2：从已有输入
python -m cp2kbuild scan --input system.inp --outdir cutoff_test

:: 两条通道共用的扫描设置
python -m cp2kbuild scan --input system.inp --outdir cutoff_test ^
    --cutoffs 50,100,150,200,250 --rel-cutoffs 10,20,30,40,50 ^
    --rel-cutoff 60 --fixed-cutoff 250

:: 在服务器上（把整个目录拷过去）
::   sh cutoff_inputs.sh && sh cutoff_run.sh && sh cutoff_analyse.sh
::   # 看 cutoff_data.ssv 定下 CUTOFF，填进三个 rel_cutoff_*.sh 的 cutoff=
::   sh rel_cutoff_inputs.sh && sh rel_cutoff_run.sh && sh rel_cutoff_analyse.sh
```

默认值就是官方脚本自己那两行：**CUTOFF `50 100 150 200 250 300 350 400 450 500`**（第 1 步）、
**REL_CUTOFF `10 20 30 40 50 60 70 80 90 100`**（第 2 步），第 1 步固定
**REL_CUTOFF = 60 Ry**。第 2 步那个固定的 CUTOFF 可以留空——脚本里会先写 `CHANGE_ME`，
**没改成真实值之前脚本会拒绝运行**，免得安静地生成一堆目录名不对的算例。
注意 `--cutoff`（`new` 的平面波截断）在这里**没有用**：模板里那个数会被占位符替换掉，
扫描值只由 `--cutoffs` / `--fixed-cutoff` 决定；给了它会打印一行说明。

**为什么是"只生成"**：这台机器上没有 CP2K 可执行文件，所以"读 `.out`、算差值、给建议值"这类
判断没法拿真实数据验证；而 `grep` + `gnuplot` 你自己在服务器上看，比工具猜的规则可靠。
`*_analyse.sh` 写出的 `.ssv` 就是教程那个格式（第一列参数、第二列总能、后面每档网格点数），
拖进 gnuplot/Excel 即可。旧的那套解析器（约 1000 行）留在 `legacy/` 里，需要时可以翻。

**工具比手抄教程多做的三件事**（都是为了不让测试白跑）：

- **保证 `sed` 有东西可替换，而且替换对了对象**：输入里已经有 `LT_cutoff` / `@SET CUTOFF LT_cutoff` / `${CUTOFF}`
  就沿用；只有字面值（`CUTOFF 400`）就把那一行换成占位符——**单位标注要留着**
  （工具自己写的输入是 `CUTOFF [Ry] 400.0`，占位符得插在 `[Ry]` **后面**，插错地方 `sed`
  会产出 `CUTOFF 100 400.0` 这种一行两个值的东西）；两者都没有就**明确报出来**——
  否则 `sed` 会一声不响地生成 N 份一模一样的输入，这种错最难发现。`template.inp` 除这两行外
  **逐字节保持原样**（CRLF 也保持）。
  第 1 步扫 CUTOFF、第 2 步扫 REL_CUTOFF，所以两行 `sed` 的**对象要对调**：
  第 1 步是 `s/LT_rel_cutoff/${rel_cutoff}/g` + `s/LT_cutoff/${ii}/g`，
  第 2 步必须是 `s/LT_cutoff/${cutoff}/g` + `s/LT_rel_cutoff/${ii}/g`
  （照抄第 1 步那两行，第 2 步就变成"把 CUTOFF 又扫一遍、REL_CUTOFF 固定不动"——
  曲线看起来还挺像一个收敛过程，实际整列都不是 REL_CUTOFF）。
- **替你找齐要复制的文件**：从输入里读出 `BASIS_SET_FILE_NAME` / `POTENTIAL_FILE_NAME`，
  加上 `@INCLUDE` 链上的文件，一起复制进目录并填进脚本的变量；本地找不到的会点名。
- **按官方教程写省机时协议**（`RUN_TYPE ENERGY` + `SCF_GUESS ATOMIC` + `MAX_SCF 1` +
  `&OUTER_SCF MAX_SCF 0` + `IGNORE_CONVERGENCE_FAILURE` + `PRINT_LEVEL MEDIUM`，每个点只跑 1 步
  SCF）：走「生成输入页参数」通道时工具是写输入的那一方，就直接写进去；走「已有输入」通道时
  工具只说一句并给出要加的内容，**不会**擅自改你的文件。（`PRINT_LEVEL MEDIUM` 不能少——
  没有它 CP2K 不打印 `MULTIGRID INFO`，分析脚本就数不出每个网格层落了几个高斯。）

### AIMD 升温 / 降温

CP2K 没有"目标温度"这种关键字，它只有**每步乘一个因子**的退火开关——工具替你把因子算出来，
并把两条路线的适用条件卡住（这两条是官方手册和源码里写着的硬约束，填错不是精度问题而是**跑不起来**）：

| 写法 | 做什么 | 什么系综能用 | 每步效果 |
|---|---|---|---|
| `&MD TEMPERATURE_ANNEALING f` | 移动 **Langevin 的恒温器目标温度** | **只能 `ENSEMBLE LANGEVIN`**（手册原文 "works only for the Langevin ensemble"；源码里这个乘法只写在 `SUBROUTINE langevin`，`integrator.F`） | T_target ← f·T_target |
| `&MD ANNEALING f` | 把**速度**乘 f（经典模拟退火/淬火，不是恒温器） | 只能是没有粒子恒温器的系综（NVE / NPE_*）。**NVT/NPT 会直接 `cp_abort`**：`simpar_methods.F` 说这两个控温手段互相打架 | T ← f²·T |
| `&MD ANNEALING_CELL f` | 同上，作用在**晶胞速度** | 只有 `NPE_I` / `NPE_F`（`integrator.F` 里只有这两个系综实现它） | T ← f²·T |
| `&EXT_RESTART/RESTART_TEMPERATURE_ANNEALING T` | 接力时用 restart 里存下的目标温度**接着斜坡走** | 配合 `TEMPERATURE_ANNEALING`（CP2K 自带的 `wat125_Tanneal_restart.inp` 就是 0.65 降温接 1.10 升温） | — |

界面在 MD 页有「升温 / 降温」一行：填**起始温度（就是「温度」那一栏）+ 目标温度 + 步数**，
工具算出因子、写进对应的关键字，并在下面写明机制、总时长、K/ps 和近似温度曲线；
系综不对时勾选框直接变灰并说清为什么。命令行是同一份换算器：

```bat
:: 300 K → 600 K，走 20000 步（LANGEVIN，自动写上 TEMPERATURE_ANNEALING 与 &LANGEVIN GAMMA）
python -m cp2kbuild new --task md --structure water.xyz --vacuum 12 ^
    --ensemble LANGEVIN --temperature-ramp 300:600 --steps 20000

:: NVE 里降温 300 → 100 K：自动改成速度缩放那种（ANNEALING）
python -m cp2kbuild new --task md --structure water.xyz --vacuum 12 ^
    --ensemble NVE --temperature-ramp 300:100 --steps 20000

:: 不想用换算器，直接给因子也行
python -m cp2kbuild new --task md ... --ensemble LANGEVIN --temperature-annealing 1.00003465796

:: 接力：接着上一次的目标温度继续升/降温
python -m cp2kbuild new --task md ... --ensemble LANGEVIN ^
    --restart run-1.restart --restart-temperature-annealing
```

因子是**几何（指数）**的：`T_n = T_start · f^n`，所以温度模式 `f = (T_end/T_start)^(1/步数)`、
速度模式 `f = (T_end/T_start)^(1/(2·步数))`（温度随速度平方）。倍数不大时（300↔600 K）和线性
几乎没差别；要**严格线性**就分段接力，每段一个固定 `TEMPERATURE`。

三条容易踩空、现在都有人管：

- **LANGEVIN 的 `GAMMA` 默认是 0**（= 没有摩擦也没有随机力，等于 NVE）——那样温度根本不受控，
  斜坡也不会生效。CP2K 自己的 Langevin 例子都显式写 `GAMMA 0.001~0.1`；工具在 LANGEVIN 下
  **一定**写一个（默认 0.01 fs⁻¹ = 10 ps⁻¹），给 0 会警告。
- **LANGEVIN 不写 `&THERMOSTAT`**：它的摩擦 + 随机力就是恒温器（`SUBROUTINE langevin` 根本不碰
  那套机制，CP2K 自己的 Langevin 输入也都不写）。工具以前会多写一个块，现在不写了，手动写了会提醒。
- **速率**：AIMD 常规 1–10 K/ps 量级；校验器在"每步移动超过 1 K"时提醒（那种斜坡恒温器跟不上，
  得到的是非平衡构型）。例：300→600 K 用 5 ps 是 60 K/ps，已经偏快；20000 步 × 0.5 fs = 10 ps
  就是 30 K/ps。

#### 多段温度程序（保持 → 升温 → 保持 → 降温）

"300 K 跑一段 → 线性升到 500 K → 保温 → 再升到 600 K → 保温 → 降温回 300 K"这种曲线，
**一次 CP2K 运行做不了**：`&MOTION/&MD` 那 47 个关键字里没有任何时间调度（`TEMPERATURE` 是标量，
退火只有"每步乘一个因子"），所以只能**分段接力**——一段一次运行，用 `&EXT_RESTART` 把坐标、速度、
步数计数器和恒温器状态接过去（CP2K 自己的 `wat125_Tanneal.inp` + `wat125_Tanneal_restart.inp`
就是这么写的）。工具把这件事自动化：

```bat
:: 命令行：一行简写或一个程序文件
python -m cp2kbuild ramp --structure water.xyz --vacuum 12 --ensemble LANGEVIN ^
    --segments "300:5,300-500:10,500:5,500-600:8,600:10,600-300:20" --outdir temp_program

:: 程序文件长得更像人话（# 注释，时长可带 ps/fs/ns/steps）
::   hold 300 5ps
::   ramp 300 500 10ps
::   hold 500 5ps
::   ramp 500 600 8ps
::   hold 600 10ps
::   ramp 600 300 20ps
python -m cp2kbuild ramp --structure water.xyz --vacuum 12 --ensemble LANGEVIN ^
    --program program.txt --outdir temp_program
```

界面在「生成输入」页的 MD 页签里有一张表：一行一段（**起始温度 | 目标温度 | 时长 ps**），
起始=目标就是"保持"，不等就是"升/降温"；点「加一段」时起始自动接上一段的终点（所以默认连续，
想跳变就手改，工具会提醒）。旁边实时显示段数、总时长、每段的机制与速率；有变化段而系综是
NVT/NPT 时直接提示换成 LANGEVIN（或 NVE 用速度缩放）。

写出来的目录（和命令行**逐字节一致**）：

```
temp_program/
  stage01_hold_300K.inp          ← 每段一个输入，PROJECT_NAME 就是段名
  stage02_heat_300to500K.inp
  stage03_hold_500K.inp
  stage04_heat_500to600K.inp
  stage05_hold_600K.inp
  stage06_cool_600to300K.inp
  run_program.sh                 ← 串行跑（后一段要读前一段的 restart，不能并行）
  program.ssv                    ← 计划曲线：段名/类型/起止温度/时长/步数/关键字/因子
  program.txt                    ← 你给的程序原文，便于复现
```

`run_program.sh` 里**每段的启动方式是可调的**（界面「温度程序」那一栏也有控件，命令行同名开关），
默认就按本项目的集群写法：

```bash
# 每段的启动（界面：每段 [mpirun] [cp2k.psmp] [-np 64] [OMP 2]）
export OMP_NUM_THREADS=2
mpirun -np 64 cp2k.psmp -i stage02_heat_300to500K.inp -o stage02_heat_300to500K.out
```

| 控件 / 开关 | 默认 | 说明 |
|---|---|---|
| mpirun | `mpirun` | 留空 = 不用 mpirun，直接单进程跑（本机调试）；`--mpirun ""` |
| CP2K 可执行 | `cp2k.psmp` | 也有 `cp2k.popt`/`cp2k.ssmp`/`cp2k.sopt` 可选；`--cp2k-bin` |
| `-np N` | 64 | 每段几个 MPI 进程；`--mpi-procs` |
| `OMP N` | 2 | 每个进程几个 OpenMP 线程（`export OMP_NUM_THREADS`）；`--omp-threads` |
| 跳过已跑完的段 | 开 | 断点续跑；`--no-resume` 关掉 |
| （脚本变量）`mpirun_extra` | 空 | 需要 `--bind-to none` 时自己加（OpenMPI 专有，MPICH/Intel MPI 不认） |

并行度≈ `-np × OMP`，别超过节点物理核数（64×2 通常就是一台双路机器；纯 MPI 就把 OMP 设 1）。
**注意这是"段内"的并行度：段与段之间永远是串行的链条**，所以调大它不会让程序变快，只会让每段更快。
这些机器相关的设置会被界面记住（和输出目录一样），下次打开不用重填。

输出目录那一行有 **「浏览…」**（走系统的目录选择框，Windows 上就是资源管理器）和 **「打开目录」**
（在文件管理器里打开当前目录，生成完一键就能看到写了什么；目录不存在时它是灰的）。

几个实现上的细节，都是踩过才知道的：

- **驱动脚本会把 restart 归一化**：CP2K 写出的 restart 名字可能带步数
  （`stage01-1_40000.restart`，就是以前让你 ABORT 的那个名字），所以每段跑完脚本把最新的
  `<段名>*.restart` 复制成 `<段名>-1.restart`，下一段读的固定是这个名字；哪一段没写出
  restart 脚本立刻停下并说明原因（不会闷头跑完一整套算出没意义的曲线）。
- **每段接的是坐标 + 速度 + 步数计数器**（+ 恒温器/压浴状态）：坐标必须接，因为每段输入里
  内联的 `&COORD` 都是**整个程序的初始结构**，不接就每段都跳回起点；计数器接上是为了让各段的
  `-1.ener` 能直接拼成一条连续的温度曲线。**波函数也接**：第 2 段起写
  `WFN_RESTART_FILE_NAME <上一段>-RESTART.wfn`（k 点用 `.kp`）——各段项目名不同，不写这一行
  CP2K 会找不到文件、退回原子初猜，每段都白花一次 SCF 起步；第一段没有可接的，明确写
  `SCF_GUESS ATOMIC`（不去打那条找不到文件的 warning）。
- **每段自己写起始温度**，所以**不需要** `RESTART_TEMPERATURE_ANNEALING`。
- 段内仍然只有 CP2K 那种**几何（指数）**斜坡：段短的时候和线性几乎没差别，要绝对线性就把段切细。
- **"一次任务"能做到什么**：CP2K 侧没法把多段塞进一个 `.inp`，但**提交一次**是可以的——
  `run_program.sh` 就是那个串行驱动器，集群上直接 `sbatch run_program.sh`（在脚本开头加
  `#SBATCH` 行）或 `qsub`，各段会在同一个申请里一段接一段跑完；也可以把它放进你自己的作业脚本里。

**段边界上什么连续、什么不连续**（这是接力的关键，别被"分段"两个字误导）：

| 量 | 段边界 | 怎么接 |
|---|---|---|
| 坐标、速度 | **连续** → 瞬时温度 T(t)、动能、总能量都不断 | `RESTART_POS` / `RESTART_VEL` |
| 步数计数器 | **连续** → 时间轴不跳，各段 `-1.ener` 拼起来就是一条曲线 | `RESTART_COUNTERS` |
| 波函数 / 电子密度 | 接上就是接着上一段的密度开始 SCF | `WFN_RESTART_FILE_NAME <上一段>-RESTART.wfn` |
| 恒温器内部状态（Nosé 链、CSVR 历史） | 不接会"冷启动"，控温量有小瞬态 | `RESTART_THERMOSTAT`（LANGEVIN 没有状态，摩擦+噪声无记忆，不需要） |
| 压浴状态（晶胞速度、barostat 质量与其恒温器） | 同上 | `RESTART_BAROSTAT` / `RESTART_BAROSTAT_THERMOSTAT` / `RESTART_CELL` |
| 温度**目标值** | 刻意每段自己写起点（这就是"程序"）；相邻段起点=上一段终点，所以曲线连续 | 每段的 `TEMPERATURE` |
| SCF 外推历史（ASPC） | **不连续**：那是个内存里的环形缓冲，`&EXT_RESTART` 里没有对应开关 | 无——每段第一步少一点加速、多几次 SCF 迭代，收敛后的结果不受影响 |
| 随机数发生器 | 不接就是新种子 | `RESTART_RANDOMG T`（只影响整条轨迹的**严格复现**，不影响物理） |
| MD 平均值（AVERAGES） | 不接就每段各自平均 | `RESTART_AVERAGES`（整条程序要一个平均时才加；斜坡期的平均通常没意义） |

所以"每段重启"的代价只是**读 restart + 波函数、重建网格、头几步多几次 SCF 迭代**——
相对几千个 MD 步可以忽略；物理轨迹是连续的，不会在段边界上出现温度或能量的突跳
（除非你把某一段的起始温度写成上一段终点之外的值，那种"跳变"工具会提醒）。

跑完怎么看温度：每段都有 `<段名>-1.ener`（`Temp[K]` 列，`PRINT_LEVEL` 默认就会写），
按 `program.ssv` 的顺序拼起来，和计划值对照即可。想让曲线更细就把上面「运行中的温度 / 能量打印」
里的间隔调小。

**和单段「升温 / 降温」的分工**（两个控件在同一页，容易混）：

| | 单段（上面的勾选框 / `new --temperature-ramp`） | 多段（温度程序表 / `cp2kbuild ramp`） |
|---|---|---|
| 一次运行做什么 | 一条几何斜坡 | 一段（保持 或 一条斜坡） |
| 能保持 / 换斜率 / 掉头 | 不能 | 能（一段一次运行，接力） |
| 产出 | 一份输入（保存/预览的那份） | 每段一份输入 + `run_program.sh` |

两者**同时填不会生成坏输入**，但**程序说了算**：每一段的目标温度和因子都由表格/程序文本自己算，
「保持」段一个退火关键字都不写，所以你上面那个单段因子**不会**进程序（生成结果里会写明这一点），
反过来程序也不会改动你单段那份输入。唯一的例外是**晶胞速度退火** `ANNEALING_CELL`：表里没有它，
它是**程序级**的，设了每一段都会带上（含保持段），而且只有 `NPE_I`/`NPE_F` 会真的用它。
命令行 `cp2kbuild ramp` 里对应的开关（`--temperature-ramp` / `--temperature-annealing` /
`--annealing` / `--steps` / `--temperature` / `--restart-temperature-annealing`）
在 `--help` 里直接标成 "not used by this command"，给了也会打印一行说明。

### 校验已有输入

```bat
python -m cp2kbuild validate legacy.inp
python -m cp2kbuild validate *.inp --strict      :: 警告也算失败
python -m cp2kbuild validate job.inp --json      :: 机器可读
```

会做两层检查：

**第一层：对照 CP2K 手册的模式校验** — section 是否存在、是否放在正确的父 section 下、关键字是否属于该 section（含别名识别）、枚举值是否合法、标量类型是否是整数/实数/逻辑值、是否重复定义、不可重复的 section 是否被写了多次、section 参数（如 `&XC_FUNCTIONAL PBE`）是否合法。

**第二层：物理/一致性检查**，覆盖实际最容易踩的坑：

| 检查 | 说明 |
|---|---|
| 基组–赝势价电子匹配 | `DZVP-MOLOPT-SR-GTH`(q16) 配 `GTH-PBE-q8` → 直接报错 |
| 元素覆盖 | 基组/赝势对某元素根本不存在 → 报错并列出可用项 |
| `&KIND` 覆盖 | `&COORD` 里出现却没定义 `&KIND` 的元素 |
| 电荷–自旋奇偶性 | 按**赝势价电子数**算电子数（不是原子序数！），`MULTIPLICITY` 奇偶性不符 → 报错 |
| 多重度与 UKS | `MULTIPLICITY > 1` 未开 UKS → 提示（新版 CP2K 会自动自旋极化） |
| 多重网格自洽性 | `REL_CUTOFF > CUTOFF` 时最细网格无法承载单位高斯 → 警告 |
| cutoff 偏小 | 相对基组给出提示 |
| SMEAR 与 OT 冲突 | `SMEAR` 必须走 `DIAGONALIZATION`；`SMEAR` 需要 `ADDED_MOS > 0`（`&OT OFF` 之类被关掉的块不计入；值由预处理器决定的场合跳过判断） |
| k 点需要周期性 | 非周期晶胞配 `&KPOINTS` |
| `RUN_TYPE` 与 `&MOTION` | `RUN_TYPE GEO_OPT` 却没写 `&MOTION &GEO_OPT`（会用默认优化器，给提示）；`RUN_TYPE ENERGY` 配了 `&MOTION &MD`（会被静默忽略 → 警告） |
| MD 系综 | `NVT`/`NPT_I`/`NPT_F`/`NPE_*` 缺 `&THERMOSTAT`；改变晶胞的系综缺 `&BAROSTAT`；`TIMESTEP` 过大；`GLE` 缺 S/A 矩阵 |
| NEB | `NUMBER_OF_REPLICA` 与 `&REPLICA` 块数不符；image 少于 3 个；**整份输入没有 `&CELL`**（每个镜像会被塞进默认的 1 Å 立方体） |
| 色散泛函一致性 | `REFERENCE_FUNCTIONAL` 与 `&XC_FUNCTIONAL` 不一致 |
| `&COORD` 与 `COORD_FILE_NAME` 同时存在 | 提示冲突 |
| 空 `&CELL` | 只有 `MULTIPLE_UNIT_CELL` 没有 `ABC` → 警告（CP2K 回退到 1 Å 立方体） |

校验器对"不确定"的情况刻意保持沉默而不是误报，具体包括：值里含 `$`/`@`（预处理器决定）、`&KIND DEFAULT` 兜底、DFTB/xTB/半经验方法不需要 `&KIND`、`BASIS_SET_FILE_NAME` 指向手册数据目录之外的文件（如测试自带的 `BASIS_SET`）时跳过基组存在性判断、`&LENNARD` 这类手册换了名字的 section、被 `OFF` 关掉的 section，以及 `1..12` 区间、`1/0.129629` 这类 CP2K 允许的表达式写法。

### 查看本机有哪些基组/赝势

```bat
python -m cp2kbuild info
python -m cp2kbuild info --elements H,C,O,Fe,Pt --target solid
python -m cp2kbuild info --elements La,Ac --target molecule   :: 难覆盖的元素
```

### 离线查手册（中文对照）

```bat
python -m cp2kbuild keyword cutoff          :: 英文
python -m cp2kbuild keyword 截断能           :: 中文也能搜
python -m cp2kbuild keyword 赝势
python -m cp2kbuild keyword --cn smearing   :: 只看有中文说明的
python -m cp2kbuild keyword --glossary      :: 全部术语对照
python -m cp2kbuild keyword --section FORCE_EVAL/DFT/SCF --all
```

**手册正文是英文的，中文部分分三层，每层都标明来源**：

| 层 | 内容 | 来源 |
|---|---|---|
| **中文说明** | 109 个高频关键字的中文解释（该选什么值、典型值、常见坑） | **cp2kbuild 自己写的，不是手册译文**（界面里会明确标注） |
| **术语对照** | 98 条英文术语→中文，附在英文原文旁边，帮你看懂原文 | 我自己整理的，并且用脚本核对过每个术语确实出现在手册正文里 |
| **中文搜索** | 输入"截断能""系综""真空层""几何优化"都能找到对应关键字 | 搜索索引建在中文名和别称上 |

**为什么不做全篇翻译**：手册有两万多个关键字。整篇机器翻译会把技术描述译错，而一条译错的物理描述比英文原文危险得多；我也没有可用的翻译接口。所以策略是——常用的手写中文解释，其余保留英文原文并给出术语提示。

准确性由脚本保证：`python tools\check_cn_glossary.py` 会核对每一条中文说明指向的 section/关键字**在手册数据库里真实存在**（写错的话那条说明永远显示不出来），并统计术语覆盖率。

---

## 2. 生成器做了什么约定（以及为什么）

生成的文件里每一处"约定"都写了注释。主要几条：

- **基组选择**：周期性体系优先 `DZVP-MOLOPT-SR-GTH`（短程、网格便宜），分子优先 `DZVP-MOLOPT-GTH`（弥散、气相更准）；MOLOPT 覆盖不到的元素自动退到 `BASIS_MOLOPT_UCL` / `UZH` / `pob` 等家族，并逐元素选择。可用 `--basis-target {solid,molecule,cheap,accurate}` 改。
- **赝势选择**：先定基组，再按**价电子数**在 `GTH-*` 家族里找同名价态的势（PBE→`GTH-PBE`，BLYP→`GTH-BLYP`，PADE→`GTH-PADE`…），保证不会出现 q 不匹配。
- **CUTOFF**：按基组家族给初值（SZV≈350、DZVP≈400、TZVP≈600、TZV2P≈700 Ry），并提示用 `scan` 收敛。`REL_CUTOFF 60 Ry`。
- **SCF 路线**：默认 `OT`（绝缘体/大体系快且稳，配 `&OUTER_SCF`）；带 k 点、开 smearing、算能带/态密度时自动切 `DIAGONALIZATION`（配 `&MIXING`）。可用 `--scf` 强制。被显式关掉的块（`&OT OFF`）不会被当成启用。
- **孤立分子**：`--vacuum 12` 会在分子外包一层真空盒，并且**自动加 `&POISSON PERIODIC NONE` + `POISSON_SOLVER MT`**——只设 `&CELL PERIODIC NONE` 而不改 Poisson 求解器，CP2K 会直接报错。原子数 > 200 时自动改用 `WAVELET`。
- **MD**：`--ensemble {NVE,NVT,NPT_I,NPT_F,NPE_I,NPE_F,LANGEVIN,MSST,ISOKIN}`，`--thermostat {NOSE,CSVR,GLE,AD_LANGEVIN,NONE}`；NOSE/CSVR 自动带 `TIMECON`，GLE 会提示需要自己提供 S/A 矩阵；改变晶胞的系综自动加 `&BAROSTAT`。`--seed` 写进 `&GLOBAL SEED` 保证初速度可复现。
- **色散**：`--dispersion d3bj` 生成完整的 `&VDW_POTENTIAL / &PAIR_POTENTIAL`，并自动填入与泛函一致的 `REFERENCE_FUNCTIONAL`。
- **能带**：`&DFT &PRINT &BAND_STRUCTURE` + 按晶格类型自动给出的高对称路径（`cub/fcc/bcc/hex/tet/orc`），坐标用 `UNITS B_VECTOR`；同时给出 SCF 用的 Monkhorst-Pack 网格（默认按 ~20 Å 一个 k 点估算，可用 `--kpoint-grid 1 1 1` 覆盖）。
- **NEB**：`RUN_TYPE BAND` + `&MOTION &BAND`（`CI-NEB` / `IT-NEB` / `B-NEB` / `D-NEB` / `SM` / `EB`）+ `&REPLICA` 列表 + 收敛判据；没给结构时会从第一个镜像文件推断晶胞和元素，仍无晶胞则明确警告。
- **`&PRINT`**：默认只开必要的输出（力、偶极、轨迹），避免几百 MB 的 cube 文件。周期性体系不出偶极。
- **`&PRINT` 的段参数是"打印等级"枚举，不是逻辑值**：写 `&FORCES ON`（不是 `&FORCES T`）。
  合法取值只有 `ON / OFF / SILENT / LOW / MEDIUM / HIGH / DEBUG`（`cp_print_key_section_create`
  里是一个**严格**枚举），别的写法会让 CP2K 直接 `invalid value for enumeration` 退出。
  注意这跟 `&OT T`、`&MULTIPOLES T` 那种**逻辑型**段参数不是一回事——两者在输入文件里长得一样，
  类型却不同，校验器现在按手册里的 `SECTION_PARAMETERS` 类型分别判断（`boolean-section-parameter`）。
- **重启文件默认都写**：`&SCF/&PRINT/&RESTART ON`（波函数，给下次的 `SCF_GUESS RESTART` 读）
  和 `&MOTION/&PRINT/&RESTART ON`（`-1.restart`，给 `&EXT_RESTART` 读）；`--no-wfn-print` /
  `--no-motion-restart` 可以关掉。
- **Poisson 求解器和周期性是绑定的**（`--poisson-solver`／界面「POISSON 求解器」）：手册逐个写明了
  每种求解器可用于哪些周期性——`PERIODIC` 只能三维周期，`MT` 只 0D/2D，`MULTIPOLE` 只 0D，
  `ANALYTIC` 0D/1D/2D，`WAVELET`/`IMPLICIT` 全都能用。所以界面上的下拉框**跟着「周期性」实时重建**
  （选 XYZ 时 MT/MULTIPOLE 直接消失，选表面板 XY 时 PERIODIC/MULTIPOLE 消失），
  默认「自动」= 三维周期不写 `&POISSON`（CP2K 默认正好是 PERIODIC）、非三维周期自动写
  `&POISSON PERIODIC <你的周期性>` + `MT`（原子数 > 200 换 `WAVELET`）。
  **注意 CP2K 自己不检查这个组合**（`pw_poisson_read_parameters` 里那条 `perd == cell%perd` 一致性
  检查是注释掉的），所以校验器只在**显式写了不匹配的求解器**时给 warning——CP2K 官方回归测试里
  有 238 个输入就是「`&POISSON PERIODIC NONE` 不写求解器」，那种情况不该报错（我第一版规则报了
  238 个 error，被语料测试当场抓住）。
- **每个 section 都显式 `&END`**：CP2K 不接受省掉 `&END` 的一行式写法（我对照了 2300+ 个官方回归测试输入确认过）。

---

## 3. 手册数据库（支持多版本）

`cp2kbuild/data/` 下每个 CP2K 版本一个库，**文件名里带着版本号**：

| 文件 | 对应版本 | 规模 |
|---|---|---|
| `manual_db_2026_2.json` | cp2k-2026_2-branch 手册 + 2026.2 源码 | 2843 sections / 25962 keywords（28 MB） |
| `manual_db_2025_1.json` | cp2k-2025_1-branch 手册 + 2025.1 源码 | 2483 sections / 22029 keywords（22 MB） |

**程序自动用最新的那个**，而且手册链接、生成的注释、界面里「?」的链接全都跟着走——不需要改任何代码。想固定用某个版本：

```bat
set CP2KBUILD_DB=E:\dsh_program\CP2K\cp2kbuild\data\manual_db_2025_1.json
```

打包成 exe 后，把另一个 `manual_db_*.json` 放进 exe 旁边的 `data/` 目录即可切换（优先级高于 exe 内嵌的那份）。

### 数据库是怎么来的

三个来源：

1. **手册爬取**：从 `https://manual.cp2k.org/<分支>/` 出发，按每页 "Subsections" 的 toctree 广度优先遍历，抽取每个 section 的描述、子 section 列表，以及每个关键字的类型、默认值、单位、别名、lone-keyword、usage、合法枚举值（含每个取值的解释）、描述、手册链接和对应的 GitHub 源码位置；
2. **CP2K 源码** `src/**/*.F`（2026.2 有 1325 个）：解析 `section_create(..., repeats=...)` 得到"哪些 section 可以重复出现"，解析 `keyword_create(..., repeats=.TRUE.)` 得到"哪些关键字可以重复写"，并收集全部 section/关键字名，用于弥合手册与 CP2K 自身的命名差异；
3. **动态列表**：`&XC_FUNCTIONAL` 下约 150 个泛函在手册里是页内锚点而不是独立页面，抓取时单独把它们收进来（否则 `&BECKE97` 会被误判为不存在的 section）。

爬取依赖手册的 HTML 结构（`<dl>/<dt>/<dd>`、嵌套 `<ul>`），解析器用配平扫描而不是正则回溯——枚举列表里嵌套 `<ul>` 或表格时，非贪婪正则会把后面的取值一起吞掉。

```bat
:: 换版本重建
python tools\build_db.py --branch cp2k-2026_2-branch ^
    --src E:\desktop\cp2k\cp2k-2026.2\src ^
    --out cp2kbuild\data\manual_db_2026_2.json
:: 或
python -m cp2kbuild db --rebuild --branch cp2k-2026_2-branch --src <cp2k>\src

:: 只更新源码相关信息（不联网，几秒钟）
python tools\build_db.py --keywords-only --src <cp2k>\src --out <库文件>
```

### 版本差异报告

换版本后想知道"到底变了什么"：

```bat
python tools\compare_db.py --old cp2kbuild\data\manual_db_2025_1.json ^
                           --new cp2kbuild\data\manual_db_2026_2.json
python tools\compare_db.py --old ... --new ... --section FORCE_EVAL/DFT   :: 只看某个子树
python tools\compare_db.py --old ... --new ... --all                      :: 完整清单
```

报告新增/删除的 section 和关键字、默认值变化、枚举取值增减。

## 4. 打包成 exe（PyInstaller）

```bat
pip install PySide6 pyinstaller
python packaging\build_exe.py            :: 两个都打
python packaging\build_exe.py gui        :: 只打 GUI
python packaging\build_exe.py --clean    :: 先清掉 build/ 和 dist/
```

产出两个单文件 exe：

| 文件 | 类型 | 说明 |
|---|---|---|
| `dist\cp2kbuild-2026.2.exe` | 控制台 | 命令行版，`cp2kbuild-2026.2.exe new --task geo_opt ...` |
| `dist\cp2kbuild-gui-2026.2.exe` | 窗口版 | GUI，双击直接用，**不需要装 Python** |

细节：

- 22 MB 的手册数据库通过 `--add-data` 打进 exe，`cp2kbuild/paths.py` 在冻结模式下从 `sys._MEIPASS` 找它；
- 排除了用不到的 Qt 模块（QtWebEngine、QtQuick、Qt3D、QtMultimedia 等），否则体积会翻倍；
- 图标由 `packaging/make_icon.py` 用 Qt 画出来，再用 ICO 容器（内嵌 PNG）写文件——不需要 Pillow；脚本会数一遍白色像素来确认字体真的渲染出了字形，而不是给了一个空白的圆角方块。

**exe 里不包含 CP2K 的基组/赝势数据**，运行时仍然需要能访问 `data/` 目录（程序会自动探测，也可以在 GUI 的「基组 / 赝势」页手动指定，或用 `--data-dir`）。

## 5. 作为 Python 库使用

```python
from cp2kbuild import Spec, build, read_structure, DataIndex, render
from cp2kbuild.validate import Validator

index = DataIndex(r"E:\desktop\cp2k\cp2k-2026.2\data")

for cutoff in (400, 500, 600):
    spec = Spec(task="geo_opt", structure=read_structure("mol.xyz"),
                functional="PBE", dispersion="d3bj", vacuum=12.0, cutoff=cutoff)
    result = build(spec, index)
    problems = [d for d in Validator(None, index).run(result.sections)
                if d.level == "error"]
    print(cutoff, "->", len(problems), "errors")
    open(f"mol_{cutoff}.inp", "w", encoding="utf-8").write(render(result))
```

## 6. 自测

```bat
python tools\selftest.py --limit 400
```

分五部分（part 1 里还带一节 1b：逐个参数旋钮生成 + 校验）：

1. **生成 + 自我校验**：对每种任务生成输入并用校验器检查，要求 0 error；
2. **拿 CP2K 自己的回归测试输入当靶子**：语料取自源码树的 `tests/TEST_DIRS` 列出的 454 个目录（共 5337 个 `.inp`，这是 CP2K 回归测试真正会跑的集合；`untested_inputs/` 这类不跑的目录会被剔除，`Fist/regtest-quip` 这种"要链接外部库才跑"的目录也跳过——`&QUIP` 只在链接了 QUIP 库的构建里存在，手册和源码树里都没有它）。这些文件是已知正确的，因此任何被报成 error 的都是本工具的误报或数据库缺口；
3. **官方截断能脚本（生成）**：`@SET CUTOFF LT_cutoff` / 字面值 / 带 `[Ry]` 单位标注 / 没有 CUTOFF 四种输入都要处理对——占位符已经在输入里时 `template.inp` 与它**逐字节相同**，只有字面值时除那两行外一个字节不动，`[Ry]` 要在占位符**前面**留着（插反了 `sed` 会产出 `CUTOFF 100 400.0`：一行两个值）;两条模板通道（「生成输入」页参数 / 已有输入）都要走一遍，`RUN_TYPE` 一律被改成 `ENERGY`（字面 `MD`/`GEO_OPT`、`@SET RTYPE`、没有 RUN_TYPE、`${RTYPE}` 改不动四种情况分开断言，并要求结果里说明改了什么），`METHOD FIST` 直接拒绝；再加上 7 个文件名、`sed` 的写法、`CHANGE_ME` 守护、从输入里读出的基组/赝势/`@INCLUDE` 列表、CRLF 保持、`.sh` 一律 LF，以及"输入里没有 CUTOFF"「非数字取值」这类必须报错而不是硬编的情况；
4. **生成的脚本真跑一遍**：`sh -n` 语法检查 → `cutoff_inputs.sh` 生成每个参数点目录（检查里面的 CUTOFF/REL_CUTOFF 确实换对了）→ 用假的 `mpirun`/`cp2k` 桩跑 `cutoff_run.sh` → `cutoff_analyse.sh` 写出 `cutoff_data.ssv`（能量随 CUTOFF 变化、每档网格点数进表、表头保持教程格式）→ 第 2 步在填好 CUTOFF 后也能一路跑通，而没填时脚本必须拒绝运行。有 `sh`/`bash` 的机器上才跑，没有就跳过。
5. **温度程序**：程序文本的解析（保持/升降温、`ps`/`fs`/`steps` 单位、坏输入要报错）、段划分（时长→整步数、每段的关键字与因子、系综决定机制、NVT 下写升温必须报错）、写出来的东西（每段输入 + `program.ssv` + `run_program.sh`，第一段没有 `&EXT_RESTART`、后面的段接前一段的 restart、每段输入校验 0 error、给了接力文件时第一段也从它接手）；再用假 CP2K **真跑一遍驱动脚本**——而且这个桩只写 CP2K 那种**带步数**的 restart（`<段名>-1_1000.restart`），这样"脚本会把 restart 归一化成 `<段名>-1.restart`"这件事才算真的验过；最后验"某一段没写 restart 时脚本立刻停下且只跑了第一段"。

目前的成绩：五个部分全部干净；part 2 在 3000 个文件里 **0 个**报 error。历史上那 3 个误报都修掉了：`DESCRIBE` 是 CP2K 输入解析器的指令（不是关键字，`input_parsing.F:209`）、`&QUIP` 需要外部接口所以那个目录不再进语料、孤立的占位记号（`XXXXXXX`）降为 warning——它和 `@IF` 分支里的东西、关键字拼错长得一样，不展开预处理器就分不出来。

这个测试是开发过程中最有价值的东西——最初 250 个样本里有 192 个误报，是靠它一个个查出来的（见下）。

GUI 另有一套无头自测（不需要显示器）：

```bat
python packaging\gui_selftest.py
```

它的核心思路是**等价性**：73 次比对里，界面里显示的文本必须和 `cp2kbuild new ...` 写出的文件**逐字节相同**——这样任何"控件接错参数"的问题都会暴露（其中五组是 MD 页上的元动力学块：关掉时输入必须一个字节都不变，开启井温元动力学 + 2 个 CV + 一张墙、PLUMED 路线的 CP2K 输入、**PLUMED 路线的 `plumed.dat` 预览页与 `--meta-plumed-draft -` 逐字节相同**，以及**单位开关 + RESTART + `ADAPTIVE` + `SIGMA_MIN/MAX` + `METAD` 其他关键字 + 自定义偏置标签**那一组同样逐字节相同）。加上布局断言（所有可见控件非零尺寸、预览区高度、基组表确实有行数，MD 页另外查了元动力学块收起/展开两种状态）、折叠断言（六个块都是默认收起、展开后内容可见、**收起不改变生成的输入**、状态记进 QSettings、勾上「启用元动力学」会自动展开）和 PLUMED 断言（CV/`&METAVAR` 在 PLUMED 路线下仍可用、CP2K 专有开关变灰、`SIGMA` 变亮、预览第二页出现、切换后「保存/复制」作用于当前页；单位下拉框选 Å 后 σ 那一格的标签跟着变 Å、选 `ADAPTIVE=DIFF` 时改成提示"步数"；「读回 `plumed.dat`」把 `examples/plumed_urea_cn.dat` 的 21 个 CV/单位/标签/`ARG=cv_uu` 都搬进控件、再生成的草稿与文件动作行一致；「把选中的相加」生成的 `COMBINE` 项数、系数与顺序正确、只选一行时不生成），以及交互断言（校验页必须抓到故意埋的三个错误）。这套测试当初确实抓出了一个真问题：`geo_opt` 和 `cell_opt` 两个页面用了同一个控件属性名，导致选几何优化的优化器时读到的其实是 cell_opt 的值；后来它又抓出两个：`--plumed-contact`/「读回」按钮把 Qt 的 `clicked(checked: bool)` 当成参数收下（按钮点了没反应），以及给 σ 输入框加 `setToolTip` 违反了"提示只挂在「?」上"的约定。

另外 `python packaging\screenshot.py` 会把各个页签渲染成 PNG（`packaging/shots/`），方便人眼检查外观；注意它用的是 Qt 的 offscreen 平台，在没有字体目录的环境里中文可能显示为空白，**外观要以真实窗口为准**。

打包出来的 GUI exe 也能自检（窗口版没有控制台，结果写成 JSON）：

```bat
dist\cp2kbuild-gui-2026.2.exe --selftest gui_report.json
```

它会跑真实的启动路径（手册库能不能从 `_MEIPASS` 找到、数据目录探测、8 个任务模板逐一生成、MD 页的元动力学块展开后配一套井温元动力学并把写出的 `&COLVAR`/`&METADYN` 数一遍），有任何一个任务出错或界面没加载完就返回非 0。

### 开发过程中它抓出来的真实 bug（举几个）

| 症状 | 根因 |
|---|---|
| 从 MD 切到单点能/几何优化，参数面板中间留一大段空白 | 任务页原来装在 `QStackedWidget` 里，而 Qt 是按**最高**的那一页给 stack 定尺寸、切页时又不会重新问 `sizeHint`：MD 页 ~2100 px（温度程序表 + 打印键 + 元动力学），单点能页只有 73 px → 差出来的 ~2000 px 就是空白。改成"隐藏其它页、只显示当前页"（隐藏的控件不参与布局），并在切页时主动跑一次布局；自测里对每个任务断言"只显示自己、且下面没有多余空白" |
| 第 2 步（扫 REL_CUTOFF）的曲线其实是一条 CUTOFF 曲线，`count for grid` 全是"全部落在最细网格" | `rel_cutoff_inputs.sh` 的两行 `sed` 照抄了第 1 步：`LT_cutoff` 拿了循环值、`LT_rel_cutoff` 拿了固定值。每个目录里 `CUTOFF=<目录名>`、`REL_CUTOFF=某固定值`——用户从 `.out` 里贴出 `cutoff [a.u.] 5.00`（=10 Ry，正好等于那一档的目录名 10）才定位到；现在两行按"扫谁谁拿 `${ii}`"生成，并逐个目录断言 |
| 每一档都在输入检查阶段 `[ABORT] The specified OLD file <cp2k-1_40000.restart> cannot be opened` | 源输入（或生成页的接力设置）里的 `&EXT_RESTART` 被原样带进了 `template.inp`：网格扫描根本不该接力，而那个 restart 文件在集群上不存在。现在两条通道都把这一段整段注释掉（原文保留），`@INCLUDE` 里的那种会点名让用户自己删 |
| 生成的 `&XC_FUNCTIONAL PBE` 后面没有 `&END` | CP2K 需要每个 section 显式 `&END`，否则嵌套错乱；用 2300+ 个官方输入验证 |
| Fe 配成 `DZVP-MOLOPT-SR-GTH`(q16) + `GTH-PBE-q8` | 选势函数时按"名字最短"排序，应该是按价电子数匹配 |
| `GTH-PADE` 在索引里找不到 | 赝势条目可以有多个别名（`C GTH-PADE-q4 GTH-LDA-q4 GTH-PADE GTH-LDA`），只解析前两个 token 会漏掉 |
| `H DZVP-GTH-BLYP`（没有 `-qN` 后缀）被当成"基组不存在" | `valence()` 返回 `None` 被当成了"找不到条目" |
| `&KIND F` 被解析成 `&KIND False` | 解析 section 参数时把裸 `T`/`F` 强转成布尔值，而 `F` 是氟 |
| `EMSL_BASIS_SETS` 里的 `LI` 条目全部消失 | 元素 token 强制要求标准大小写，大写元素被丢弃 |
| `PW_GRID`/`POTENTIAL_TYPE` 的枚举值少了几个 | 手册的枚举列表里嵌套 `<ul>`/`<table>`，非贪婪正则把后面的取值一起吞掉 |
| `&2PNT` 没被识别为 section | section 名正则不允许数字开头 |
| `CUTOFF 200 ! 320` 被当成 3 个值 | CP2K 也接受 `!` 作为注释符、`\` 作为续行符 |
| `CHARGE=-1` 被当成关键字名 | CP2K 允许 `KEYWORD=value` 不写空格，也容忍 `EPS_SCF:` 带冒号 |
| `ADDED_MOS` 被写进 `&OT` | 它属于 `&SCF`；`check_spec.py` 对照手册发现的 |
| `&KIND F` 明明没被任何原子使用却报"势不存在" | CP2K 根本不看未使用的 KIND 块（`H2_MP2_debug.inp` 就是靠这个才跑得起来） |
| NEB 输入没有 `&CELL` | 每个镜像会被塞进默认的 1 Å 立方体；现在会警告并可从镜像文件推断晶胞 |
| "GEO_OPT 的子 section" 列出了整个手册 | 把页面**侧栏**的 toctree 当成了页面自己的 "Subsections" 标题块 |
| 图标生成时解释器崩溃（0xC0000005） | PySide6 的 `QBuffer(QByteArray())`：临时 QByteArray 被回收，QBuffer 拿着野指针 |
| `new --stdout > job.inp` 会把校验报告写进输入文件 | 用 `--stdout` 时诊断信息改走 stderr（GUI 等价性测试顺带发现的） |
| 生成的输入文件里 `&END NOSE` 被当成上一行的取值，导致 section 嵌套错乱 | `@INCLUDE` 指令行打断了续行缓冲：`TIMECON \` 后面直接跟 `@INCLUDE file0.inc`，缓冲没刷新就拼上了下一行 |
| `cp2kbuild converge` 报 `summarise() takes 1 positional argument but 2 were given`（那条命令已经移除，留作教训） | `cli.py` 先 `from .analysis import ... summarise`，随后 `from .validate import Validator, summarise` 把前一个**覆盖**了。单测直接调 `analysis.summarise` 永远是绿的，只有走 CLI 才会炸——所以自测里加了端到端跑 CLI 的那两条 |
| 一个目录里两套扫描（`cutoff_*` 和 `rel_cutoff_*`）被混成一张表，参考点也串了 | 两套扫描的"最细"不是同一个设置，混在一起比毫无意义。改成先按"哪个参数在变"拆族，每族各用自己的参考点 |
| `REL_CUTOFF` 建议值给出 20 Ry，可那一档下 `grid 1` 的网格点数是 0 | 只按能量判据 20 Ry 确实"收敛"了——但那档网格完全没被用上，说明网格分配是错的。建议值现在要求每档都有点，并显式写出"为什么 20 被否掉" |
| 同一个参数点被算了两遍，表里出现两行 | 两套扫描的交叉点（`cutoff_650Ry` 与 `rel_cutoff_60Ry`）本来就是同一次计算。现在会合并；如果两次能量不一致则直接告警 |
| 模板是 LF，生成的文件却全变成 CRLF，每行多一个 `\r` | Windows 上 `Path.write_text` 会把 `\n` 静默翻译成 `\r\n`。后果不只是"多一个字符"：`@SET CUTOFF 450\r` 的值里会带着回车，`run.sh` 到了 Linux 上更是直接 `\r: command not found`。改成按字节读写（生成端一律 LF，模板模式原样保留），并且**自测改成比字节**——原来的比法是先 `read_text()` 再比行文本，而 `read_text()` 会把 CRLF 归一化成 LF，正好把这个 bug 藏起来（真实模板是 95 CRLF + 30 LF 的混合行尾，一归一化就看不出来了） |
| 每个 print key 都写成了 `&FORCES T` | `T` 在这里根本不合语法：print key 的段参数是**严格枚举** `ON/OFF/SILENT/LOW/MEDIUM/HIGH/DEBUG`（`cp_print_key_section_create`），CP2K 的输入解析器对枚举不给任何逻辑值退路，会直接 `invalid value for enumeration:T` 退出。`&OT T` 之所以没事，是因为 OT 的段参数是**逻辑型**——两者在文件里长得一样。改成 `ON`，并加了一条按手册 `SECTION_PARAMETERS` 类型判断的校验规则（扫 4000 个 CP2K 官方回归输入：0 个误报） |
| 扫描生成的 `README.md` 里 "How to read the results" 出现两次，还写着"每个参数点一个目录" | `--sweeps grid`（单目录）复用了 `--sweeps separate`（一档一目录）的说明文字，两段正文又用了同一个标题 |
| 界面上把真空层从 12 改成 0，预览里还是那个大盒子 | `build()` 里的 `prepare_structure` 直接改了**界面传进去的那个结构对象**（加晶胞、平移到盒心），于是第一次预览之后 `self.structure` 就永久带着那个真空盒。改成先 `structure.copy()` 再动——库函数不该改调用者的对象 |
| 「恢复默认」按钮把 `SCF_GUESS` 恢复成 `ATOMIC` | 那个按钮自带一份默认值副本，改默认值时漏了它；现在改为断言「点完按钮的输出 == 命令行默认输出」，8 个任务逐个比 |
| `POISSON_SOLVER PERIODIC` 配 `PERIODIC NONE` 被误报为 error（238 个官方输入中招） | 手册说 PERIODIC 只用于三维周期，但 CP2K **不检查**这个组合（`pw_poisson_read_parameters` 里那条一致性检查是注释掉的），而它的默认求解器就是 PERIODIC——所以「不写求解器」不是错。规则改成只看**显式写出**的求解器，而且降为 warning |
| 校验输入页的「添加文件…」点了永远没反应 | 目录记忆那两个辅助函数定义在「生成输入」页上（`_start_directory`/`_remember_directory`），而 `ValidateTab` 自己没有——`AttributeError` 被 Qt 事件循环吞掉，看起来就像按钮没接上。拖放同一个列表是好的，所以一直没暴露。现在通过 `self.window().generate` 调用，并且加了一条拿假文件对话框真调一次 `add_files()` 的检查 |
| 刚启动就点关闭 → 进程直接 abort（退出码 `0xC0000409`，stderr 一行 `QThread: Destroyed while thread '' is still running`） | 后台加载手册数据库的 `LoaderThread` 没有父对象，`closeEvent` 也不等它：窗口一销毁就等于销毁一个**正在运行**的 QThread，Qt 的处理方式是终止进程。现在线程挂在窗口上，关闭时 `wait()` 到它结束（实测整次加载 <1 s） |
| 保存到别的目录后，磁盘上的文件与预览不一致（力场文件名要么是 Windows 绝对路径、要么是短名） | `save()` 先用**旧的**输出目录把文本算好了，选完路径才填 `w_out`；「文件路径写法＝自动」正是按输出目录决定写长名还是短名的。现在先把目标路径写进 `w_out` 再重新生成，生成失败就什么都不写 |
| `SCHEME MONKHORST-PACK` 和 `KPOINT x y z w` 同时出现，校验器还说"没有问题" | 显式 k 点表和 SCHEME 是两个独立控件，写的时候互不相干；可手册写着 KPOINT 行只在 `SCHEME GENERAL` 下被读。现在**显式列表压过方案名**（写成 `GENERAL` 并在文件里注明原因），说明行也会点出这个冲突 |
| 只选 `MONKHORST-PACK` 不给数字 → 写出一行 CP2K 读不了的 `SCHEME MONKHORST-PACK` | 手册原文："For MONKHORST-PACK the number of k points in all 3 dimensions has to be supplied along with the keyword"。命令行 `--kpoint-scheme` 收的是原始字符串，很容易这么写；GUI 上「按间距自动」勾着时手填网格框看着能编辑、值却被忽略。现在选这两个方案会自动取消「按间距自动」，网格框空着就按间距估算补上，writer 里也兜一层 |
| `--barostat MTS` 这个开关什么也不做，`MTS` 也不是 CP2K 的关键字 | `&BAROSTAT` 只有 `PRESSURE/TEMPERATURE/TEMP_TOL/TIMECON/VIRIAL`（2025.1 与 2026.2 手册一致），压浴算法由**系综**决定、压浴的恒温器是子段——所以"压浴类型"这个字段本身就不存在。删掉它（连带那条已死的耦合规则），也顺手清理了 `info --kind`、`db --stats` 这类"声明了却没读 `args`"的开关 |
| 「1-4 缩放」旁边的 T/F 三态下拉框选了没反应 | `VDW_SCALE14`/`EI_SCALE14` 是**数值**（AMBER 0.5 / 0.8333），不是逻辑值，那个下拉框从来没有代码读它——更糟的是"F"看字面像"关掉 1-4 缩放"，实际运行仍按力场的值缩放。删掉控件，只留两个数值框 |
| `EPS_DEFAULT` 填 0：GUI 写 `1.000000E-10`，命令行写 `0.0` | GUI 用 `_optional_float(...) or 1e-10` 把 0 静默换成了默认值，CLI 照抄用户输入。`EPS_DEFAULT` 按比例缩放所有 SCF 阈值，0 等于要求收敛到 0。现在填 0/负数会把输入框标红并说明"按 CP2K 默认 1e-10 写"，两个前端不再各写各的 |
| 晶胞框里打字时 POISSON 求解器下拉框会停在旧周期性 | `w_cell.textChanged` 直接连到 `_poisson_changed`，中间 `parse_cell_text` 对"30,30,3"这种半截输入抛 `ValueError`，异常被事件循环吞掉、后面的刷新不再执行。现在半截输入按"还没填晶胞"处理，框填完整后一切照旧 |
| 半填的 k 网格（`6 6 x`）或 PDOS LDOS 让「生成温度程序」点了没反应 | 槽函数里的异常被 Qt 事件循环吞掉，窗口版连 traceback 都看不到。现在 `collect_spec()` 的异常被捕获并写进提示行（带上异常类型），预览那边照旧会显示"生成失败" |
| 打包版（exe）里 `db --rebuild` 报 "cannot find ...\Temp\_MEI...\tools\build_db.py" | 爬手册的脚本在源码目录里，PyInstaller 只打包 `cp2kbuild` 包。现在识别 frozen 模式并直接给出"请在源码目录里跑 python tools/build_db.py …"的完整命令 |
| 手册数据库读失败时整个窗口永久灰着，只能关掉重开 | 错误分支弹完对话框就 `return` 了，`tabs.setEnabled(True)` 在后面；而"生成/校验"其实不依赖手册（物理一致性检查是独立的），「基组 / 赝势」页还是换数据目录的地方。现在降级成"没有手册也能用"，并把失败原因记进自检报告（无头自检也置 `silent`，免得模态框把测试挂住） |
| 「收敛测试」页每次启动都不记得上次选的那份 `.inp` | `closeEvent` 一直在写 `scan_source_file`，但没人读回来。现在启动时读回（`scan_outdir` 本来就是这么做的） |
| 手填网格框空着时 `&KPOINTS` 整段消失（勾了 k 点却什么都没写） | 修上一条"不带数字的 SCHEME"时才发现：空网格 + 无间距会让四个字段全为空，`&KPOINTS` 段干脆不写。现在空网格按间距估算，至少写出一个完整网格 |
| 把 `cp2k-1.restart` 拖进结构框、点「读参数」，「从上次续算」那一格自己多出一个 `True`（还把它勾上，输入里就凭空长出一段 `&EXT_RESTART`） | **CP2K 自己写的 restart 里 `RESTART_FILE_NAME` 是有键无值的空行**。输入解析器把"无值"读成逻辑真，`_cast()` 又照着"这一项要字符串"把 `True` 转成 `str(True)`——于是文件名那一格填进了四个字母。现在 `_cast()` 只要遇到布尔值而目标不是逻辑型就返回 `None`（无值就是没值），`&EXT_RESTART` 也只在文件名**非空**时才读回来；文件名是空的那种会在「没读」清单里点名说明（"CP2K 的 restart 就是这么写的，续算相关的设置没有读——要续算就自己填那一格"）。反例一并钉进自测：真正用来接力的 `RESTART_FILE_NAME prev-1.restart` 照读不误，`RESTART_TAKE vel,counters` 也跟着读回来 |
| 同一个问题在报告里出现两次，而且一次是「警告」一次是「错误」（`警告 AMBER: no PARM_FILE_NAME, so CP2K has no force field.` 紧挨着 `错误 PARMTYPE AMBER reads the force field from a file, but PARM_FILE_NAME is not set`） | 生成器和校验器各自都看得见同一件事：`_classical_findings()` 把「没有 `PARM_FILE_NAME`」「没有 `CONN_FILE_NAME`」「没有 `&CELL`」当成**警告**报，校验器的 `mm-parmtype-without-file`/`mm-connectivity-without-file`/`periodic-without-cell` 当成**错误**报。三件事其实都是 CP2K 的硬中止（`force_fields_input.F` 的 "Force Field Parameter's filename is empty!"、空文件名进 `cp_files.F` 的 "The specified OLD file <> cannot be opened"、`cell_methods.F` 的 `case 4`），所以现在生成端也报 error，并且用 `BuildResult.covered_codes` 告诉合并报告"这几条我已经说过了"——**一件事一行、一个等级**（GUI 的 `without_covered()`、CLI 的 `report()` 都过滤）。顺手把缺晶胞那条按坐标格式分了岔：CRD/PSF 这些格式 CP2K 根本不会去读盒子（`read_coordinate_crd` 里 "BOX information ... will be ignored"）→ error；PDB/CIF/CP2K/EXTXYZ 是 `case 4` 认的四种 → warning |
| 报告一半中文一半英文（`提示 keyword-alias …` 是中文，`错误 PARMTYPE AMBER reads the force field from a file …`、`提示 this ensemble changes the cell …` 是英文） | 校验器的 85 条消息 + 39 条「建议」、生成器的 22 条生成期提醒，全部改写成中文（**CP2K 自己的原话仍照引英文**，放在中文句子里的引号中，方便对着源码搜）。自测里加了一条语言检查：把 CP2K 语料过一遍校验器，**每一条消息和每一条建议都必须含中文**（1500 个输入、6043 条诊断，0 条纯英文）。顺带 `summarise()` 的收尾行也变成「0 个错误、0 个警告、3 条提示」 |
| 手写的 `plumed.dat` 用续行写法（`cv: COORDINATION ...` 一行一个关键字、`...` 收尾）读回来等于被掏空：CV 只剩一个 `...`，`ARG`/`SIGMA`/`HEIGHT`/`PACE`/`BIASFACTOR` 全掉回默认值，后面每一行都变成「工具不认识的自由行」（读进去再存出来就把人家的元动力学设置改没了） | 那是手册 **"Continuation lines"** 一页的正式写法（`SWITCH={RATIONAL …}` 与 `NLIST`/`NL_CUTOFF`/`NL_STRIDE` 也都是 `COORDINATION` 的正当关键字），所以按用户的要求「工具得能读」：解析前先把 `ACTION ...` 到 `...` 之间的行**折成一行**（注释和空行允许出现在中间，收尾的 `... 动作名` 也按手册去核对名字），再走原来的解析。回写时不会留下单独的 `...`（PLUMED 会把它当成续行的开头）；没写收尾、多出来的 `...`、收尾名字不对，都会点名。顺带修掉一个误报：`SWITCH={RATIONAL R_0=2.30 NN=12 MM=24}` 已经把 `R_0`/`NN`/`MM`/`D_0` 给全了，旧检查还按手册表格说「没写 R_0」——现在会看 `{…}` 里给的东西 |
| 「PLUMED 能分别给多个 CV 定义 σ，那工具是不是还有点小问题」——确实有：σ 在文件里是 `SIGMA=0.15,0.5`（**每个 ARG 一个值**），可界面上只看得见一个「默认 SIGMA」，CV 表里没有 σ 这一列；而且当每个 CV 都带自己的 σ 时，改那个「默认 SIGMA」**完全不起作用**（生成的 `SIGMA=0.15,0.5` 一动不动），看着像编辑丢了；另外 `SIGMA=0.15` 配两个 ARG 这种 PLUMED 会拒绝的写法被工具悄悄"修好"，一声不吭 | 三处都改了：①CV 表加了第五列「该 CV 的 σ」（显示自己的值，或「默认」）；②「默认 SIGMA」下面多一行会随状态更新的说明——"表格里 2 个 CV 各自设了 σ（d1 0.15、d2 0.5）：上面这一格现在用不上，改它不会影响文件"，部分 CV 带 σ 时说清只影响其余几个，ADAPTIVE 打开时说清单 σ；③读文件时，**全 CV 同一个宽度**（`SIGMA=0.15,0.15`）就提升成默认值，面板直接显示 0.15、那一列写「默认」，改这一格真的会改到文件；只给一个值却有多个 ARG 时按 PLUMED 的原话点名（`number of arguments does not match number of SIGMA parameters`）并写成铺开的形式。依据是 `MetaD.cpp`：`parseVector("SIGMA", sigma0_)` 之后非 ADAPTIVE 时 `sigma0_.size()!=getNumberOfArguments()` 直接 error，ADAPTIVE 时反过来要求 `size()==1` |
| 表里明明有一行「警告 build-warning」，上方摘要却写着 **「0 错误 / 0 警告 · 1 项生成期问题」**（CLI 是同一个毛病：先打 `error:` 行，收尾却写「0 个错误」） | 生成器自己的发现在摘要里被**排除**在错误/警告计数之外，另算成"N 项生成期问题"——那是这些发现还只挂在悬浮提示里的年代留下的写法。现在它们就是同一张表里的行（`build-error`/`build-warning`），所以**按行数统计**：GUI 的 `_show_summary()` 数所有行，另外用一句「· 其中 N 项来自生成器」说明来源，不再二次计数；CLI 的 `summarise()`/`report()` 新增 `extra_errors`/`extra_warnings`，把 `new` 已经打过的 `warning:`/`error:` 行算进收尾行（否则退出码 1 却写着"0 个错误"） |

## 7. 目录结构

```
cp2kbuild/
  model.py       Section/Keyword 树 + CP2K 格式化输出
  structures.py  xyz / pdb / POSCAR / cif（含对称操作）/ CP2K &COORD 读取（含 -1.restart）
  datafiles.py   CP2K data/ 目录索引（基组、赝势、别名、价电子、元素覆盖）
  recommend.py   基组与赝势的选择算法（价电子匹配优先）
  recipes.py     energy / geo_opt / cell_opt / md / freq / band / pdos / neb 模板
  metadyn.py     元动力学：&SUBSYS/&COLVAR 的 CV 定义 + &FREE_ENERGY/&METADYN（含自检）
  kpoints.py     高对称 k 点路径（Setyawan-Curtarolo）
  parser.py      CP2K 输入文件解析器（宽容模式，不执行预处理器）
  template.py    把已有输入当**文本**扫描：只替换占位符，@SET/@IF/@INCLUDE 原样保留
  cutoffkit.py   写官方教程那套 CUTOFF/REL_CUTOFF 脚本（template.inp + 6 个 sh）
  tempprogram.py 多段温度程序：保持/升温/降温 → 每段一个输入 + run_program.sh（分段接力）
  validate.py    手册模式校验 + 物理一致性校验
  manualdb.py    手册数据库查询层
  paths.py       资源定位（源码运行 / PyInstaller 冻结模式）
  helptext.py    界面里「?」的引导文本（含手册位置，可点击跳转）
  mmcheck.py     经典 MD 的坐标/拓扑一致性检查（prmtop/PSF/PDB/inpcrd 读取）
  cn_glossary.py 中文层：116 条高频关键字中文说明 + 98 条术语对照 + 中文搜索
  cli.py         命令行（new / ramp / scan / mmcheck / validate / info / keyword / db）
  gui.py         PySide6 图形界面
  data/          手册数据库（2026.2 26.6 MB + 2025.1 22.2 MB JSON，自动选最新）
tools/
  build_db.py    手册数据库生成（全量 / --keywords-only / --dynamic-lists）
  selftest.py    生成（含 6 条经典力场路线）+ 官方回归误报测试 + 截断能脚本生成/真跑（4 部分）
  make_examples.py   重新生成 examples/generated（DFT 8 个 + 经典力场 4 个）
  fist_smoke.py      六条经典力场路线各生成一份输入并打印（人工看一眼用）
  fist_gui_check.py  无头打开界面，切到 FIST 页检查显示/取值（人工看一眼用）
  check_spec.py  核对生成端用到的 section/关键字名是否都在手册库里
  check_cn_glossary.py  核对中文说明指向的手册位置是否真实存在
  compare_db.py  两个版本的手册库对拷（section/关键字/默认值/枚举的增删改）
  dump_tree.py   打印某个 CP2K 输入的解析树（调试用）
  check_end_required.py  统计官方输入里 section 是否都写了 &END
packaging/
  build_exe.py      PyInstaller 打包（两个 exe）
  gui_entry.py      GUI 的 exe 入口
  cli_entry.py      CLI 的 exe 入口
  make_icon.py      用 Qt 画图标并写成 ICO（含字形渲染自检）
  dump_help.py      把全部「?」帮助导出成 markdown（打包/help.md）
  gui_selftest.py   GUI 无头功能测试（GUI 输出必须与 CLI 逐字节一致）
  screenshot.py     把各页签渲染成 PNG
examples/        示例结构与生成结果（`examples/generated` 是两种路线的成品输入）
legacy/           已归档的旧实现（旧的 scan/converge 与它的假扫描夹具），不参与打包与测试
```

## 8. 验证与已知限制

### 已经验证过的

| 项目 | 怎么验的 | 结果 |
|---|---|---|
| 生成器 | 8 个任务模板生成 + 自我校验 | 全部 0 error |
| mmcheck | 6 组造出来的 prmtop/PSF/PDB/inpcrd 夹具：prmtop 的 %FLAG POINTERS/%FLAG ATOM_NAME、PSF 的 !NATOM 与原子名列、inpcrd 的盒子与速度行都要读对；原子数不符要报差额、NMOL 写错要报差额、原子名不同**不能**报错（官方 multi_psf 就是名字不同的）；再拿 CP2K 自己的 multi_psf.inp（1762 原子）和 lamol.inp（4×50）核对 | 全部通过 |
| 经典 MD（FIST） | 6 条力场路线（AMBER/CHARMM/GROMOS96/GROMOS87/参数写在输入里/纯对势）各生成一份：PARMTYPE/CONNECTIVITY/COORDINATE 必须等于 CP2K 枚举里的字面值、树里不能出现 &DFT/&SCF/&QS/&MGRID/&KPOINTS、校验器 0 error；再断言 NPT 写了 STRESS_TENSOR ANALYTICAL、AMBER 拿到 0.5/0.8333 而不是 CP2K 默认的 0.0、非周期退成 EWALD_TYPE NONE、混合体系是 MOL_SET + 每分子一个 &MOLECULE；8 个经典 MD 错误输入必须被点名 | 全部通过 |
| 校验器 | 拿 CP2K 2026.2 回归测试语料（`TEST_DIRS` 的 454 个目录、5337 个 `.inp`）里的 3000 个跑一遍，已知正确的文件不该报 error | 0 个文件报 error |
| print key 段参数的类型 | 全库扫一遍：段参数是枚举的 section（print key）只允许 `ON/OFF/SILENT/LOW/MEDIUM/HIGH/DEBUG`，写成 `T/F` 必须是 error | 4000 个官方输入 0 个误报；自己生成的文件里曾经的 `&FORCES T` 全部改成 `ON` |
| GUI ↔ CLI 一致性 | 73 次比对：界面预览必须和 `cp2kbuild new ...` 写出的文件逐字节相同（含元动力学关闭 / 井温元动力学 + 墙 / PLUMED 三组，以及单位开关 + `RESTART` + `ADAPTIVE` + `SIGMA_MIN/MAX` + `METAD` 其他关键字 + 自定义偏置标签那一组） | 全部一致 |
| 截断能脚本页 ↔ CLI | 两条通道各来一遍：来源下拉框的取值与默认档、按通道显示/隐藏文件框与提示文案（在 MD 页上要提醒 RUN_TYPE 会被固定）、带占位符的输入写出的 8 个文件（7 个 + `@INCLUDE`）必须和 `cp2kbuild scan --input ...` 写出的**逐字节相同**、字面值与 `[Ry]` 单位标注的输入和 `cp2kbuild scan --input ...` 也逐字节相同、「生成输入页参数」通道和 `cp2kbuild scan --structure ...` 逐字节相同；默认范围/固定值、`CHANGE_ME` 守护、缺文件与缺协议时的提示都逐条断言 | 全部一致 |
| 续算默认是关的 | 第 6 节「从上次续算」在**没给文件**时：`都以该文件为准` 不勾也不可改，预览里没有 `&EXT_RESTART` 段（真实反馈：拖进 restart 当结构后那一项是勾着的，整段看着像已经开启）。**填上**文件且「取哪些量」是「全部」时才自动勾上（= `RESTART_DEFAULT T`，和 CLI 的 `--restart FILE` 默认一致）；此后手动取消能留住（刷新不会勾回去，`RESTART_DEFAULT F` 也照写），清空文件则连勾带段一起消失 | 全部通过 |
| GUI 布局 | 所有可见控件非零尺寸、预览/诊断/基组表都有内容；左侧参数栏在每种状态下都**不出现横向滚动条**（`geo_opt` 页 / MD 页 / 元动力学展开 / PLUMED 路线 21 行 CV + 墙，要求 视口 ≥ 内容最小宽度）；「保存/复制/恢复默认」在滚动区**外面**、贴左栏底部，表单滚到顶或底时它的位置不变；「每 N 步 / 最后一步也写 / 备份」分两行（行宽 < 420 px）；表格不按列宽和撑宽面板 | 全部通过 |
| 路线摘要跟着方法走 | 选 `METHOD FIST` 时「当前路线」必须换成「经典力场（METHOD FIST）——不写 `&DFT`/`&SCF`」，不能再留着上一次算出的 `OT`；OT/对角化两块都被判定隐藏；**切回 QUICKSTEP 后摘要与 OT 块必须恢复**（另一条真实反馈：那一行摘要故意放在可折叠的 3.2 外面，而 `_method_changed` 只收起了 3.2，于是它还挂着上一次的 `OT`） | 全部通过 |
| 参数字段 | 新增的参数（SCF 鲁棒性、网格、混合、分数坐标、MD 耦合、重启打印…）逐项生成 + 校验（0 error），并断言参数确实写进了输入；GUI 与 CLI 对每个参数组合逐字节一致 | 全部通过 |
| MD 温度打印 | 三种机制各一个用例：`&MD TEMP_KIND T` 与 `&MD/&PRINT/&TEMP_KIND` 必须**成对**写（只开一半时校验器分别报 error/info，CP2K 自己只会警告）、`&ENERGY` 的间隔与 `FILENAME`、`&THERMOSTAT REGION MOLECULE` + `&PRINT/&TEMPERATURE`；写出的每个 section/keyword（含 `&EACH`）都拿手册数据库核对存在性；界面默认三个开关都关、生成的输入与命令行逐字节相同；NVE / 恒温器 NONE / 非 MD 任务下控件自动变灰 | 全部通过 |
| 元动力学（metadynamics） | 三种组合（井温 + 距离/配位 CV + 二次墙；组合 CV + 路径/RMSD CV + 扩展拉格朗日 + Langevin；PLUMED 驱动）各生成一份、校验 0 error，并断言两半都写对了：`&SUBSYS` 里 `&COLVAR` 的**顺序** = `&METAVAR COLVAR n` 的序号、墙的类型只带自己的关键字（`DIRECTION`/`K` vs `SIGMA`/`WW`）；49 个关键字/子段（含 4 种墙和两个 `&EACH` 段）逐个拿手册数据库核对路径与写法；12 种写法问题由生成前的自检抓出来（原子越界、`DO_HILLS F`、井温缺 `TEMPERATURE`/`DELTA_T`、`&METAVAR` 指向不存在的 CV、`LAGRANGE` 缺 `MASS`、给了 `GAMMA` 却没开 `LANGEVIN`、墙类型错、组合 CV 缺 `FUNCTION`、路径 CV 缺 `&FRAME`、原子个数不对）；7 个非 MD 任务下那两段一个字都不写、并给出一条"只在 MD 里生效"的警告；PLUMED 路线只写 `USE_PLUMED` + 文件名（CV/丘陵/墙/`&PRINT` 一个都不写）；**`plumed.dat` 草稿**：单位换算（Å→nm、Hartree→kJ/mol，和 CP2K 测试文件的 `HEIGHT=5.251` 对得上）、四类 CV 的对应、`KINDS_*` 按结构展开、非周期加 `NOPBC`、`SCALE`→`COEFFICIENTS`、墙→`UPPER_WALLS`/`LOWER_WALLS`（`AT`/`KAPPA` 都换算）、井温→`BIASFACTOR`，以及**不假装翻译**的部分（组合 CV 只给子 CV + 表达式注释、路径/RMSD 给 `PATHMSD`/`RMSD` 占位、反射墙明说没有直译、没填 SIGMA 时写 TODO、没有结构时不猜原子组、没有可用 ARG 时不写空 `METAD` 行）；手写输入里的别名（`R_0`/`EXPON_NUMERATOR`/`EXPON_DENOMINATOR`）按 `R0`/`NN`/`ND` 读回，工具不建模的关键字（`KINDS_TO_B`）进"没读"清单；CP2K 自己的 80 个元动力学回归输入全部能读出来（0 异常；工具不建模的关键字会被点名列进「没读」，所以"读进来"这件事本身是干净的）；界面：CV/`&METAVAR` 两张表**不排序**（行序就是序号）、两个对话框对 7 种 CV 与 5 种 `&METAVAR` 都原样读回、非 MD 任务整页变灰并说明原因、预览第二页与 `--meta-plumed-draft -` 逐字节一致。**PLUMED 专用表的新一批断言**（都对着 PLUMED 2.9 源码核过）：单位开关 Å 时 `UNITS LENGTH=A TIME=fs ENERGY=kj/mol` 必须是**第一个动作行**（`src/setup/Units.cpp` 是 setup action，`ActionSetup.cpp:36` 会拒绝排在后面的），而且单位串**大小写敏感**（`src/tools/Units.cpp:42-57` 逐字符比较，`kJ/mol` 会被拒——读回手写文件时会点出这个错并把回写改成规范写法）；`ARG=` 留空时取「叶子」标量（20 个中间量 + `COMBINE` 的表里只有总量进 `ARG`，并且不再误把多分量引用 `cn.mean` 当成"没被引用"）；多分量动作的「分量」会被补到动作行上（已写的不重复），`MORE_THAN` 这类必须带值的分量没给值会提醒；`RESTART=YES`（并注明找不到 `HILLS` 时 2.8+ 直接报错）；`ADAPTIVE=GEOM\|DIFF` 时 `SIGMA` 只能一个数、`DIFF` 必须是整数、表格里给单个 CV 设的 σ 会被忽略、`SIGMA_MIN/MAX` 是**每个 CV 一个**（只给一个时替用户铺开成 N 个）、没开 `ADAPTIVE` 时写它们会让 PLUMED 报 `cannot understand the following words`、存网格（`GRID_*`）时手册要求必须给 `SIGMA_MIN`、以及 `sum_hills --histo` 的直方图修正提醒；读回 `examples/plumed_urea_cn.dat`（21 个 CV、单位 Å、标签 `metad`、`ARG=cv_uu`、0 问题、回写后 23/24 行动作逐字节相同）与一个手写小文件（分量/墙/未知 METAD 关键字/对不上号的 `PRINT` 都归位）；接触型 CV 生成器用 `19x1,8x20` 生成的 21 行与那份真实文件**逐字节相同**，分块总数与体系原子数不符会提醒、块号/偏移越界直接报错 | 全部通过 |
| AIMD 升温 / 降温 | 换算器：`plan_ramp` 的温度模式 `f=(T2/T1)^(1/N)`、速度模式 `f=(T2/T1)^(1/2N)`、时长与 K/ps；机制判定（LANGEVIN→温度、NVE/NPE→速度、NVT/NPT→两条都不行，且要求升降温度时直接报错）；写出的关键字（`TEMPERATURE_ANNEALING` + `&LANGEVIN GAMMA`、`ANNEALING`、`ANNEALING_CELL`、`RESTART_TEMPERATURE_ANNEALING`）逐条生成 + 校验 0 error；**系综用不上的因子不许写进输入**（NVT 下三个关键字一个都不出现，否则 CP2K 会中止或静默无效）；手写输入里的 `ANNEALING`×恒温器报 error、`TEMPERATURE_ANNEALING` 非 LANGEVIN 报 warning、`GAMMA 0` 报 warning、没有斜坡却要接力报 warning；界面勾选框算出的因子与 `new --temperature-ramp` **逐字节一致**、NVE/NVT 下自动换机制或变灰清空 | 全部通过 |
| 温度程序（多段接力） | 程序解析（保持/升降温、`ps`/`fs`/`ns`/`steps` 单位、5 类坏输入被拒）、段划分（时长→整步数、每段关键字/因子、系综决定机制、NVT 写升温直接报错、跳变/过快/NVE 保温三条提示）；生成的 6 段输入各校验 0 error、第一段无 `&EXT_RESTART`、后面各段接上一段的 restart（坐标/速度/计数器/波函数）、计划表 `program.ssv` 一行一段、第一段可用接力文件播种子；驱动脚本：串行链条（无后台任务、无 `wait`）、按集群标准起作业（`OMP_NUM_THREADS=2` + `mpirun -np 64 cp2k.psmp -i/-o`）、`RunOptions` 改 `-np`/OMP/可执行/`mpirun_extra`/续跑都真的写进脚本、**断点续跑**（跑完的段跳过；改动某段输入后它和它后面的段都重跑）、某段没写 restart 时立刻停下且只跑第一段；输出目录的「浏览…」走系统目录框且记住上次路径、「打开目录」在文件管理器里打开（目录为空/不存在时是灰的或给出说明）；界面控件 → `ramp --program` **逐字节一致**（含启动参数那一组） | 全部通过 |
| 截断能脚本（生成） | 四种输入风格（`@SET` 占位符 / 字面值 / 带 `[Ry]` 单位标注 / 没有 CUTOFF）、两条模板通道（现有参数、已有输入）、`RUN_TYPE` 强制成 `ENERGY`（字面 `MD`/`GEO_OPT`、`@SET RTYPE`、没有 RUN_TYPE、展开前改不动的 `${RTYPE}` 五种情况，并要求结果里写明改了哪一行）、`METHOD FIST` 被拒、`&EXT_RESTART` 被整段注释（含"藏在 `@INCLUDE` 里要点名"和"生成页的接力设置一个都不写"）、7 个文件名、`sed` 写法、`CHANGE_ME` 守护、从输入读出基组/赝势/`@INCLUDE` 列表、CRLF 保持、`.sh` 一律 LF、非数字取值被拒 | 全部通过 |
| 氘代（H→D） | 写出的 `&KIND H / MASS 2.0141017781` 里元素仍是 H（基组/赝势跟着氢走）；勾了却写 0.5 fs 时提示可提到 1.0 fs、没勾却写 1.0 fs 时反过来提示；没有氢的体系与 METHOD FIST 都不写 MASS 并说明原因；界面在"没有氢 / 单点任务"时变灰、FIST 下整块区域隐藏；`&KIND MASS` 的存在性拿手册数据库核对 | 全部通过 |
| 读参数（已有输入 → 表单） | 逐个旋钮的往返：生成的每个任务/参数写进文件后再读回来，必须能再生成**逐字节相同**的输入；手写输入里的另一种写法（`&XC_FUNCTIONAL/&PBE`、`DFTD3(BJ)`、`SCF_GUESS ATOMIC`、`MAX_SCF`、`OT MINIMIZER`、`ENSEMBLE NVE`、`THERMOSTAT NONE`…）也要读对；力场输入的 **`&SUBSYS/&CELL`** 与 `PERIODIC` 现在也读回来（`ABC` 三个数，或 `A`/`B`/`C` 矢量换算成长度+角度），因为力场文件路线的原子在 `COORD_FILE_NAME` 里、盒子**只**在这一处（少一个单元格预览就一直是"还差一个晶胞"）；读不出结构的文件（力场输入没有 `&COORD`）**「读参数」仍然可用**，读完预览直接生成；**`&EXT_RESTART` 只在 `RESTART_FILE_NAME` 真写了文件时才读**——CP2K 自己写的 restart 里那一行是空的，读回来会变成 `True` 这种鬼东西（现已堵死，见第 6 节的 bug 表） | 全部通过 |
| 坐标：缺了是错误、重复是警告 | `&SUBSYS` 里**既没有 `&COORD`（或它是空的）也没有坐标文件** → **error**（依据：`topology.F` 的 `coordinate_control` 试完所有来源后 `CPABORT("No atomic coordinates have been found!")`）；输入里有 `@INCLUDE`/`@FFTYPE` 时降为 info（坐标可能写在被包含的文件里，静态检查看不见）；**同时**给了 `&COORD` 和 `COORD_FILE_NAME`（两套坐标）→ **warning** + 说清哪一套生效（`&COORD` 覆盖文件）；**坐标文件与结构的原子数不一致** → 仍是 **warning**（生成物有坐标，只是要你核对是不是同一套体系）。这一条也顺手修了两个漏读：CP2K 自己的 FIST 回归输入写的是手册别名 **`COORD_FILE`**（语料里 46 个文件），以及坐标写成**表达式**的行（`Si 1/3 2/3 0.063`、`0*1/3`、`1./3.`，语料里 12 个文件）——原先都会被误判成"没有坐标" | 全部通过 |
| 生成期 error（与 warning 分开） | `BuildResult` 现在有 `errors`（生成器**确定** CP2K 会中止的：力场路线既没有坐标文件也没有结构 → 输入里一个原子都没有；力场路线缺 `PARM_FILE_NAME`/`CONN_FILE_NAME`/`&CELL` → `force_fields_input.F`、`cp_files.F`、`cell_methods.F` 三处硬中止）。GUI 里它是**红色「错误」行**（`build-error`）、算进摘要的错误数；CLI 打 `error:` 到 stderr（`--quiet` 也打）并**返回码 1**（`--no-validate` 也一样）。同时不再写空的 `&COORD`：空块 = 0 个原子，比不写更误导 | 全部通过 |
| 一份事实只说一次 | 生成器和校验器都能看见的发现（缺 `PARM_FILE_NAME`/`CONN_FILE_NAME`/`&CELL`）现在由生成器报 error，并把它覆盖的校验器 code 记进 `BuildResult.covered_codes`；GUI 的 `without_covered()` 和 CLI 的 `report()` 据此把校验器那份丢掉——报告里不会再出现"同一条事实、两个等级"。自测断言：合并后的报告里**没有任何 code 同时出现在两个等级上**，且 AMBER 缺文件的三条都在 `errors` 里、`warnings` 里没有旧英文句子的残留 | 全部通过 |
| 报告语言 | 校验器 85 条消息 + 39 条建议、生成器 22 条生成期提醒全部中文（CP2K 原话照引英文）；`summarise()` 收尾行也是中文。自测把 CP2K 语料过一遍校验器，**每条消息和每条建议都必须含中文**（1500 个输入 / 6043 条诊断，0 条纯英文），语料里 0 个 error | 全部通过 |
| PLUMED 续行写法 | 一份手写的多行 `plumed.dat`（`cv: COORDINATION ...` + 一行一个关键字 + `...` 收尾，手册 "Continuation lines" 的写法）：读回 6 个动作、`GROUPA/GROUPB/SWITCH/NLIST/NL_CUTOFF/NL_STRIDE` 一个不丢、`ARG/SIGMA/HEIGHT/PACE/TEMP/BIASFACTOR/FILE` 全进控件、只有 `FLUSH STRIDE=100` 落进自由行框、0 条 notes；回写的草稿里没有单独的 `...`。另外钉住三种坏写法：没写收尾的续行、多出来的 `...`、收尾的 `... 动作名` 写错（PLUMED 会报错）各有点名；`SWITCH={RATIONAL R_0=… NN=… MM=…}` 不再被误报「没写 R_0」，而真的没给 `R_0` 时照样提醒 | 全部通过 |
| 每个 CV 自己的 σ | 读 `SIGMA=0.15,0.5` → 落到两个 CV 上、回写不变；`SIGMA=0.15,0.15` → 读成默认值 0.15（面板显示 0.15、表里写「默认」、回写仍是 `SIGMA=0.15,0.15`）；`SIGMA=0.15` 配 2 个 ARG → 按 PLUMED 的原话点名并写成 `0.15,0.15`；一个 CV 一个 σ（合法）不吭声。界面上：CV 表第五列显示 `['0.15','0.5']`，提示行写明「上面这一格现在用不上」，改它文件不变；换成 `0.15,0.15` 后改它就真的改成 `SIGMA=0.9,0.9`（不再是空操作）；PLUMED 路线 + 21 行 CV + 墙时左栏仍然够宽（表列宽 760 也不撑面板） | 全部通过 |
| 摘要计数（表里有几行就数几行） | 一条 `build-warning` → 「0 错误 / 1 警告 · 其中 1 项来自生成器」（不再写 0 警告）；`build-error` 算进错误数（实测那一屏是「5 错误 / 0 警告」）；一行都没有 → 「0 错误 / 0 警告 — 没有问题」。CLI 收尾行把 `new` 自己打的 `warning:` 行算进去（实测 3 行 warning → 「3 个警告」，退出码 1 的那次也不会再写「0 个错误」）。`summarise(..., extra_errors=2, extra_warnings=1)` 的三种组合都有断言 | 全部通过 |
| 温度程序默认折叠 | MD 页上的「温度程序（多段升温/降温）」是 `CollapsibleBox`、默认收起、展开后内容可见、状态记进 `QSettings`；收起/展开不改变生成的输入；展开时左栏宽度仍够（视口 701 ≥ 内容最小 669，没有横向滚动条）；顺带量了高度：1120 → 757 px | 全部通过 |
| 「提示」级别的措辞 | 三个同族提示（`keyword-alias` / `keyword-underscore-dash` / `legacy-keyword`）改成**中文 + 明确说"不用改"**，并在「建议」里给出下一步（手册里的关键字名、`cp2kbuild keyword <名字>`）。起因是用户反馈：工具自己生成的 `&TOPOLOGY` 写的是 `CONNECTIVITY`（CP2K 回归测试里 842 次 vs `CONN_FILE_FORMAT` 33 次，短写法才是常见写法），于是每次校验自己的输出都会冒出两行英文提示，看着像出了问题 | 全部通过 |
| 行尾策略 | `new`/`scan`/`*_run.sh`/`*_analyse.sh`/`.ssv`/GUI 保存的文件一律 LF（shell 脚本在 Linux 上必须 LF，CRLF 的 shebang 会直接 `not found`） | 全部 LF |
| 截断能脚本（真跑） | 用真 `sh`：过 `sh -n`；`cutoff_inputs.sh` 生成每个参数点目录且值换对；假 `mpirun`/`cp2k` 桩跑 `cutoff_run.sh`；`cutoff_analyse.sh` 写出能量随 CUTOFF 变化的 `cutoff_data.ssv`（含每档网格点数、表头保持教程格式）；第 2 步填好 CUTOFF 后一路跑通、没填时拒绝运行；**并逐个目录解析生成的输入，断言第 2 步扫的是 REL_CUTOFF、CUTOFF 保持固定、且没有活着的 `&EXT_RESTART`**（前一条以前没查，漏掉了两行 `sed` 写反的 bug） | 全部通过 |
| 界面帮助 | 193 个「?」都有实质内容、每条帮助都挂到了控件上、帮助里引用的手册位置都真实存在（否则点击会 404） | 全部通过 |
| SCF 路线的联动界面 | OT/对角化两块按实际路线显示或隐藏；`ADDED_MOS`、`&MIXING`、`ENERGY_GAP`、`SAFE_DIIS`、`STEPSIZE` 的灰显条件逐条断言（换预条件子/最小化器/线搜索就跟着变）；下拉框里的取值逐个对照手册枚举，多一个就报错 | 全部通过 |
| Poisson × 周期性 | 11 组「求解器 × 周期性」的判定（`PERIODIC`/`MT`/`MULTIPOLE`/`ANALYTIC`/`WAVELET` 各自的适用范围，依据手册里每个取值的说明）；界面下拉框在每种周期性下都只提供允许的求解器（4 种周期性逐个断言）；不写求解器时不管（CP2K 默认 PERIODIC，官方语料里 238 个输入就这么写） | 全部通过 |
| 中文对照层 | 112 条中文说明指向的手册位置全部真实存在；6 组中文查询都能命中预期关键字；术语能从英文原文中抽出 | 全部通过 |
| 中文层核对脚本 | `tools/check_cn_glossary.py`（独立于 GUI 也能跑） | 全部通过 |
| 跨版本适配 | 2026.2 把 PDOS 搬进 `&DOS`、移除 `&QUIP`、改 SMEAR 默认值——生成端与校验端都跟着数据库版本走，两版各自的输出用各自的库都能校验通过 | 全部通过 |
| 打包后的 exe | CLI：跑遍所有子命令；GUI：`--selftest` 走真实启动路径，把 8 个任务逐一生成，再把「元动力学」页配一套井温元动力学（2 个 CV + 一张墙）并检查写出的 `&COLVAR`/`&METADYN` | 退出码 0，`problems` 为空、行数与源码运行完全相同 |

那 3 个文件是：

- `tests/Fist/regtest-11/H2O-32_NVT_NOSE_gen_noCNS0.inp` 里的 `&MD / DESCRIBE`——`DESCRIBE` 在 CP2K 全部源码和手册里都不存在（2025.1 和 2026.2 都一样）。
- `tests/MM/regtest-quip/Quip_Cu_LJ.inp` 里的 `&QUIP`——2026.2 的 [changelog](https://github.com/cp2k/cp2k/pull/4616) 明确写了 "Remove QUIP"，手册里 NONBONDED 下也只剩 `NEQUIP`。这个测试文件属于没跟着更新的那种。
- `tests/QS/regtest-gpw-1/H2-inpp.inp` 里的 `&SUBSYS / XXXXXXX`——这是 CP2K 自己用来测"坏输入要报错"的文件，故意写的乱码关键字。

两个我都**故意保留报错**，因为把查不到来源的名字加白名单会让校验器失去意义。

### 已知限制

- `validate` 不执行 CP2K 的预处理器（`@IF` / `@SET` / `@INCLUDE`），只报告它们的存在；带变量的输入请先让 CP2K 或 `cp2k-input-tools` 展开。CP2K 自己生成的输入里那种 `TIMECON \` + `@INCLUDE file.inc` 写法会被正确断句，但那个值校验器看不到。
- 校验是"静态"的：不做 SCF 收敛性、基组完备性、k 点收敛性的数值判断。网格那类要靠 `scan` 生成官方脚本、在服务器上跑完、自己看 `*_data.ssv`；SCF/基组/k 点的收敛同样得你自己看输出。
- 截断能测试现在**只生成脚本**：读 `.out`、算差值、判断取哪一档都留给你（`cutoff_analyse.sh` 会把能量和每档网格点数汇总成 `.ssv`）。要收敛力或应力，还得自己比较输出里的 `MAXIMUM ATOMIC FORCE`——官方教程的判据本来就是能量。
- `scan` 只会替你改 `RUN_TYPE`（一律 `ENERGY`）和那两个占位符，别的什么都不动。像 `@SET BAND_STRUCTURE` 这种"顺手把能带也算一遍"的设置，模板里写的是什么就是什么——每档都白算一遍能带的时候，自己在那份输入里关掉它（不做预处理器展开是有意的：工具不去猜 `@SET`/`@IF` 展开后的样子）。
- 升温/降温和 `scan` 是两件事：`scan` 会把 `&EXT_RESTART` 整段注释掉（截断能扫描每档必须独立），所以**接力升降温要用 `new`**。
- CP2K 的退火只有"每步乘一个因子"这一种形状，所以工具的斜坡是**几何（指数）**的，不是严格线性。要严格线性就分段接力（每段一个固定 `TEMPERATURE` + `RESTART_TEMPERATURE_ANNEALING T`）；工具的界面只做单段，还不替你按段拆。
- 生成器给出的是**合理的起点**，不是收敛过的参数。CUTOFF、k 点网格、EPS_SCF 请务必自己收敛。
- 能带路径按晶胞几何判断 Bravais 类型（`detect_lattice`）。如果你的原胞不是标准取法，请用 `--lattice` 指定，或直接给分数坐标 `--kpath "0,0,0;0.5,0,0"`。
- 手册数据库默认用最新的 `manual_db_2026_2.json`（`cp2k-2026_2-branch` 手册 + 2026.2 源码交叉核对）；要按 2025.1 校验就 `set CP2KBUILD_DB=...\manual_db_2025_1.json`，或按第 3 节重建别的版本。
- **没有在真实 CP2K 上跑过一次完整计算**：这台机器上只有 CP2K 源码，没有 `cp2k.pscp`/`cp2k.psmp` 可执行文件。所以"文件写得对、能过校验、和官方输入写法一致"是验证过的，"算出来的数对"没有验证过。
- GUI 的 `freq` 模板只给红外强度；两版手册里都没有 `&RAMAN` 段（只有 `&LINRES/&POLAR/DO_RAMAN`），拉曼要另外算。
- 界面外观我没法亲眼确认（我用的是纯文本模型，看不了图片）：`packaging/shots/` 里的截图是 offscreen 渲染的，中文可能因缺字体而空白，**请以真实窗口为准**，不满意的地方告诉我怎么改。
- 打包用的是 `D:\miniconda` 里的 Python 3.12；PyInstaller 会警告有几个 conda 的 DLL（`ffi.dll`、`libcrypto-3-x64.dll` 等）没找到。实测两个 exe 都正常，但如果换一台机器出现异常，用官方 CPython 重新打包即可。
