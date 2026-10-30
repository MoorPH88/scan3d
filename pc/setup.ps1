# ScanStation setup for Windows (NVIDIA GPU).
# Installs Python 3.12 if needed, the Python libraries, and the CUDA build of COLMAP.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes downloads much faster in PowerShell 5
Set-Location $PSScriptRoot
$ColmapTag = '4.2.1'

function Say($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }

function Test-Python($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and ($v -eq '3.12' -or $v -eq '3.11'))
    } catch { return $false }
}

function Find-Python {
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('3.12', '3.11')) {
            try {
                $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $p) { $cands = @($p.Trim()) + $cands }
            } catch {}
        }
    }
    foreach ($c in $cands) { if (Test-Python $c) { return $c } }
    return $null
}

# ---------------------------------------------------------------- Python
Say "Checking Python"
$python = Find-Python
if (-not $python) {
    Say "Installing Python 3.12 (via winget)"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is not available. Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run setup.bat again."
    }
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $python = Find-Python
    if (-not $python) {
        throw "Python 3.12 still not found. Install it from https://www.python.org/downloads/ and run setup.bat again."
    }
}
Write-Host "Using $python"

# ---------------------------------------------------------------- venv + libraries
Say "Installing Python libraries (a few minutes the first time)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $python -m venv .venv }
$vpy = (Resolve-Path ".venv\Scripts\python.exe").Path
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Installing Python libraries failed (see messages above)." }

# ---------------------------------------------------------------- COLMAP (CUDA)
Say "Checking COLMAP (photogrammetry engine)"
$tools = Join-Path $PSScriptRoot 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
$existing = Get-ChildItem $tools -Recurse -Filter 'colmap.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Write-Host "COLMAP already installed: $($existing.FullName)"
} else {
    $asset = $null
    foreach ($url in @("https://api.github.com/repos/colmap/colmap/releases/tags/$ColmapTag",
                       "https://api.github.com/repos/colmap/colmap/releases/latest")) {
        try {
            $rel = Invoke-RestMethod -Uri $url -Headers @{ 'User-Agent' = 'ScanStation-setup' }
            $asset = $rel.assets | Where-Object { $_.name -match 'windows' -and $_.name -match 'cuda' -and $_.name -notmatch 'nocuda' } | Select-Object -First 1
            if ($asset) { Write-Host "Found COLMAP $($rel.tag_name): $($asset.name)"; break }
        } catch { Write-Host "  (could not read $url)" }
    }
    if (-not $asset) {
        throw "Could not find the COLMAP Windows CUDA download. Get 'colmap-x64-windows-cuda.zip' from https://github.com/colmap/colmap/releases , unzip it into the 'tools' folder here, and run setup.bat again."
    }
    $zip = Join-Path $tools $asset.name
    Write-Host "Downloading $([math]::Round($asset.size / 1MB)) MB ..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    $dest = Join-Path $tools 'colmap'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Write-Host "Unpacking ..."
    $unpacked = $false
    try { & tar -xf $zip -C $dest; $unpacked = ($LASTEXITCODE -eq 0) } catch { $unpacked = $false }
    if (-not $unpacked) { Expand-Archive -Path $zip -DestinationPath $dest -Force }
    Remove-Item $zip -Force
}

# ---------------------------------------------------------------- mats + check
Say "Writing printable mats"
& $vpy scanstation.py mat

Say "Checking everything"
& $vpy scanstation.py check

Write-Host ""
Write-Host "Setup finished. Print Scan3D-mat-A4.pdf at 100% (Actual size), then double-click start.bat." -ForegroundColor Green
