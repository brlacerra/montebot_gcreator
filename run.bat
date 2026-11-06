@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [MonteBot] Criando ambiente virtual .venv...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Nao foi possivel criar o ambiente virtual .venv. Verifique a instalacao do Python.
        pause
        exit /b 1
    )
    echo [MonteBot] Instalando dependencias do requirements.txt...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" -c "import vpype, svglib" 2>nul
if errorlevel 1 (
    echo [MonteBot] Sincronizando dependencias em .venv...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo [MonteBot] Iniciando MonteBot GCreator...
".venv\Scripts\python.exe" app.py

