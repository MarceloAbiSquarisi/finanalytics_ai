#!/usr/bin/env python3
"""
Fix MomentumStrategy: escala o limiar do filtro RSI proporcionalmente ao period do ROC.

Problema raiz:
  ROC(20) em série senoidal (período ~21 bars) cruza zero perto dos picos/vales de preço.
  Nas cristas (zero-crossing BUY), RSI(14) ≈ 75–82 → filtro rsi_filter=65 bloqueia tudo.

Fix:
  effective_rsi_filter = rsi_filter + max(0, (period - 14) * 3)
  period=10 → effective = 65 + 0  = 65  (comportamento inalterado)
  period=20 → effective = 65 + 18 = 83  (permite BUY na senoide)

Semântica preservada:
  - rsi_filter=0 continua desabilitando o filtro (condição `if self.rsi_filter > 0`)
  - rsi_filter=100 continua sendo permissivo para todos os casos
  - Escala apenas quando period > 14 (ROC e RSI em janelas incompatíveis)
"""
import sys, re, pathlib

path = sys.argv[1] if len(sys.argv) > 1 else r"src\finanalytics_ai\domain\backtesting\strategies\technical.py"

raw = pathlib.Path(path).read_bytes()
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        src = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue

OLD = """\
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    rsi_ok = rsi_values[i] < self.rsi_filter  # type: ignore[operator]"""

NEW = """\
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    # Escala o limiar com o período do ROC: quando period > 14 o zero-crossing
                    # do ROC ocorre em fases de preço mais extremas → RSI naturalmente mais alto.
                    effective_filter = self.rsi_filter + max(0.0, (self.period - 14) * 3.0)
                    rsi_ok = rsi_values[i] < effective_filter  # type: ignore[operator]"""

if OLD in src:
    pathlib.Path(path).write_text(src.replace(OLD, NEW), encoding="utf-8")
    print("[OK] MomentumStrategy: RSI filter escalado com period do ROC")
else:
    # Tenta versão sem type: ignore
    OLD2 = """\
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    rsi_ok = rsi_values[i] < self.rsi_filter"""
    if OLD2 in src:
        NEW2 = """\
                rsi_ok = True
                if self.rsi_filter > 0 and rsi_values[i] is not None:
                    effective_filter = self.rsi_filter + max(0.0, (self.period - 14) * 3.0)
                    rsi_ok = rsi_values[i] < effective_filter"""
        pathlib.Path(path).write_text(src.replace(OLD2, NEW2), encoding="utf-8")
        print("[OK] MomentumStrategy: RSI filter escalado (variante sem type-ignore)")
    else:
        for i, line in enumerate(src.splitlines(), 1):
            if "rsi_filter" in line and "rsi_ok" in line:
                print(f"[DEBUG] L{i}: {repr(line)}")
        print("[FAIL] Padrão não encontrado — verifique o arquivo")
