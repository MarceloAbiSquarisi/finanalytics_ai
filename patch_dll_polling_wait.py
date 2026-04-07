from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

ANCHOR1 = '        # state_cb minimo: apenas atualizacoes de estado, SEM logging nem I/O.\n        # I/O na ConnectorThread bloqueia entrega de conn_type=2 (market data).\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            if t == 0:\n                _state.login_connected = (r == 0)\n            elif t == 1:\n                _state.routing_connected = (r >= 4)\n            elif t == 2:\n                _state.market_connected = (r == 4)\n            elif t == 3:\n                _state.market_login_valid = (r == 0)\n            if _state.ready and _loop:\n                _loop.call_soon_threadsafe(_event.set)\n'
REPL1   = '        # state_cb ultra-minimo: APENAS atualizacoes de atributo.\n        # Nenhuma chamada threading — atribuicao de bool e atomica no CPython (GIL).\n        @_WFTYPE(None, _cint, _cint)\n        def _state_cb(t, r):\n            if t == 0:   _state.login_connected    = (r == 0)\n            elif t == 1: _state.routing_connected  = (r >= 4)\n            elif t == 2: _state.market_connected   = (r == 4)\n            elif t == 3: _state.market_login_valid = (r == 0)\n'
ANCHOR2 = '    async def wait_connected(self, timeout: float = 30.0) -> bool:\n        """Aguarda até a conexão estar pronta (Market Data + Login válido)."""\n        try:\n            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)\n            # Inicia consumer apenas apos conexao confirmada\n            if self._consumer_task is None:\n                self._consumer_task = asyncio.create_task(self._consume_loop())\n            return True\n        except asyncio.TimeoutError:\n            log.warning("profit_dll.connect_timeout", timeout=timeout, state=self._state)\n            return False\n'
REPL2   = '    async def wait_connected(self, timeout: float = 30.0) -> bool:\n        """Aguarda market_login_valid via polling — sem threading primitives no callback."""\n        steps = int(timeout * 2)\n        for _ in range(steps):\n            if self._state.market_login_valid:\n                if self._consumer_task is None:\n                    self._consumer_task = asyncio.create_task(self._consume_loop())\n                return True\n            await asyncio.sleep(0.5)\n        log.warning("profit_dll.connect_timeout", timeout=timeout, state=self._state)\n        return False\n'
SENTINEL = '# state_cb ultra-minimo: APENAS atualizacoes de atributo.'

def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()[:12]

def apply_patch(path, dry_run=False, check=False):
    if not path.exists():
        print(f"[ERROR] {path}", file=sys.stderr); return 2
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if SENTINEL in text:
        print("[OK] Patch ja aplicado."); return 0
    if ANCHOR1 not in text:
        print("[ERROR] Ancora 1 nao encontrada.", file=sys.stderr); return 3
    if ANCHOR2 not in text:
        print("[ERROR] Ancora 2 nao encontrada.", file=sys.stderr); return 3
    if check:
        print("[FAIL]", file=sys.stderr); return 1
    patched = text.replace(ANCHOR1, REPL1, 1).replace(ANCHOR2, REPL2, 1)
    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=path.name+" (original)",
            tofile=path.name+" (patched)", n=3))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferenca."); return 0
    bak = path.with_suffix(f".py.bak_{_sha(text)}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak}")
    out = patched.encode("utf-8")
    if crlf:
        out = out.replace(b"\n", b"\r\n")
    path.write_bytes(out)
    print(f"[PATCHED] {path}")
    return 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run, a.check))

if __name__ == "__main__":
    main()
