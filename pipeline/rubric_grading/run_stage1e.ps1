# Stage 1e: OpenAI graders, configured exactly as in openai/simple-evals, on the original Stage 1 rows (the plan's
# pre-specified 3,000-row test; pass mark = physicians' weighted average on those rows, 0.681):
#   gpt41      GPT-4.1 (2025-04-14), the paper's validated grader
#   gpt54_low  GPT-5.4 (2026-03-05), low reasoning: HealthBench Professional's default grader
# This OpenAI organisation can queue only 1.35M tokens per model, so batch_openai_seq.py sends each run in ~1M-token
# chunks, one at a time per model. Ends with every grader compared on the same rows.
# Run from a terminal:  powershell -ExecutionPolicy Bypass -File .\run_stage1e.ps1
# The OpenAI API key is typed at a hidden prompt and kept in this window's memory only. Safe to re-run.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$py = ".\.venv\Scripts\python.exe"
$graders = @("gpt41", "gpt54_low")

function Run([string[]]$a) {
    & $py @a | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "python $($a -join ' ') failed (exit $LASTEXITCODE)" }
}

if (-not $env:OPENAI_API_KEY) {
    $sec = Read-Host "Paste your OpenAI API key (input hidden), then press Enter" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$new = @($graders | Where-Object { -not (Test-Path "results\batches_meta_$_.json") -and
                                    -not (Test-Path "results\seqbatches_meta_$_.json") })
if ($new.Count -gt 0) {
    $total = 0.0
    foreach ($g in $new) {
        Write-Host "`n== ${g}: live check and cost estimate =="
        $o = & $py scripts\preflight.py --requests "requests\meta_$g.jsonl"
        $code = $LASTEXITCODE
        $o | ForEach-Object { Write-Host $_ }
        if ($code -ne 0) { throw "preflight failed for $g" }
        if (($o -join "`n") -match 'about \$([0-9.]+) through') { $total += [double]$Matches[1] }
    }
    Write-Host ("`nAbout `${0:N2} in total." -f $total)
    if ((Read-Host "Submit? (y/n)") -ne "y") { Write-Host "Not submitted."; exit 0 }
} else {
    Write-Host "`nBoth graders were already approved (about `$18.52 in total); continuing in chunks."
}

Write-Host "`n== Sending the batches in chunks (status every minute) =="
Run (@("scripts\batch_openai_seq.py", "--names") + ($graders | ForEach-Object { "meta_$_" }))

$scored = @()
foreach ($g in $graders) {
    $name = "meta_$g"
    Write-Host "`n== $name =="
    Run @("scripts\parse_results.py", "--name", $name)
    for ($k = 1; $k -le 3; $k++) {
        $nfail = @(Get-Content "results\failed_$name.txt" | Where-Object { $_.Trim() }).Count
        if ($nfail -eq 0) { break }
        if ($nfail -gt 150) { throw "$name has $nfail verdicts missing; stopping instead of retrying at full price." }
        Run @("scripts\retry_failed.py", "--requests", "requests\$name.jsonl", "--failed", "results\failed_$name.txt",
              "--out", "requests\${name}__retry$k.jsonl")
        Run @("scripts\run_sync.py", "--requests", "requests\${name}__retry$k.jsonl", "--name", $name)
        Run @("scripts\parse_results.py", "--name", $name)
    }
    Run @("scripts\meta_eval_paper.py", "--grader", $g)
    $scored += $name
}

Write-Host "`n== Same rows: OpenAI graders vs earlier Claude graders vs physicians (pass mark 0.681) =="
Run (@("scripts\compare_graders.py", "--rows", "requests\meta_sonnet.rows.jsonl", "--runs",
       "meta_sonnet", "meta_haiku", "meta_haiku_phys_think", "meta_haiku_firm_think") + $scored)
Write-Host "`nStage 1e finished. Tell Claude it is done."
