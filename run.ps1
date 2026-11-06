$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[MonteBot] Criando ambiente virtual .venv..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { py -3.11 -m venv .venv } catch { python -m venv .venv }
    } else {
        python -m venv .venv
    }
    Write-Host "[MonteBot] Instalando dependencias..." -ForegroundColor Cyan
    & $VenvPython -m pip install -r requirements.txt
}

& $VenvPython -c "import vpype, svglib" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[MonteBot] Sincronizando pacotes do requirements.txt..." -ForegroundColor Cyan
    & $VenvPython -m pip install -r requirements.txt
}

Write-Host "[MonteBot] Iniciando MonteBot GCreator..." -ForegroundColor Green
& $VenvPython app.py

