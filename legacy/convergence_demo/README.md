# 合成的 CUTOFF 收敛演示

`.inp` 是 `cp2kbuild scan` **真实生成**的（可以直接当模板用），
`.inp`/`run.sh`/`run.ps1` 都是工具的原样输出。

`.out` **不是真实计算结果**：它们由 `tools/make_demo_scan.py` 按 CP2K 的
输出格式手写（列位置照抄源码 `force_env_methods.F` 的 `FMT`，网格那几行
照抄真实输出），能量随 cutoff 变大而收敛，纯粹用来演示分析命令：

```bat
python -m cp2kbuild converge examples\convergence_demo
python -m cp2kbuild converge examples\convergence_demo --ssv examples\convergence_demo
```

真实用法是：先用 `cp2kbuild scan` 生成输入，在集群上跑完，
再对存放输出的目录执行 `cp2kbuild converge <目录>`。
