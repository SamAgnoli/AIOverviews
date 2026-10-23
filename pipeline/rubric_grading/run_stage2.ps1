# Stage 2: grade the 3,474 AI Overviews against their HealthBench rubrics with GPT-4.1 (the grader that passed
# Stage 1e: MF1 0.707 vs physicians 0.681), configured exactly as in openai/simple-evals.
#   1. Pilot: 50 overviews (611 requests, ~$1), scored, with 10 verdicts printed for a by-hand check.
#   2. Asks before the full run: 41,180 requests (~$67), sent in ~1M-token chunks because this OpenAI organisation
#      can queue only 1.35M tokens per model. Takes several hours; keep the computer awake.
# Run from a terminal:  powershell -ExecutionPolicy Bypass -File .\run_stage2.ps1
# The OpenAI API key is typed at a hidden prompt and kept in this window's memory only. Safe to re-run: finished
# chunks are not resent, and an interrupted run resumes where it stopped.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$py = ".\.venv\Scripts\python.exe"

function Run([string[]]$a) {
    & $py @a | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "python $($a -join ' ') failed (exit $LASTEXITCODE)" }
}

function Grade([string]$name, [int]$maxRetry) {
    Run @("scripts\batch_openai_seq.py", "--names", $name)
    Run @("scripts\parse_results.py", "--name", $name)
    for ($k = 1; $k -le 3; $k++) {
        $nfail = @(Get-Content "results\failed_$name.txt" | Where-Object { $_.Trim() }).Count
        if ($nfail -eq 0) { break }
        if ($nfail -gt $maxRetry) { throw "$name has $nfail verdicts missing; stopping instead of retrying at full price." }
        Run @("scripts\make_requests.py", "--grader", "gpt41", "--only-cids", "results\failed_$name.txt",
              "--out", "requests\${name}__retry$k.jsonl")
        Run @("scripts\run_sync.py", "--requests", "requests\${name}__retry$k.jsonl", "--name", $name)
        Run @("scripts\parse_results.py", "--name", $name)
    }
    Run @("scripts\score.py", "--name", $name)
}

if (-not $env:OPENAI_API_KEY) {
    $sec = Read-Host "Paste your OpenAI API key (input hidden), then press Enter" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not (Test-Path "results\scores_pilot_gpt41.csv")) {
    Write-Host "`n== Pilot: 50 overviews (611 requests, about `$1) =="
    Grade "pilot_gpt41" 60
}
Write-Host "`n== Pilot summary =="
Get-Content "results\summary_pilot_gpt41.md" | Select-Object -First 25 | ForEach-Object { Write-Host $_ }
Write-Host "`n== 10 pilot verdicts to check by hand =="
Run @("scripts\show_verdicts.py", "--name", "pilot_gpt41", "--n", "10")

if (-not (Test-Path "results\seqbatches_gpt41_as_sent.json")) {
    $ok = Read-Host "`nRun the full set now: 41,180 requests, about `$67, several hours? (y/n)"
    if ($ok -ne "y") { Write-Host "Full run not started. Re-run this script when ready."; exit 0 }
}
Write-Host "`n== Full run: 3,474 overviews (status every minute) =="
Grade "gpt41_as_sent" 1000
Write-Host "`nStage 2 finished. Tell Claude it is done."
