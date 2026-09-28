#Requires -Version 5.1
<#
.SYNOPSIS
    Vinted product finder - one-command pipeline runner (multi-product).

.DESCRIPTION
    A "product" is one search profile defined in products\<name>\<name>.json
    (brands, price range, age window, LLM prompts). Output goes to
    products\<name>\vinted\<name>_DD.MM.YY.html (dated, french format); the
    previous runs' pages are kept per the product's keep_days (default 2).

    The script:
      1. creates a virtualenv (.venv\) and installs requirements.txt if needed
      2. verifies the LLM server from config.json is reachable and the model loaded
      3. backs up the product's previous HTML output
      4. runs the pipeline steps:
           1 fetch_all.py        fetch + parse brand search pages   (no LLM)
           2 filter_classify.py  price/age filter + title classify  (text LLM)
           3 vision_verify.py    re-classify kept items on photo    (vision LLM)
           4 build_html.py       generate <name>\<name>.html        (no LLM)

    Steps 1-3 are resumable: each keeps checkpoints in work\data\<name>\, and a
    per-product run-state (work\data\<name>\.run_state.json) records which steps
    finished. Re-running after a failure skips finished steps and picks the rest
    up where they left off (with a message).

.PARAMETER Product
    Product name (a file in products\). Default: bois.

.PARAMETER All
    Run every product in products\ in sequence.

.PARAMETER Refetch
    Force a fresh fetch of all brand pages (step 1) even if it already ran.

.PARAMETER Retitle
    Force a fresh title classification (step 2) - clears its checkpoint.

.PARAMETER Revision
    Force a fresh vision verification (step 3) - clears its checkpoint.

.PARAMETER Clean
    Clear this product's run state and checkpoints, then run everything from scratch.

.PARAMETER Rerun
    Full redo: implies -Refetch -Retitle -Revision (all three LLM/fetch steps are
    forced). Without -Product it reruns EVERY product in products\; with
    -Product <name> it does the full redo for that one product only.
#>
[CmdletBinding()]
param(
    [string]$Product = 'bois',
    [switch]$All,
    [switch]$Refetch,
    [switch]$Retitle,
    [switch]$Revision,
    [switch]$Clean,
    [switch]$Rerun
)

if ($Rerun) {
    $Refetch = $true; $Retitle = $true; $Revision = true
    if (-not $All -and -not $PSBoundParameters.ContainsKey('Product')) { $All = $true }
}

$ErrorActionPreference = 'Stop'
$root  = $PSScriptRoot
$work  = Join-Path $root 'work'
$venv  = Join-Path $root '.venv'
$pyExe = Join-Path $venv 'Scripts\python.exe'
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
Write-Step 'LLM server check'
if (-not (Test-Path $cfgFile)) { Write-Bad "config.json not found at $cfgFile"; exit 1 }
$cfg = Get-Content $cfgFile -Raw | ConvertFrom-Json
if (-not $cfg.llm_url) { Write-Bad 'config.json: llm_url is empty'; exit 1 }
$llmUrl = $cfg.llm_url.TrimEnd('/')
$apiKey = [string]$cfg.api_key
$model  = [string]$cfg.model

$env:VT_LLM_URL     = $llmUrl
$env:VT_LLM_API_KEY = $apiKey
$env:VT_LLM_MODEL   = $model

$headers = @{}
if ($apiKey) { $headers['Authorization'] = "Bearer $apiKey" }
try {
    $resp = Invoke-WebRequest -Uri "$llmUrl/v1/models" -Headers $headers -TimeoutSec 10 -UseBasicParsing
    $models = $resp.Content | ConvertFrom-Json
    $ids = @($models.data | ForEach-Object { $_.id })
    Write-Ok "$llmUrl reachable ($($ids.Count) model(s) loaded)"
    if ($ids -notcontains $model) {
        Write-Warn "configured model '$model' not in server list - check config.json"
    } else {
        Write-Ok "model '$model' available (needs vision for step 3)"
    }
} catch {
    Write-Bad "cannot reach LLM server at $llmUrl : $($_.Exception.Message)"
    Write-Host '    Steps 2-3 need it. Step 1 (fetch) could still run if you want.'
    exit 1
}

