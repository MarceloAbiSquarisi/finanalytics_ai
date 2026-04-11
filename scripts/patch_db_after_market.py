"""
patch_db_after_market.py
------------------------
Fix: com ProactorEventLoop, DB/Redis (IOCP) compete com market data da DLL.
Mover init de DB/Redis para APÓS market_connected resolve a contenda de IOCP.

Também corrige event loop policy: ProactorEventLoop funciona, SelectorEventLoop
não permite routing (t=1 r=4 nunca chega). Comentário estava invertido.
"""
from pathlib import Path
import sys
import re

TARGET = Path("src/finanalytics_ai/workers/profit_market_worker.py")
if not TARGET.exists():
    print(f"ERRO: {TARGET} não encontrado.")
    sys.exit(1)

content = TARGET.read_text(encoding="utf-8")
original = content

# Fix 1: corrige event loop policy — remove o "pass" e restaura ProactorEventLoop
# (comentário estava invertido: Proactor funciona, Selector não)
OLD_LOOP = '''    if sys.platform == "win32":
        pass  # TEST: ProactorEventLoop (default Windows) para comparar com diag'''
NEW_LOOP = '''    if sys.platform == "win32":
        # ProactorEventLoop (default Windows) funciona com a DLL.
        # SelectorEventLoop impede routing (t=1 r=4 nunca chega) — NAO usar.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())'''

if OLD_LOOP in content:
    content = content.replace(OLD_LOOP, NEW_LOOP, 1)
    print("[ok] Event loop policy corrigida: WindowsProactorEventLoopPolicy")
else:
    # Talvez já esteja como ProactorEventLoop
    if "WindowsProactorEventLoopPolicy" in content:
        print("[--] ProactorEventLoopPolicy já configurada")
    else:
        print("[!!] Padrão de event loop não encontrado — verificar manualmente")

# Fix 2: aguarda market_connected ANTES de iniciar DB/Redis
OLD_DB = '''    # Inicializa DB/Redis APOS market connected — sem bloquear callbacks
    session_factory = get_session_factory()'''

NEW_DB = '''    # Aguarda market_connected antes de iniciar DB/Redis.
    # ProactorEventLoop usa IOCP — DB/Redis competem com market data da DLL.
    # Iniciar DB/Redis antes impede t=2 r=4 de chegar.
    for _dbi in range(60):  # max 30s
        if profit_client.state.market_connected:
            log.info("profit_market_worker.market_connected_before_db")
            break
        await asyncio.sleep(0.5)
    else:
        log.warning("profit_market_worker.proceeding_without_market_connected")

    # Inicializa DB/Redis APOS market connected — sem competir com IOCP da DLL
    session_factory = get_session_factory()'''

if OLD_DB in content:
    content = content.replace(OLD_DB, NEW_DB, 1)
    print("[ok] DB/Redis movido para após market_connected")
else:
    print("[!!] Padrão DB init não encontrado")
    for i, line in enumerate(content.splitlines(), 1):
        if "get_session_factory" in line:
            print(f"  L{i}: {line}")

if content == original:
    print("ERRO: nenhuma alteração feita")
    sys.exit(1)

TARGET.write_text(content, encoding="utf-8")
print(f"[ok] {TARGET} atualizado")

final = TARGET.read_text(encoding="utf-8")
checks = [
    ("ProactorEventLoopPolicy configurada",  "WindowsProactorEventLoopPolicy" in final),
    ("wait market_connected antes DB",       "_dbi in range(60)" in final),
    ("get_session_factory ainda presente",   "get_session_factory" in final),
]
for label, ok in checks:
    print(f"  {'[ok]' if ok else '[!!]'} {label}")
