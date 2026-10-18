# 跑完 CUTOFF 扫描（5 个参数点）
# 用法:  pwsh cutoff_run.ps1
$cp2k = if ($env:CP2K) { $env:CP2K } else { "cp2k.psmp" }
$nproc = if ($env:NPROC_PER_CALC) { $env:NPROC_PER_CALC } else { 1 }
$watch = { param($p, $label) Write-Host "== $label"; $p.WaitForExit(); "  exit=$($p.ExitCode) ($label)" }

Write-Host "== cp2k-cutoff_300-rel_cutoff_60"
& $cp2k -i cp2k-cutoff_300-rel_cutoff_60.inp -o cp2k-cutoff_300-rel_cutoff_60.out
Write-Host "  exit=$LASTEXITCODE"
Write-Host "== cp2k-cutoff_400-rel_cutoff_60"
& $cp2k -i cp2k-cutoff_400-rel_cutoff_60.inp -o cp2k-cutoff_400-rel_cutoff_60.out
Write-Host "  exit=$LASTEXITCODE"
Write-Host "== cp2k-cutoff_500-rel_cutoff_60"
& $cp2k -i cp2k-cutoff_500-rel_cutoff_60.inp -o cp2k-cutoff_500-rel_cutoff_60.out
Write-Host "  exit=$LASTEXITCODE"
Write-Host "== cp2k-cutoff_600-rel_cutoff_60"
& $cp2k -i cp2k-cutoff_600-rel_cutoff_60.inp -o cp2k-cutoff_600-rel_cutoff_60.out
Write-Host "  exit=$LASTEXITCODE"
Write-Host "== cp2k-cutoff_700-rel_cutoff_60"
& $cp2k -i cp2k-cutoff_700-rel_cutoff_60.inp -o cp2k-cutoff_700-rel_cutoff_60.out
Write-Host "  exit=$LASTEXITCODE"
