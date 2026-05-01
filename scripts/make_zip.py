"""
make_zip.py
Cria ZIP limpo do projeto finanalytics_ai excluindo arquivos desnecessarios.
Uso: python make_zip.py
"""
from datetime import datetime
import os
import pathlib
import zipfile

ROOT = pathlib.Path(r"D:\Projetos\finanalytics_ai_fresh")

# Diretorios e padroes a EXCLUIR
EXCLUDE_DIRS = {
    ".venv", "__pycache__", ".git", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "node_modules", ".idea", ".vscode",
    "dist", "build", "*.egg-info",
}

EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd",
    ".log", ".tmp", ".bak",
    ".db", ".sqlite", ".sqlite3",
}

EXCLUDE_FILES = {
    ".DS_Store", "Thumbs.db", "desktop.ini",
}

# Arquivos/dirs grandes na raiz que nao sao codigo
EXCLUDE_ROOT_PATTERNS = {
    "*.zip", "*.tar.gz", "*.tar",
}

def should_exclude(path: pathlib.Path) -> bool:
    # Exclui por nome de diretorio
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
        if part.endswith(".egg-info"):
            return True
    # Exclui por extensao
    if path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return True
    # Exclui por nome de arquivo
    if path.name in EXCLUDE_FILES:
        return True
    # Exclui ZIPs na raiz
    if path.parent == ROOT and path.suffix in {".zip", ".tar", ".gz"}:
        return True
    return False

def make_zip():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    zip_name  = ROOT.parent / f"finanalytics_ai_{timestamp}.zip"

    total_files = 0
    total_size  = 0
    skipped     = 0

    print(f"Criando: {zip_name}")
    print(f"Origem:  {ROOT}")
    print()

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fpath in sorted(ROOT.rglob("*")):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(ROOT)
            if should_exclude(rel):
                skipped += 1
                continue
            size = fpath.stat().st_size
            zf.write(fpath, rel)
            total_files += 1
            total_size  += size
            if size > 1_000_000:  # mostra arquivos > 1MB
                print(f"  {rel}  ({size/1024/1024:.1f} MB)")

    zip_size = zip_name.stat().st_size
    print()
    print(f"Arquivos incluidos : {total_files}")
    print(f"Arquivos excluidos : {skipped}")
    print(f"Tamanho original   : {total_size/1024/1024:.1f} MB")
    print(f"Tamanho ZIP        : {zip_size/1024/1024:.1f} MB")
    print(f"ZIP criado em      : {zip_name}")

if __name__ == "__main__":
    make_zip()
