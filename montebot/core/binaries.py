"""
Utilitários multiplataforma para localização de executáveis no ambiente virtual (.venv) e no sistema.
"""

import os
import platform
import shutil
import sys
from typing import Optional


def find_executable(name: str) -> Optional[str]:
    """
    Procura um executável priorizando:
      1. Diretório Scripts/bin do interpretador Python ativo (.venv)
      2. Diretório local bin/ do projeto
      3. Variável PATH do sistema (shutil.which)
      4. Diretórios de usuário Windows (%APPDATA%\\local\\bin, %APPDATA%\\cabal\\bin) e Unix (~/.local/bin, etc.)
    """
    is_win = platform.system() == "Windows"
    exe_names = [f"{name}.exe", name] if is_win and not name.lower().endswith(".exe") else [name]

    # 1. Diretório do Python atual (.venv/Scripts ou .venv/bin)
    py_dir = os.path.dirname(os.path.abspath(sys.executable))
    search_dirs = [
        py_dir,
        os.path.join(py_dir, "Scripts"),
        os.path.join(py_dir, "bin"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bin")),
    ]

    # 2. Diretórios padrão no Windows e Unix
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        search_dirs.extend([
            os.path.join(appdata, "local", "bin"),
            os.path.join(appdata, "cabal", "bin"),
            os.path.join(appdata, "Python", "Scripts"),
        ])
    if localappdata:
        search_dirs.append(os.path.join(localappdata, "Programs", "Python", "Scripts"))

    search_dirs.extend([
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.cabal/bin"),
        os.path.expanduser("~/.stack/bin"),
        "/usr/local/bin",
    ])

    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        for exe_name in exe_names:
            candidate = os.path.join(directory, exe_name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    # 3. Fallback para PATH global
    path = shutil.which(name)
    if path:
        return path

    return None


def find_vpype() -> Optional[str]:
    return find_executable("vpype")


def find_juicy_gcode() -> Optional[str]:
    return find_executable("juicy-gcode")

