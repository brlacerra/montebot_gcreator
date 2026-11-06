"""
Ponto de entrada principal do MonteBot GCreator.
Garante execução dentro do ambiente virtual (.venv) do projeto quando disponível,
configura DPI Awareness no Windows e inicializa a interface gráfica modularizada.
"""

import ctypes
import os
import platform
import subprocess
import sys


def _ensure_venv_execution():
    """
    Se o usuário executar `python app.py` com um interpretador global que não possui
    as dependências instaladas, redireciona automaticamente para o interpretador do `.venv` local.
    """
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if platform.system() == "Windows":
        venv_python = os.path.join(project_dir, ".venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(project_dir, ".venv", "bin", "python")

    if not os.path.isfile(venv_python):
        return

    current_exe = os.path.normcase(os.path.abspath(sys.executable))
    target_exe = os.path.normcase(os.path.abspath(venv_python))

    if current_exe != target_exe:
        try:
            import vpype  # noqa: F401
        except ImportError:
            res = subprocess.run([venv_python, os.path.abspath(__file__)] + sys.argv[1:])
            sys.exit(res.returncode)


def _configure_dpi_awareness():
    """Ativa renderização nítida em telas High-DPI no Windows."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main():
    _ensure_venv_execution()
    _configure_dpi_awareness()

    from montebot.ui.main_window import MonteBotApp

    app = MonteBotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
