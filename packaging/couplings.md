# 参数耦合表（反应式启用）

`cp2kbuild/couplings.py` 里的 `RULES` 是唯一来源：界面按它灰掉用不上的控件（原因写在该行的「?」上）、命令行按它对「给了却不会生效」的参数发警告、`tools/selftest.py` 逐条验证（每条都要能找到一个让它失效的输入、给出对应标志时会警告、默认值不误报）。

共 163 条（其中 9 条是**界面/库独有**：命令行已冻结，没有对应开关，表里写「—」）。每一行都附了依据（手册页面或 CP2K 源码文件）。

| 参数（Spec 字段） | 命令行 | 只在……时生效 | 依据 |
|---|---|---|---|
| `functional` | `--functional` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `dispersion` | `--dispersion` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `cutoff` | `--cutoff` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `rel_cutoff` | `--rel-cutoff` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ngrids` | `--ngrids` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `qs_method` | `--qs-method` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `eps_default` | `--eps-default` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `basis` | `--basis` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `potential` | `--potential` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `basis_target` | `--basis-target` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `basis_file` | `--basis-file` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `potential_file` | `--potential-file` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `data_path_style` | `--data-path-style` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `scf` | `--scf` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `eps_scf` | `--eps-scf` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `max_scf` | `--max-scf` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `outer_max_scf` | `--outer-max-scf` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `outer_eps_scf` | `--outer-eps-scf` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `smearing` | `--smearing` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `kpoint_scheme` | `--kpoint-scheme` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `poisson_solver` | `--poisson-solver` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_minimizer` | `--ot-minimizer` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_preconditioner` | `--ot-preconditioner` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_algorithm` | `--ot-algorithm` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_linesearch` | `--ot-linesearch` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_stepsize` | `--ot-stepsize` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_energy_gap` | `--ot-energy-gap` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_rotation` | `--ot-rotation` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `ot_safe_diis` | `--ot-safe-diis` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `outer_optimizer` | `--outer-optimizer` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `diag_algorithm` | `--diag-algorithm` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `level_shift` | `--level-shift` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `added_mos` | `--added-mos` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `smear_method` | `--smear-method` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `electronic_temperature` | `--electronic-temperature` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `mixing_method` | `--mixing-method` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `mixing_alpha` | `--mixing-alpha` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `mixing_nbuffer` | `--mixing-nbuffer` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `mixing_beta` | `--mixing-beta` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `scf_guess` | `--scf-guess` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `wfn_restart_file` | `--wfn-restart-file` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `wfn_restart_print` | `--no-wfn-print` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `wfn_restart_every` | `--wfn-restart-every` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `wfn_restart_add_last` | `--no-wfn-add-last` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `wfn_backup_copies` | `--wfn-backup-copies` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `charge` | `--charge` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `multiplicity` | `--multiplicity` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `uks` | `--uks` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `print_molden` | `--molden` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_file` | `--molden-file` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_nlumo` | `--molden-nlumo` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_unit` | `--molden-unit` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_gto_kind` | `--molden-gto-kind` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_write_pseudo` | `--molden-write-pseudo` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `molden_write_cell` | `--molden-write-cell` | METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS` 整段都不会写，这个设置不起作用 | `cp2kbuild/recipes.py:fist_force_eval_section` |
| `vacuum` | `--vacuum` | 从文件读力场的路线（AMBER/CHARMM/GROMOS）的坐标和晶胞来自坐标文件：再加真空层会把那个盒子换掉，反而和拓扑文件对不上。结构文件自带周期性晶胞时也会完全忽略真空层（晶体、以及 POSCAR/CIF 里已经带真空的表面板都属于这种）；要改那种体系的盒子请用「晶胞」 | `cp2kbuild/recipes.py:prepare_structure` |
| `mm_route` | `--mm-route` | 这是经典力场（METHOD FIST）的设置，Quickstep 路线下没有 `&MM` 段 | `FORCE_EVAL/MM/FORCEFIELD PARMTYPE` |
| `mm_parm_file` | `--mm-parm-file` | 只有从参数文件读力场的路线（AMBER / CHARMM / GROMOS）才写 `PARM_FILE_NAME`；`PARMTYPE OFF` 的路线把参数写在输入里 | `FORCE_EVAL/MM/FORCEFIELD PARM_FILE_NAME` |
| `mm_conn_file` | `--mm-conn-file` | 混合体系用 `CONNECTIVITY MOL_SET`：每个 `&MOLECULE` 指向自己的拓扑文件，顶层的 `CONN_FILE_NAME` 不会写 | `FORCE_EVAL/SUBSYS/TOPOLOGY CONN_FILE_NAME` |
| `mm_molecules` | `--mm-molecule` | 只有从文件读力场的路线才有拓扑文件要分配（参数写在输入里的路线用 `CONNECTIVITY GENERATE` 自己判断成键） | `FORCE_EVAL/SUBSYS/TOPOLOGY/MOL_SET/MOLECULE` |
| `mm_coord_file` | `--mm-coord-file` | 参数写在输入里的路线用 `&COORD` 内联坐标，不写 `COORD_FILE_NAME` | `FORCE_EVAL/SUBSYS/TOPOLOGY COORD_FILE_NAME` |
| `mm_coord_format` | `--mm-coord-format` | 没有坐标文件时 `COORDINATE` 也没有对象 | `FORCE_EVAL/SUBSYS/TOPOLOGY COORD_FILE_FORMAT` |
| `mm_ff_file` | `--mm-ff-file` | 只有「参数写在输入里」的路线会去读这个力场文件；其他路线的力场来自 `PARM_FILE_NAME` 指向的参数文件 | `cp2kbuild/recipes.py:_forcefield_from_file` |
| `mm_element_lj` | `--lj` | 只有参数写在输入里的路线会从 ε/σ 生成 `&LENNARD-JONES` 对势块 | `FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES` |
| `mm_lj` | `--lj` | 只有参数写在输入里的路线会写 `&LENNARD-JONES`；其他路线的 LJ 参数来自参数文件 | `FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES` |
| `mm_charges` | `--mm-charge` | 只有参数写在输入里的路线会写 `&CHARGE`；其他路线的电荷来自参数文件（PSF/prmtop） | `FORCE_EVAL/MM/FORCEFIELD/CHARGE` |
| `mm_vdw_scale14` | `--mm-vdw-scale14` | 1-4 缩放只有从参数文件读力场的路线才有：排除表来自拓扑文件 | `FORCE_EVAL/MM/FORCEFIELD VDW_SCALE14` |
| `mm_ei_scale14` | `--mm-ei-scale14` | 1-4 缩放只有从参数文件读力场的路线才有：排除表来自拓扑文件 | `FORCE_EVAL/MM/FORCEFIELD EI_SCALE14` |
| `mm_shift_cutoff` | `--no-shift-cutoff` | `SHIFT_CUTOFF` 影响的是非键相互作用的连续性，参数写在输入里的路线不需要它 | `FORCE_EVAL/MM/FORCEFIELD SHIFT_CUTOFF` |
| `mm_spline_emax` | `--mm-spline-emax` | `&SPLINE` 修的是参数文件给出的大半径势能尾部，参数写在输入里的路线自己定 RCUT | `FORCE_EVAL/MM/FORCEFIELD/SPLINE` |
| `mm_multiple_potential` | `--mm-multiple-potential` | 这是经典力场（METHOD FIST）的设置，Quickstep 路线下没有 `&MM` 段 | `FORCE_EVAL/MM/FORCEFIELD MULTIPLE_POTENTIAL` |
| `mm_ignore_missing_params` | `--mm-ignore-missing-params` | 这是经典力场（METHOD FIST）的设置，Quickstep 路线下没有 `&MM` 段 | `FORCE_EVAL/MM/FORCEFIELD IGNORE_MISSING_CRITICAL_PARAMS` |
| `mm_verlet_skin` | `--mm-verlet-skin` | 这是经典力场（METHOD FIST）的设置，Quickstep 路线下没有 `&MM` 段 | `FORCE_EVAL/MM/NEIGHBOR_LISTS VERLET_SKIN` |
| `mm_ewald` | `--mm-ewald` | 非周期晶胞里 `&EWALD` 只能是 `EWALD_TYPE NONE`（SPME/PME 要在周期网格上插值），那时没有类型可选 | `FORCE_EVAL/MM/POISSON/EWALD EWALD_TYPE` |
| `mm_ewald_gmax` | `--mm-ewald-gmax` | `GMAX` 是倒空间网格数，只在 EWALD/PME/SPME 下有对象 | `FORCE_EVAL/MM/POISSON/EWALD GMAX` |
| `mm_ewald_alpha` | `--mm-ewald-alpha` | `ALPHA` 是 Ewald 分裂参数，只在 EWALD/PME/SPME 下有对象 | `FORCE_EVAL/MM/POISSON/EWALD ALPHA` |
| `mm_o_spline` | `--mm-o-spline` | `O_SPLINE` 是 PME/SPME 的插值阶数 | `FORCE_EVAL/MM/POISSON/EWALD O_SPLINE` |
| `mm_rcut` | `--mm-rcut` | `RCUT` 是 Ewald 实空间截断，只在 EWALD/PME/SPME 下有对象；`EWALD_TYPE NONE` 用每个 `&LENNARD-JONES` 自己的 RCUT | `FORCE_EVAL/MM/POISSON/EWALD RCUT` |
| `ot_minimizer` | `--ot-minimizer` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT` | `FORCE_EVAL/DFT/SCF/OT` |
| `ot_preconditioner` | `--ot-preconditioner` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT` | `FORCE_EVAL/DFT/SCF/OT` |
| `ot_algorithm` | `--ot-algorithm` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT` | `FORCE_EVAL/DFT/SCF/OT` |
| `ot_linesearch` | `--ot-linesearch` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT` | `FORCE_EVAL/DFT/SCF/OT` |
| `ot_rotation` | `--ot-rotation` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT` | `FORCE_EVAL/DFT/SCF/OT` |
| `ot_stepsize` | `--ot-stepsize` | 只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT`，而且它是**线搜索**的初始步长——LINESEARCH 选 NONE/ADAPT 时没有线搜索 | `FORCE_EVAL/DFT/SCF/OT/STEPSIZE（手册：Initial stepsize used for the line search）` |
| `ot_energy_gap` | `--ot-energy-gap` | 手册写明只有 `FULL_ALL` 和 `FULL_SINGLE_INVERSE` 两个预条件子会读它（其余预条件子根本不看这个值） | `FORCE_EVAL/DFT/SCF/OT/ENERGY_GAP（…used in preconditioning, especially effective with the FULL_ALL preconditioner）` |
| `ot_safe_diis` | `--ot-safe-diis` | 它过滤的是 **DIIS 步**：MINIMIZER 不是 DIIS/BROYDEN 时没有 DIIS 步需要拒绝 | `FORCE_EVAL/DFT/SCF/OT/SAFE_DIIS（Reject DIIS steps …）` |
| `diag_algorithm` | `--diag-algorithm` | 只有对角化路线（`--scf DIAGONALIZATION`，或带 k 点 / 开 SMEAR 时「自动」判定成它）才会用到 | `FORCE_EVAL/DFT/SCF/DIAGONALIZATION` |
| `level_shift` | `--level-shift` | 只有对角化路线（`--scf DIAGONALIZATION`，或带 k 点 / 开 SMEAR 时「自动」判定成它）才会用到（源码里只有 `qs_scf_diagonalization.F` 用 level_shift） | `src/qs_scf_diagonalization.F:198` |
| `added_mos` | `--added-mos` | 只有对角化路线（`--scf DIAGONALIZATION`，或带 k 点 / 开 SMEAR 时「自动」判定成它）才会用到（`qs_environment.F` 里是 `IF (.NOT. scf_control%use_ot)` 才读 ADDED_MOS） | `src/qs_environment.F:2129` |
| `smear_method` | `--smear-method` | 没开 SMEAR（`&SMEAR` 段本身不会出现） | `FORCE_EVAL/DFT/SCF/SMEAR` |
| `electronic_temperature` | `--electronic-temperature` | 没开 SMEAR：占据数不需要展宽，温度无人可设 | `FORCE_EVAL/DFT/SCF/SMEAR/ELECTRONIC_TEMPERATURE` |
| `mixing_method` | `--mixing-method` | `&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，smearing 路线留给 CP2K 自己的默认混合 | `cp2kbuild/recipes.py:scf_section` |
| `mixing_alpha` | `--mixing-alpha` | `&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，smearing 路线留给 CP2K 自己的默认混合 | `cp2kbuild/recipes.py:scf_section` |
| `mixing_nbuffer` | `--mixing-nbuffer` | `&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，smearing 路线留给 CP2K 自己的默认混合 | `cp2kbuild/recipes.py:scf_section` |
| `mixing_beta` | `--mixing-beta` | `&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，smearing 路线留给 CP2K 自己的默认混合 | `cp2kbuild/recipes.py:scf_section` |
| `wfn_restart_file` | `--wfn-restart-file` | SCF_GUESS 不是 RESTART/HISTORY_RESTART：不需要指定波函数文件 | `FORCE_EVAL/DFT/WFN_RESTART_FILE_NAME` |
| `wfn_restart_every` | `--wfn-restart-every` | 关掉了「波函数文件」：`&SCF/&PRINT/&RESTART` 整段不会出现 | `FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH` |
| `wfn_restart_level` | `--wfn-restart-level` | 关掉了「波函数文件」：没有间隔要计数 | `FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH` |
| `wfn_restart_add_last` | `--no-wfn-add-last` | 关掉了「波函数文件」：没有东西可以在最后一步写 | `FORCE_EVAL/DFT/SCF/PRINT/RESTART/ADD_LAST` |
| `wfn_backup_copies` | `--wfn-backup-copies` | 关掉了「波函数文件」：没有备份可留 | `FORCE_EVAL/DFT/SCF/PRINT/RESTART/BACKUP_COPIES` |
| `restart_every` | `--restart-every` | 关掉了「重启文件」：`&MOTION/&PRINT/&RESTART` 整段不会出现 | `MOTION/PRINT/RESTART/EACH` |
| `restart_add_last` | `--no-restart-add-last` | 关掉了「重启文件」：没有东西可以在最后一步写 | `MOTION/PRINT/RESTART/ADD_LAST` |
| `restart_backups` | `--restart-backups` | 关掉了「重启文件」：没有备份可留 | `MOTION/PRINT/RESTART/BACKUP_COPIES` |
| `ext_restart_file` | `--restart` | 单点 / 频率 / 能带没有可续的迭代步，`&EXT_RESTART` 会被 CP2K 忽略（校验器也会给 `ext-restart-ignored`） | `FORCE_EVAL/DFT/… ; EXT_RESTART（continues a run with iteration steps）` |
| `ext_restart_default` | `--no-restart-default` | 没有给续算文件时，这个开关没有对象 | `EXT_RESTART/RESTART_DEFAULT` |
| `ext_restart_take` | `--restart-take` | 没有给续算文件时，没有东西可以「取」 | `EXT_RESTART/RESTART_<POS|VEL|CELL|…>（选了几项就写 `RESTART_DEFAULT F` + 这几项 `T`；见 cp2kbuild/recipes.py:ext_restart_section）` |
| `kpoint_grid` | `--kpoint-grid` | （随任务/取值变化，见实现） | `FORCE_EVAL/DFT/KPOINTS/SCHEME（+ FORCE_EVAL/DFT/SCF/&OT）` |
| `kpoint_scheme` | `--kpoint-scheme` | （随任务/取值变化，见实现） | `FORCE_EVAL/DFT/KPOINTS/SCHEME` |
| `kpoint_points` | `--kpoint-point` | （随任务/取值变化，见实现） | `FORCE_EVAL/DFT/KPOINTS/KPOINT` |
| `metadyn` | `--metadyn` | 元动力学靠 MD 的步数积累偏置：`&MOTION/&FREE_ENERGY/&METADYN` 和 `&SUBSYS/&COLVAR` 只在 `RUN_TYPE MD` 下会被 CP2K 读。要先用 MD 采样，或者把任务改成 MD | `MOTION/FREE_ENERGY/METADYN ; FORCE_EVAL/SUBSYS/COLVAR` |
| `fixed_atoms` | `--fixed-atoms` | 这个 RUN_TYPE 不会移动原子，固定原子没有任何作用（手写输入里出现 &CONSTRAINT 时校验器也会给 `constraint-ignored`） | `MOTION/CONSTRAINT/FIXED_ATOMS（只有 GEO_OPT/CELL_OPT/MD/BAND 有意义）` |
| `vacuum` | `--vacuum` | 结构文件自带周期性晶胞时**完全忽略**真空层（晶体、以及 POSCAR/CIF 里已经带真空的表面板都属于这种）；要改那种体系的盒子请用「晶胞」 | `cp2kbuild/recipes.py:prepare_structure` |
| `scaled_coords` | `--scaled-coords` | 没有晶胞：分数坐标需要晶胞才能换算成 Cartesian | `FORCE_EVAL/SUBSYS/COORD（SCALED 需要 &CELL）` |
| `poisson_solver` | `--poisson-solver` | 没有 &CELL 时不写 `&POISSON`（CP2K 会按 PERIODIC XYZ + 默认求解器处理） | `FORCE_EVAL/DFT/POISSON` |
| `h_to_d` | —（界面独有） | （随任务/取值变化，见实现） | `FORCE_EVAL/SUBSYS/KIND MASS` |
| `print_forces` | `--no-forces` | 只有单点能页面会写 `&FORCE_EVAL/&PRINT/&FORCES` | `cp2kbuild/recipes.py:force_eval_section` |
| `print_mulliken` | `--mulliken` | Mulliken 布居只在单点能任务、且体系非周期时才请求 | `cp2kbuild/recipes.py:force_eval_section` |
| `print_dipole` | `--no-dipole` | 偶极矩对完全周期体系没有意义，所以周期性体系不写 `&MOMENTS` | `cp2kbuild/recipes.py:force_eval_section` |
| `optimizer` | `--optimizer` | 只有几何优化 / 变胞优化有优化器 | `MOTION/GEO_OPT, MOTION/CELL_OPT` |
| `max_iter` | `--max-iter` | 只有几何优化 / 变胞优化有迭代步数 | `MOTION/GEO_OPT/MAX_ITER, MOTION/CELL_OPT/MAX_ITER` |
| `external_pressure` | `--external-pressure` | 外压只对变胞优化有意义 | `MOTION/CELL_OPT/EXTERNAL_PRESSURE` |
| `keep_angles` | `--keep-angles` | 固定晶胞角度只对变胞优化有意义 | `MOTION/CELL_OPT/KEEP_ANGLES` |
| `ensemble` | `--ensemble` | 系综是 MD 的设置 | `MOTION/MD/ENSEMBLE` |
| `thermostat` | `--thermostat` | （随任务/取值变化，见实现） | `cp2kbuild/recipes.py:md_section` |
| `temperature_annealing` | `--temperature-annealing` | 温度斜坡只对 LANGEVIN 生效：`TEMPERATURE_ANNEALING` 的那个乘法写在 `SUBROUTINE langevin` 里，手册也写着 "works only for the Langevin ensemble"；别的系综下它一声不响什么都不做 | `MOTION/MD/TEMPERATURE_ANNEALING` |
| `annealing` | `--annealing` | 速度缩放不是恒温器：CP2K 不允许它和粒子恒温器同时开（NVT/NPT 会直接中止："these two methods ... act one against the other"），LANGEVIN 也自带控温 | `MOTION/MD/ANNEALING` |
| `annealing_cell` | `--annealing-cell` | 晶胞速度的退火只在 NPE_I / NPE_F 里实现（`integrator.F`），别的系综写了也不生效 | `MOTION/MD/ANNEALING_CELL` |
| `langevin_gamma` | `--langevin-gamma` | `&LANGEVIN` 是 LANGEVIN 系综自己的段（CP2K 默认 GAMMA 0 = 没有摩擦也没有噪声，等于 NVE，所以工具会显式写一个值） | `MOTION/MD/LANGEVIN/GAMMA` |
| `restart_temperature_annealing` | `--restart-temperature-annealing` | 只有「接力」并且真的开了温度斜坡时才写：它让 CP2K 用 restart 里存下的目标温度接着斜坡（`input_cp2k_check.F` 会把 MOTION%MD%TEMPERATURE 覆盖掉） | `EXT_RESTART/RESTART_TEMPERATURE_ANNEALING` |
| `temperature` | `--temperature` | 温度是 MD 的设置；NVE 也读它——手册写明 TEMPERATURE 「用来给 init/pos restart 的速度定温」，CP2K 自己的 argon NVE 测试就写了它 | `MOTION/MD/TEMPERATURE` |
| `timestep` | `--timestep` | 时间步长是 MD 的设置 | `MOTION/MD/TIMESTEP` |
| `steps` | `--steps` | 步数是 MD 的设置 | `MOTION/MD/STEPS` |
| `pressure` | `--pressure` | 压力是 `&BAROSTAT` 的关键字，只有改变晶胞的系综才有它 | `MOTION/MD/BAROSTAT/PRESSURE` |
| `thermostat_timecon` | `--thermostat-timecon` | TIMECON 是 NOSE / CSVR 恒温器的关键字（GLE / AD_LANGEVIN / NONE 没有它） | `MOTION/MD/THERMOSTAT/NOSE/TIMECON` |
| `barostat_timecon` | `--barostat-timecon` | 它是 `&BAROSTAT` 的关键字，只有改变晶胞的系综才有 `&BAROSTAT` | `MOTION/MD/BAROSTAT/TIMECON` |
| `trajectory_stride` | `--trajectory-stride` | 只有会走多步的任务才写轨迹（单点 / 频率没有轨迹文件） | `MOTION/PRINT/TRAJECTORY/EACH` |
| `thermostat_region` | —（界面独有） | REGION 是 `&THERMOSTAT` 的关键字：NVE 不写恒温器，LANGEVIN 自带控温，恒温器选 NONE 时也没有它 | `MOTION/MD/THERMOSTAT/REGION` |
| `thermostat_temp_print` | —（界面独有） | `&THERMOSTAT/&PRINT/&TEMPERATURE` 打印的是恒温器各区域的温度：没有恒温器就没有可打印的区域 | `MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE` |
| `thermostat_temp_every` | —（界面独有） | 没勾「热浴温度」：`&THERMOSTAT/&PRINT/&TEMPERATURE` 整段不会写 | `MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE/EACH` |
| `md_temp_kind_print` | —（界面独有） | `&MD TEMP_KIND` + `&MD/&PRINT/&TEMP_KIND` 是 MD 的设置：只有会走多步的动力学才算得出温度 | `MOTION/MD/TEMP_KIND` |
| `md_temp_kind_every` | —（界面独有） | 没勾「每个原子种类的温度」：`&MD TEMP_KIND` 和它的打印键都不会写 | `MOTION/MD/PRINT/TEMP_KIND/EACH` |
| `md_energy_print` | —（界面独有） | `&MD/&PRINT/&ENERGY`（`-1.ener`，含 Temp[K] 列）是 MD 的打印键 | `MOTION/MD/PRINT/ENERGY` |
| `md_energy_every` | —（界面独有） | 没勾「能量文件」：`&ENERGY` 整段不会写（CP2K 仍会按 PRINT_LEVEL 写它自己那份 `-1.ener`，间隔是每步） | `MOTION/MD/PRINT/ENERGY/EACH` |
| `md_energy_file` | —（界面独有） | 没勾「能量文件」：`&ENERGY` 整段不会写，也就没有文件名可改 | `MOTION/MD/PRINT/ENERGY/FILENAME` |
| `intensities` | `--intensities` | 红外强度要算偶极导数，只对**非周期**体系请求（周期性体系 CP2K 不支持） | `FORCE_EVAL/DFT/…/VIBRATIONAL_ANALYSIS/INTENSITIES` |
| `thermochemistry` | `--thermochemistry` | 热化学量只在频率计算里 | `VIBRATIONAL_ANALYSIS/THERMOCHEMISTRY` |
| `molden_vib` | `--no-molden-vib` | `&MOLDEN_VIB` 是 `&VIBRATIONAL_ANALYSIS/&PRINT` 下的打印键：只有频率计算才有振动模式可写 | `VIBRATIONAL_ANALYSIS/PRINT/MOLDEN_VIB` |
| `lattice` | `--lattice` | 晶格类型只用于自动生成能带路径 | `FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE` |
| `kpath` | `--kpath` | k 点路径只用于能带计算 | `FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE` |
| `npoints` | `--npoints` | 路径点数只用于能带计算 | `FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE` |
| `pdos_nlumo` | `--pdos-nlumo` | NLUMO 只用于投影态密度 | `FORCE_EVAL/DFT/PRINT/DOS/PDOS` |
| `pdos_ldos` | `--pdos-ldos` | LDOS 列表只用于投影态密度 | `FORCE_EVAL/DFT/PRINT/DOS/PDOS` |
| `pdos_broaden` | `--pdos-broaden` | 展宽只用于投影态密度 | `FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN` |
| `pdos_broaden_type` | `--pdos-broaden-type` | 没有设展宽宽度 FWHM 时不会写 `&BROADEN`，也就没有展宽函数可选 | `FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN/TYPE` |
| `band_type` | `--band-type` | NEB 类型只用于过渡态搜索 | `MOTION/BAND/BAND_TYPE` |
| `k_spring` | `--k-spring` | 弹簧常数只用于 NEB | `MOTION/BAND/K_SPRING` |
| `replicas` | `--replicas` | 镜像只用于 NEB | `MOTION/BAND/REPLICA` |
| `molden_file` | `--molden-file` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |
| `molden_nlumo` | `--molden-nlumo` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |
| `molden_unit` | `--molden-unit` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |
| `molden_gto_kind` | `--molden-gto-kind` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |
| `molden_write_pseudo` | `--molden-write-pseudo` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |
| `molden_write_cell` | `--molden-write-cell` | 没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入 | `FORCE_EVAL/DFT/PRINT/MO_MOLDEN` |

## 说明

- 「用不上」不等于「写错了」：这些参数在别的上下文里很有用，只是当前这套输入不会把它们写进去（或者写了也被 CP2K 忽略）。
- 界面上每个被灰掉的控件，它那一行的「?」里会写出**当前**为什么用不上，以及依据在哪；换任务 / 换路线 / 换周期性时会实时重算。
- 命令行只对**你确实要求过**的参数发警告（`--temperature 500` 配单点会警告，`--no-intensities` 不会——明确关掉的东西不需要提醒）。