# ------------------------------------------------ 3. which product(s) ----
# a product is products\<name>\<name>.json
$ProductsDir = Join-Path $root 'products'
function Get-ProductNames {
    if (Test-Path $ProductsDir) {
        Get-ChildItem $ProductsDir -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName "$($_.Name).json") } |
            ForEach-Object { $_.Name } | Sort-Object
    } else { @() }
}
if ($All) {
    $products = Get-ProductNames
    if (-not $products) { Write-Bad 'no products in products\'; exit 1 }
    Write-Ok "products: $($products -join ', ')"
} else {
    if (-not (Test-Path (Join-Path $ProductsDir "$Product\$Product.json"))) {
        Write-Bad "product '$Product' not found in products\ (available: $((Get-ProductNames) -join ', '))"
        exit 1
    }
    $products = @($Product)
}

# --------------------------------------------------------- 4. pipeline ----
$failed = @()
foreach ($prod in $products) {
    $dataDir  = Join-Path $work "data\$prod"
    $outDir   = Join-Path (Join-Path $ProductsDir $prod) 'vinted'
    $stateFile = Join-Path $dataDir '.run_state.json'
    # dated output (french date): <name>_DD.MM.YY.html; build_html.py rotates
    # these, keeping at most the product's keep_days (default 2)
    $outHtml = Join-Path $outDir ("{0}_{1}.html" -f $prod, (Get-Date).ToString('dd.MM.yy'))
    New-Item -ItemType Directory -Force -Path $dataDir, $outDir | Out-Null

    Write-Step "PRODUCT: $prod"

    # ---- state ----
    $raw = $null
    if (Test-Path $stateFile) { $raw = Get-Content $stateFile -Raw | ConvertFrom-Json }
    $state = [pscustomobject]@{
        fetch  = [bool]$raw.fetch
        title  = [bool]$raw.title
        vision = [bool]$raw.vision
    }
    function SaveState([pscustomobject]$s) {
        $s | ConvertTo-Json -Compress | Set-Content $stateFile -Encoding UTF8
    }

    # ---- force switches ----
    if ($Clean) {
        foreach ($f in 'cls_partial.json', 'vision_partial.json') {
            Remove-Item -Force (Join-Path $dataDir $f) -ErrorAction SilentlyContinue
        }
        Remove-Item -Force $stateFile -ErrorAction SilentlyContinue
        $state = [pscustomobject]@{ fetch=$false; title=$false; vision=$false }
        Write-Warn 'Clean: all checkpoints cleared, running everything from scratch'
    }
    if ($Refetch) { $state.fetch  = $false }
    if ($Retitle) {
        $state.title = $false
        Remove-Item -Force (Join-Path $dataDir 'cls_partial.json') -ErrorAction SilentlyContinue
    }
    if ($Revision) {
        $state.vision = $false
        Remove-Item -Force (Join-Path $dataDir 'vision_partial.json') -ErrorAction SilentlyContinue
    }

    function Invoke-Step(
        [string]$name, [string]$script, [pscustomobject]$State, [string]$StateKey,
        [string]$PartialFile, [string]$PartialLabel)
    {
        if ($StateKey -and $State.$StateKey) {
            $swName = switch ($StateKey) { 'fetch' { 'Refetch' } 'title' { 'Retitle' } 'vision' { 'Revision' } }
            Write-Ok "$name - skipped (already completed in a previous run). Use -${swName} to force."
            return 0
        }
        if ($PartialFile -and (Test-Path $PartialFile)) {
            try {
                $done = (Get-Content $PartialFile -Raw | ConvertFrom-Json)
                $n = if ($done -is [System.Collections.IList]) { @($done).Count } else { @($done.PSObject.Properties).Count }
                if ($n -gt 0) { Write-Warn "$name - picking up previous work: $n $PartialLabel already done" }
            } catch { }
        }
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        # route the child's stdout through Write-Host so it does NOT pollute this
        # function's output stream (the caller captures the return value as $code)
        & $pyExe (Join-Path $work $script) $prod 2>&1 | ForEach-Object { Write-Host "      $_" }
        $code = $LASTEXITCODE
        $sw.Stop()
        if ($code -ne 0) {
            Write-Bad "$name FAILED (exit $code, $($sw.Elapsed.ToString('hh\:mm\:ss')))."
            if ($PartialFile) { Write-Host '    Checkpoint preserved - re-run this script to pick up where it stopped.' }
            return $code
        }
        if ($StateKey) {
            $State.$StateKey = $true
            SaveState $State
        }
        Write-Ok "$name completed in $($sw.Elapsed.ToString('hh\:mm\:ss'))"
        return 0
    }

    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()

    $code = Invoke-Step 'Step 1/4 fetch catalogs' 'fetch_all.py'      $state 'fetch'  $null $null
    if ($code -ne 0) { $failed += $prod; continue }
    $code = Invoke-Step 'Step 2/4 title classify' 'filter_classify.py' $state 'title'  'cls_partial.json' 'titles classified'
    if ($code -ne 0) { $failed += $prod; continue }
    $code = Invoke-Step 'Step 3/4 vision verify'  'vision_verify.py'  $state 'vision' 'vision_partial.json' 'photos verified'
    if ($code -ne 0) { $failed += $prod; continue }
    $code = Invoke-Step 'Step 4/4 build HTML'     'build_html.py'     $state $null    $null $null
    if ($code -ne 0) { $failed += $prod; continue }

    Write-Host ''
    Write-Host "  $prod : DONE in $($swTotal.Elapsed.ToString('hh\:mm\:ss'))" -ForegroundColor Cyan
    Write-Host "    output : $outHtml"
    if (Test-Path $outHtml) {
        $n = ([regex]::Matches((Get-Content $outHtml -Raw), '<a class="card"')).Count
        Write-Host "    cards  : $n listings"
    }
}

Write-Host ''
if ($failed.Count) {
    Write-Host "FAILED (re-run to resume): $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'ALL PRODUCTS DONE.' -ForegroundColor Green
