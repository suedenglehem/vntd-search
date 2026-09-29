#Requires -Version 5.1
<#
.SYNOPSIS
    Fire the Vinted product JSON editor (Gradio UI) from the terminal.

.DESCRIPTION
    Same venv/requirements handling as run.ps1, then runs
    work\product_editor.py IN THE FOREGROUND so its log shows up right here.
    The editor reads the LLM endpoint from config.json, exactly like the
    pipeline, and opens http://127.0.0.1:7860 (see the README's "Product
    editor" section). If 7860 is already taken by a running editor, the
    next free port (7861, 7862, ...) is used automatically.

    Any extra arguments are passed straight to product_editor.py, e.g.:
        .\editor.ps1 --port 8000
        .\editor.ps1 --no-browser
        .\editor.ps1 --name rtx3090 --desc "..." --save   # headless

.EXAMPLE
    .\editor.ps1
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$PassThru
)

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$work    = Join-Path $root 'work'
$venv    = Join-Path $root '.venv'
$pyExe   = Join-Path $venv 'Scripts\python.exe'
$cfgFile = Join-Path $root 'config.json'

function Write-Step([string]$msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn([string]$msg)  { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Bad([string]$msg)   { Write-Host "    $msg" -ForegroundColor Red }

# ------------------------------------------------------------ 1. venv ----
Write-Step 'Python environment'
$python = $null
foreach ($cand in 'python', 'py') {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) { Write-Bad 'python not found on PATH'; exit 1 }

if (-not (Test-Path $pyExe)) {
    Write-Host "    creating venv at $venv ..."
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Bad 'venv creation failed'; exit 1 }
    & $pyExe -m pip install --quiet --upgrade pip
    Write-Ok "venv created with $(& $pyExe --version 2>&1)"
} else {
    Write-Ok "venv present: $(& $pyExe --version 2>&1)"
}

$reqHash = (Get-FileHash (Join-Path $root 'requirements.txt') -Algorithm SHA256).Hash
$hashFile = Join-Path $venv '.req.sha256'
if ((-not (Test-Path $hashFile)) -or ((Get-Content $hashFile -Raw).Trim() -ne $reqHash)) {
    Write-Host '    installing requirements ...'
    & $pyExe -m pip install --quiet -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Bad 'requirements install failed'; exit 1 }
    Set-Content $hashFile $reqHash -Encoding ASCII
    Write-Ok 'requirements installed'
} else {
    Write-Ok 'requirements already satisfied'
}

# ------------------------------------------------------- 2. LLM check ----
# Warn-only: the editor still opens without the server (Load/Save/List work);
# the model dropdown shows its own warning and Generate will fail with a
# friendly status line.
Write-Step 'LLM server check'
try {
    $cfg = Get-Content $cfgFile -Raw | ConvertFrom-Json
    $llmUrl = [string]$cfg.llm_url
    if (-not $llmUrl) { throw 'config.json: llm_url is empty' }
    $llmUrl = $llmUrl.TrimEnd('/')
    $headers = @{}
    $apiKey = [string]$cfg.api_key
    if ($apiKey) { $headers['Authorization'] = "Bearer $apiKey" }
    $resp = Invoke-WebRequest -Uri "$llmUrl/v1/models" -Headers $headers -TimeoutSec 10 -UseBasicParsing
    Write-Ok "$llmUrl reachable"
} catch {
    Write-Warn "LLM server not reachable ($($_.Exception.Message)) - editor still opens; Generate needs it"
}

# ------------------------------------------------------------- 3. run ----
# Foreground, and NOT under $ErrorActionPreference='Stop': with 'Stop' a
# native command's stderr (Gradio writes its startup lines there) becomes a
# terminating PowerShell error and kills the run. Merge 2>&1 so the log
# reaches the terminal as-is; Ctrl+C here stops the editor.

# An editor may already be running (leftover from a previous session) and
# hold 7860; step up until a free port is found, unless the user gave --port.
function Get-FirstFreePort([int]$start, [int]$count) {
    for ($p = $start; $p -lt $start + $count; $p++) {
        if (-not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) { return $p }
    }
    return $null
}
$port = 7860
if (-not ($PassThru -contains '--port')) {
    $free = Get-FirstFreePort 7860 20
    if ($null -eq $free) {
        Write-Bad 'no free port in 7860-7879; pass -Port via .\editor.ps1 --port <n>'
        exit 1
    }
    if ($free -ne 7860) {
        Write-Warn "port 7860 is busy (editor already running?) - using $free instead"
        $port = $free
        $PassThru += @('--port', [string]$port)
    }
}

Write-Step 'Product editor'
Write-Host "    opening http://127.0.0.1:$port (Ctrl+C here stops the editor)" -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'
& $pyExe (Join-Path $work 'product_editor.py') @PassThru 2>&1 | ForEach-Object { Write-Host $_ }
exit $LASTEXITCODE
