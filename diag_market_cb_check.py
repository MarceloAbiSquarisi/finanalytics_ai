"""
diag_market_cb_check.py
Verifica se conn_type=2 esta sendo chamado no worker.
Escreve C:\Temp\market_cb.log com TODOS os eventos recebidos.
Usar: uv run python diag_market_cb_check.py
(parar o worker antes de rodar)
"""
import ctypes, os, sys, time, asyncio
from ctypes import WINFUNCTYPE, c_int, c_size_t, c_uint, c_wchar_p
sys.path.insert(0, r"D:\Projetos\finanalytics_ai_fresh\src")
from dotenv import load_dotenv
load_dotenv(override=False)

LOG = r"C:\Temp\market_cb.log"
dll = ctypes.WinDLL(os.getenv("PROFIT_DLL_PATH", r"C:\Nelogica\profitdll.dll"))
routing_done = False
market_done = False

@WINFUNCTYPE(None, c_int, c_int)
def state_cb(t, r):
    global routing_done, market_done
    msg = f"t={t} r={r}"
    if t == 1 and r >= 4: routing_done = True; msg += " [ROUTING_CONNECTED]"
    if t == 2 and r >= 4: market_done  = True; msg += " [MARKET_CONNECTED]"
    try:
        with open(LOG, 'a') as f: f.write(msg + "\n"); f.flush()
    except: pass
    print(msg, flush=True)

@WINFUNCTYPE(None, c_size_t, c_size_t, c_uint)
def trade_cb(a, p, fl):
    try:
        with open(LOG, 'a') as f: f.write(f"[TRADE] ptr={p:#x}\n"); f.flush()
    except: pass
    print(f"[TRADE] #{p:#x}", flush=True)

async def main():
    # Simula exatamente o que o worker faz agora:
    # start() chama DLLInitializeLogin + armazena cb (sem SetTradeCallbackV2)
    dll.DLLInitializeLogin(
        c_wchar_p(os.getenv("PROFIT_ACTIVATION_KEY","")),
        c_wchar_p(os.getenv("PROFIT_USERNAME","")),
        c_wchar_p(os.getenv("PROFIT_PASSWORD","")),
        state_cb,
        None, None, None, None, None, None, None, None, None, None,
    )
    print("[OK] DLLInitializeLogin chamado (sem SetTradeCallbackV2 aqui)", flush=True)

    # Simula wait_connected: poll para market_login_valid (t=3 r=0)
    # wait_connected retorna quando market_login_valid=True
    # Como state_cb nao seta variavel de controle para t=3,
    # vamos so esperar 3s (tempo tipico)
    await asyncio.sleep(3)
    print("[OK] wait_connected simulado", flush=True)

    # Simula DB init (cria objetos asyncio, como o worker faz)
    import redis.asyncio as aioredis
    redis_client = aioredis.from_url("redis://localhost:6379/0")
    print("[OK] Redis client criado", flush=True)

    # Aguarda routing (como o worker faz)
    for i in range(40):
        if routing_done: print(f"[OK] routing em {i*0.5:.1f}s"); break
        await asyncio.sleep(0.5)
    else:
        print("[WARN] routing timeout")

    # Registra SetTradeCallbackV2 (como o worker faz)
    dll.SetTradeCallbackV2(trade_cb)
    print("[OK] SetTradeCallbackV2 registrado", flush=True)

    # Aguarda market_connected
    for i in range(60):
        if market_done: print(f"[OK] market_connected em {i*0.5:.1f}s"); break
        await asyncio.sleep(0.5)
    else:
        print("[WARN] market_connected timeout")

    print(f"[STATUS] market_done={market_done}", flush=True)

    # Subscribe e aguarda ticks
    ret = dll.SubscribeTicker(c_wchar_p("PETR4"), c_wchar_p("B"))
    ret2 = dll.SubscribeTicker(c_wchar_p("WINFUT"), c_wchar_p("B"))
    print(f"[SUB] PETR4={ret} WINFUT={ret2}", flush=True)

    print("[...] Aguardando 30s...", flush=True)
    for i in range(30):
        await asyncio.sleep(1)
        if (i+1) % 10 == 0:
            print(f"  {i+1}s — market={market_done}", flush=True)

    await redis_client.aclose()
    dll.DLLFinalize()
    print("[OK] Done", flush=True)

if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
