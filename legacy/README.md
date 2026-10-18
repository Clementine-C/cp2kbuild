# legacy/

These files are **archived, not part of cp2kbuild any more**. Nothing imports
them, the packaging step does not bundle them, and the self-test does not run
them - they are kept only so the old approach can be read again if it is ever
wanted.

What they were, and what replaced them:

| file | what it did | today |
|---|---|---|
| `analysis.py` | read CP2K `.out` files (energy, `count for grid`, atom counts), judged convergence, suggested CUTOFF/REL_CUTOFF, checked the two-step recipe, wrote `.ssv` tables | gone: the analysis now runs on the cluster. `*_analyse.sh` (from `cp2kbuild/cutoffkit.py`) writes `cutoff_data.ssv` / `rel_cutoff_data.ssv`, and the user reads those |
| `scanrun.py` | generated arbitrary sweeps: structure mode, `--from`, `--template`, `--vary` (cartesian products), flat/dirs layouts, the cheap grid-scan protocol | the two surviving template channels are "the generation tab's current parameters -> the official 7 files" and "existing input, verbatim -> the official 7 files", in `cp2kbuild/cutoffkit.py` (`source_from_spec` / `source_from_input`) |
| `scanscripts.py` | wrote the old `*_run.sh` / `*_analyse.sh` (with `FIXED_VALUE=`, `--set` handling, …) | `cutoffkit.py` writes CP2K's own `cutoff_inputs.sh` / `cutoff_run.sh` / `cutoff_analyse.sh` (+ the `rel_cutoff_*` trio) |
| `convergence_demo/` | the synthetic `.out`/`.inp` fixture the old analysis tests and the README transcript used | not needed: the generated scripts are tested end to end with a fake CP2K (`tools/selftest.py`, part 4) |

The decision (2026-09): the tool only produces `template.inp` + the six scripts
for the workflow of CP2K's manual page
[How to Converge the CUTOFF and REL_CUTOFF](https://manual.cp2k.org/trunk/methods/dft/cutoff.html);
everything after that - running the jobs and looking at the results - happens on
the server. Reading a `.ssv` file back would be ~50 lines if it is ever wanted,
which is why the archived `.out` parser (1000 lines) was not kept alive.
