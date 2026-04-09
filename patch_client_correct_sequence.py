import sys, pathlib

TARGET = pathlib.Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")
text = TARGET.read_text(encoding="utf-8")

if "patch_correct_sequence_applied" in text:
    print("JA APLICADO"); sys.exit(0)

# Localiza a linha com registered_early e substitui o bloco
lines = text.splitlines(keepends=True)
out = []
i = 0
patched1 = False
while i < len(lines):
    line = lines[i]
    # Detecta inicio do bloco a substituir
    if "trade_callback_v2_registered_early" in line and not patched1:
        # Remove linhas anteriores do bloco (SetTradeCallbackV2 e comentário)
        # Remove as ultimas linhas adicionadas que fazem parte do bloco
        while out and ("SetTradeCallbackV2" in out[-1] or "IMEDIATAMENTE" in out[-1] or "nao pode aguardar" in out[-1]):
            out.pop()
        # Adiciona nova implementacao
        indent = "        "
        out.append(f"{indent}# patch_correct_sequence_applied\n")
        out.append(f"{indent}# Sequencia correta provada pelo diag_cotation_cb.py\n")
        out.append(f"{indent}# REGRA manual sec 3.2: nunca chamar DLL dentro de callback\n")
        out.append(f"{indent}self._dll.SetChangeCotationCallback(_trade_cb_v2)\n")
        out.append(f"{indent}self._dll.SetTradeCallback(_trade_cb_v2)\n")
        out.append(f"{indent}log.info(\"profit_dll.callbacks_registered_after_init\")\n")
        patched1 = True
        i += 1
        # Pula linha de separacao se existir
        if i < len(lines) and lines[i].strip().startswith("# " + "\u2500"*5):
            out.append(lines[i])
            i += 1
        continue
    out.append(line)
    i += 1

if not patched1:
    print("ERRO: linha 'trade_callback_v2_registered_early' nao encontrada")
    sys.exit(1)
print("Patch 1 (callbacks apos init): OK")

# Patch 2: state_cb event.set no r=4
text2 = "".join(out)
OLD2 = "if r >= 4:   _state.market_connected = True"
NEW2 = ("if r >= 4:\n"
        "                _state.market_connected = True\n"
        "                try:\n"
        "                    _event.set()  # sinaliza thread subscribe\n"
        "                except Exception:\n"
        "                    pass")
if OLD2 in text2:
    text2 = text2.replace(OLD2, NEW2, 1)
    print("Patch 2 (event.set r=4): OK")
else:
    print("AVISO: Patch 2 nao encontrado (pode ja estar OK)")

# Patch 3: adiciona start_subscribe_thread
ANCHOR = '        log.warning("profit_dll.connect_timeout", timeout=timeout, state=self._state)\n        return False\n'
METHOD = '''
    def start_subscribe_thread(self, tickers: list[str], exchange: str = DEFAULT_EXCHANGE) -> None:
        """Thread separada: aguarda t=2 r=4 e chama SubscribeTicker fora do callback."""
        import threading as _threading, time as _time
        from ctypes import c_wchar_p as _cwp
        _dll, _event, _log = self._dll, self._connected_event, log

        def _sub():
            if not _event.wait(timeout=90):
                _log.warning("profit_dll.subscribe_thread_timeout"); return
            _time.sleep(0.5)
            for ticker in tickers:
                ret = _dll.SubscribeTicker(_cwp(ticker), _cwp(exchange))
                _log.info("profit_dll.subscribed_via_thread", ticker=ticker, ret=ret)

        _threading.Thread(target=_sub, daemon=True).start()
        log.info("profit_dll.subscribe_thread_started", tickers=tickers)

'''

if "start_subscribe_thread" not in text2 and ANCHOR in text2:
    text2 = text2.replace(ANCHOR, ANCHOR + METHOD, 1)
    print("Patch 3 (start_subscribe_thread): OK")
elif "start_subscribe_thread" in text2:
    print("Patch 3: ja existe")
else:
    print("AVISO: Patch 3 anchor nao encontrado")

TARGET.write_text(text2, encoding="utf-8")
print("PATCH COMPLETO")
