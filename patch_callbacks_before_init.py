import pathlib, sys

f = pathlib.Path("src/finanalytics_ai/infrastructure/market_data/profit_dll/client.py")
t = f.read_text(encoding="utf-8")

# Move SetTradeCallback para ANTES do DLLInitializeLogin
OLD = (
    "        self._cb_trade = _trade_cb_v2  # sobrescreve minimal com assinatura correta\n"
    "        # SetTradeCallbackV2 NAO chamado aqui \u2014 sera registrado uma unica vez\n"
    "        # no worker APOS routing, igual ao diagnostico que funciona.\n"
    "        log.info(\"profit_dll.trade_callback_stored\")\n"
    "\n"
    "\n"
    "        # Inicializa via DLLInitializeLogin"
)

NEW = (
    "        self._cb_trade = _trade_cb_v2  # sobrescreve minimal com assinatura correta\n"
    "        log.info(\"profit_dll.trade_callback_stored\")\n"
    "\n"
    "        # Registra callbacks ANTES do DLLInitializeLogin — igual ao diag que funcionou\n"
    "        self._dll.SetTradeCallback(_trade_cb_v2)\n"
    "        self._dll.SetChangeCotationCallback(_trade_cb_v2)\n"
    "        log.info(\"profit_dll.callbacks_registered_before_init\")\n"
    "\n"
    "        # Inicializa via DLLInitializeLogin"
)

if OLD in t:
    t = t.replace(OLD, NEW, 1)
    print("Patch 1 (callbacks antes do init): OK")
else:
    print("ERRO: Patch 1 nao encontrado")
    sys.exit(1)

# Remove registro duplicado apos init
OLD2 = (
    "        # patch_correct_sequence_applied\n"
    "        # Sequencia correta provada pelo diag_cotation_cb.py\n"
    "        # REGRA manual sec 3.2: nunca chamar DLL dentro de callback\n"
    "        self._dll.SetTradeCallback(_trade_cb_v2)\n"
    "        log.info(\"profit_dll.callbacks_registered_after_init\")\n"
    "        # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)
NEW2 = "        # callbacks ja registrados antes do init"

if OLD2 in t:
    t = t.replace(OLD2, NEW2, 1)
    print("Patch 2 (remove duplicata apos init): OK")
else:
    print("AVISO: Patch 2 nao encontrado (pode ja estar limpo)")

f.write_text(t, encoding="utf-8")
print("CONCLUIDO")
