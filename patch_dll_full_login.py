"""patch_dll_full_login.py — troca DLLInitializeMarketLogin por DLLInitializeLogin."""
from __future__ import annotations
import argparse, hashlib, shutil, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\infrastructure\market_data\profit_dll\client.py"
)

# Ancora: bloco inserido pelo patch anterior (DLLInitializeMarketLogin)
ANCHOR = """
        # Inicializa a conexao via DLLInitializeMarketLogin.
        # Passamos None para todos exceto state_cb — callbacks incorretos
        # na init corrompem a ConnectorThread (manual Nelogica, sec 3.2).
        from ctypes import c_wchar_p as _wstr
        ret = self._dll.DLLInitializeMarketLogin(
            _wstr(self._activation_key),
            _wstr(self._username),
            _wstr(self._password),
            _state_cb,  # StateCallback
            None,       # NewTradeCallback (via SetTradeCallbackV2 acima)
            None,       # NewDailyCallback
            None,       # PriceBookCallback
            None,       # OfferBookCallback
            None,       # HistoryTradeCallback
            None,       # ProgressCallback
            None,       # TinyBookCallback
        )
        if ret != 0:
            raise RuntimeError(f"DLLInitializeMarketLogin falhou: {ret}")
        log.info("profit_dll.initialized", mode="market_login")
"""

REPLACEMENT = """
        # Inicializa via DLLInitializeLogin (login completo).
        # DLLInitializeMarketLogin exige assinatura API standalone.
        # DLLInitializeLogin ativa conn_type=1 (routing) que libera
        # conn_type=2 (market data) — igual ao exemplo oficial Delphi.
        from ctypes import c_wchar_p as _wstr
        ret = self._dll.DLLInitializeLogin(
            _wstr(self._activation_key),
            _wstr(self._username),
            _wstr(self._password),
            _state_cb,  # StateCallback
            None,       # HistoryCallback
            None,       # OrderChangeCallback
            None,       # AccountCallback
            None,       # TradeCallback (via SetTradeCallbackV2 acima)
            None,       # DailyCallback
            None,       # PriceBookCallback
            None,       # OfferBookCallback
            None,       # HistoryTradeCallback
            None,       # ProgressCallback
            None,       # TinyBookCallback
        )
        if ret != 0:
            raise RuntimeError(f"DLLInitializeLogin falhou: {ret}")
        log.info("profit_dll.initialized", mode="full_login")
"""

SENTINEL = "# Inicializa via DLLInitializeLogin (login completo)."


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def apply_patch(path: Path, dry_run: bool = False, check: bool = False) -> int:
    if not path.exists():
        print(f"[ERROR] Nao encontrado: {path}", file=sys.stderr); return 2
    original = path.read_text(encoding="utf-8")
    if SENTINEL in original:
        print("[OK] Patch ja aplicado."); return 0
    if ANCHOR not in original:
        print("[ERROR] Ancora nao encontrada. Execute patch_dll_init.py primeiro.", file=sys.stderr)
        return 3
    if check:
        print("[FAIL] Patch nao aplicado.", file=sys.stderr); return 1
    patched = original.replace(ANCHOR, REPLACEMENT, 1)
    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=path.name + " (original)",
            tofile=path.name + " (patched)", n=3))
        print("".join(diff) if diff else "[DRY-RUN] Sem diferenca."); return 0
    bak = path.with_suffix(f".py.bak_{_sha(original)}")
    shutil.copy2(path, bak)
    print(f"[BACKUP] {bak}")
    path.write_text(patched, encoding="utf-8")
    print(f"[PATCHED] {path}")
    print("\nSequencia esperada nos logs:")
    print("  profit_dll.initialized   mode=full_login")
    print("  dll_state.login          conn_type=0 result=0")
    print("  dll_state.other          conn_type=1  (routing)")
    print("  dll_state.market_data    state=MARKET_CONNECTED")
    print("  profit_dll.subscribed    (x8 tickers)\n")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run, a.check))


if __name__ == "__main__":
    main()
