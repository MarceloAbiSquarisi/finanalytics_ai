# Proposta — Decisão 15 do CLAUDE.md (Dual-GPU)

**Data:** 16/abr/2026
**Autor:** Claude (sessão de verificação de hardware)
**Destino:** `CLAUDE.md §3 — Decisões Arquiteturais — IMUTÁVEIS` (adicionar como Decisão 15) e nota de apoio em `§2` (hardware).
**Status:** DRAFT para revisão.

---

## 1. Contexto histórico do usuário

Durante esta sessão, o usuário relatou literalmente:

> "No início usava as duas GPUs e isso fazia a máquina travar e rebotar. Por isso deixei só para treinamento e outra para os monitores."

Este é um diagnóstico operacional importante. Antes de formalizar a Decisão 15, registra-se a causa técnica real do incidente para evitar que no futuro alguém (usuário, colega, ou a própria IA em outra sessão) reverta a solução por desconhecer o problema.

## 2. Diagnóstico técnico do incidente

A assinatura descrita — **reboot imediato, sem BSOD, sem tela azul, sem mensagem** — é característica de **proteção da fonte (OCP/OPP)** disparando sob **transientes de potência**. Não é bug de driver.

**Mecanismo físico:**

- A RTX 4090 tem TDP oficial de 450 W, mas Ada Lovelace gera **picos transientes em microssegundos a poucos milissegundos** que chegam a 1,5–2× o TDP — medidos em laboratório entre ~700 W e ~900 W por placa sob compute intensivo.
- Com **duas RTX 4090 em compute simultâneo**, os transientes podem se sincronizar. No pior caso, a PSU vê picos combinados entre **1.400 e 1.800 W em alguns milissegundos**.
- PSUs pré-ATX 3.0 têm tolerância de transiente limitada (tipicamente ~110–120% do nominal por 100 µs). Um pico de 1,5 kW em uma PSU de 1,2 kW dispara **Over-Current Protection** e corta a alimentação → reboot.

**Por que não há "correção de driver":**

- NVIDIA não pode alterar a física do transiente via driver.
- O que evoluiu foi o ecossistema:
    - **ATX 3.0 / 3.1** — normatizou tolerância a transientes até 200% do nominal por tempo curto.
    - **12V‑2×6 revisão B** — resolveu o problema do sense wire que causava alguns casos de conector derretido.
    - **Power limit via `nvidia-smi -pl`** — mecanismo de software que derruba o teto de potência e corta transientes.

**Por que a adaptação do usuário funcionou:**

Ao deixar a GPU 0 headless/ociosa e rodar compute apenas na GPU 1 (a do desktop), o usuário **eliminou a simultaneidade de transientes**. Só uma GPU entra em regime de compute pesado por vez — os transientes não se somam. PSU nunca vê o pico combinado. Reboots param.

Nota subsequente: esta sessão revelou que os **cabos de vídeo foram trocados** entre as GPUs em algum momento. Hoje a GPU com monitor é a de bus `08:00.0` (índice lógico 1), e a sem monitor é a de bus `01:00.0` (índice lógico 0). O `docker-compose.override.yml` ainda usa `CUDA_VISIBLE_DEVICES: "1"` baseado na configuração anterior — apontando para a GPU que hoje serve o desktop.

## 3. Confirmação experimental do mapeamento atual

Realizado em 16/abr/2026 com container externo:

```
docker run --rm --gpus '"device=0"' nvidia/cuda:12.1.0-base-ubuntu22.04 \
    nvidia-smi --query-gpu=index,name,pci.bus_id --format=csv

Resultado:
index, name,                          pci.bus_id
0,     NVIDIA GeForce RTX 4090,       00000000:01:00.0   (headless, Disp.A Off)
1,     NVIDIA GeForce RTX 4090,       00000000:08:00.0   (desktop,  Disp.A On)
```

Portanto:
- **GPU lógica 0** (Docker/CUDA enumeração) = bus 01:00.0 = headless = **correta para compute**.
- **GPU lógica 1** = bus 08:00.0 = desktop = reservada para Windows shell e aplicativos.

O override atual (`CUDA_VISIBLE_DEVICES: "1"`) está apontando para a GPU errada.

## 4. Complicação detectada — containers não têm GPU ainda

Durante a mesma investigação ficou evidente que **nenhum dos serviços do `docker-compose.yml` atual recebe GPU reservation**. Falta em todos:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          device_ids: ["0"]
          capabilities: [gpu]
```

Sem este bloco, o Docker não passa nenhuma GPU para dentro do container. O `CUDA_VISIBLE_DEVICES` fica inócuo (filtra de um conjunto vazio). Os workloads atuais (scheduler, api, workers) são CPU-bound e nunca precisaram de GPU de verdade — por isso a lacuna passou despercebida.

Quando o Sprint S03 (HMM) e seguintes começarem a depender de compute acelerado (LSTM, FinBERT, GAT, vLLM), a reservation precisa ser adicionada **junto** com a correção de `CUDA_VISIBLE_DEVICES`.

## 5. Proposta formal — Decisão 15 (versão final após confirmação do usuário)

**Input do usuário em 16/abr/2026:** PSU instalada é ≥ 1.500 W **E é a mesma unidade que historicamente causou os reboots** quando as duas GPUs eram usadas em compute simultâneo pesado. Essa evidência empírica é vinculante — um rating nominal não desfaz um fato medido.

Adicionar à lista de decisões imutáveis do `CLAUDE.md §3`:

> **15. Dual-GPU — separação estrita e explícita:**
>
> - Toda carga de compute ML (treinamento, inferência, serving, embeddings) é executada **exclusivamente na GPU 0** (enumeração CUDA/Docker, equivalente ao PCIe bus `01:00.0` — a placa sem monitor).
> - A GPU 1 (bus `08:00.0`, com monitor) é reservada para o Windows/desktop. Nunca recebe workload de compute em regime de produção.
> - Toda definição de serviço Docker que depende de GPU deve declarar **`deploy.resources.reservations.devices`** com `device_ids: ["0"]` e `capabilities: [gpu]`. `CUDA_VISIBLE_DEVICES` acompanha com `"0"` para redundância explícita.
> - Uso simultâneo das duas GPUs em compute pesado **proibido** enquanto a PSU instalada for a mesma que causou reboots nesse regime — independente do rating nominal. A evidência empírica supera o spec.
> - Para habilitar dual-GPU simultâneo no futuro, uma das duas condições precisa ser satisfeita: (a) **upgrade da PSU** para modelo ≥ 1.600 W ATX 3.0/3.1 80+ Titanium com 2 cabos 12V-2×6 nativos (sem adaptador 4×8-pin → 12VHPWR); OU (b) **migração do compute para o servidor de colocation** (CLAUDE.md §16.4), onde a PSU nova já resolve o problema na origem.
> - **Exceção temporária autorizada** (Modo 2, ver Anexo A): workloads ML **distintos** por GPU (ex.: treino na GPU 0, FinBERT inferência na GPU 1), exclusivamente para jobs offline que toleram retry, com power cap obrigatório `nvidia-smi -pl 320` em ambas as placas e monitoramento ativo. **Nunca** aplicar em horário de pregão nem em serving crítico de produção.
> - **Proibido totalmente:** Modo 3 (paralelismo puro via `device_map="auto"`, DDP, DataParallel, ou qualquer sincronização a cada step) enquanto a PSU atual estiver instalada. Esse é o modo que demonstradamente trava a máquina.
> - Se os cabos de monitor forem fisicamente remanejados entre placas, a env var `CUDA_VISIBLE_DEVICES` e o `device_ids` do compose **devem ser revisados** antes de o próximo container com compute subir. Verificação padrão: `docker run --rm --gpus '"device=0"' nvidia/cuda:<versão> nvidia-smi --query-gpu=pci.bus_id --format=csv` — deve retornar `00000000:01:00.0`.

Adicionar também à §2 (Hardware), como nota:

> **GPU — configuração ativa:**
> - GPU 0 (bus 01:00.0): sem monitor, dedicada a compute. Em idle P8 ≈ 45 °C, 18 W. 24 GB VRAM.
> - GPU 1 (bus 08:00.0): monitor principal, desktop do Windows. Em uso leve P0 ≈ 37 °C, 60 W, ~2 GB VRAM ocupados por processos do sistema.
> - Driver NVIDIA Windows 591.86, CUDA reportada 13.1, compute capability 8.9 (Ada Lovelace).
> - Uso simultâneo das duas em compute pesado **bloqueado por decisão imutável 15**.

## 6. Implementação sugerida — quando aplicar

**Agora (17-18/abr, durante a janela de ingestão):** nada. Os containers atuais não precisam de GPU; mexer no compose obriga `docker compose up -d` no serviço afetado e corre risco de interromper o scheduler. Segurar.

**A partir de 19/abr (pós-ingestão):** atualizar o `docker-compose.override.yml`:

```yaml
services:
  worker:
    environment:
      CUDA_VISIBLE_DEVICES: "0"          # era "1" — corrige mapeamento
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              device_ids: ["0"]
              capabilities: [gpu]

  # análogo para api, scheduler, worker_v2 SE/QUANDO precisarem de GPU
```

Atualizar os Dockerfiles dos serviços que vão usar GPU para instalar `nvidia-utils-535` (ou pacote equivalente), permitindo `nvidia-smi` dentro do container para debug:

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends nvidia-utils-535 && \
    rm -rf /var/lib/apt/lists/*
```

**Teste de validação pós-alteração:**

```bash
docker exec finanalytics_worker nvidia-smi --query-gpu=index,name,pci.bus_id --format=csv
# Esperado: apenas a linha da GPU 0 (bus 01:00.0)
```

## 7. Checklist de aceitação da Decisão 15

Antes de encerrar a Decisão 15 como "aplicada":

- [ ] Texto da Decisão 15 presente em `CLAUDE.md §3`.
- [ ] Nota atualizada em `CLAUDE.md §2` confirmando bus IDs e papéis.
- [ ] Pelo menos um container produtivo (ex.: worker) com GPU reservation ativa, passando no teste de validação.
- [ ] `nvidia-smi.exe` do host em horário de pregão mostra GPU 0 ativa (compute) e GPU 1 com < 10% GPU-Util (só desktop).
- [ ] Documento `verificacao_hardware_finanalyticsai.md` com P22 marcada como resolvida.
- [ ] Se em algum momento houver decisão deliberada de usar as duas GPUs simultaneamente em compute, antes disso: validar PSU (marca/modelo na §2) e aplicar `nvidia-smi -pl 380` em ambas. Registrar no documento como exceção formal autorizada.

## 8. Material de apoio produzido nesta sessão

- Saída do `nvidia-smi.exe` do host em 16/abr (registrada em `verificacao_hardware_finanalyticsai.md`, Seção 4).
- Saída do `docker run --gpus` confirmando mapeamento `device=0 → bus 01:00.0` (Seção 8 do mesmo documento e log de chat).
- Histórico de logs do `finanalytics_worker` e do `ohlc_ingestor` mostrando que nenhum container carrega CUDA runtime atualmente.
- Override `docker-compose.override.yml` com o texto atual do `CUDA_VISIBLE_DEVICES: "1"` — a correção proposta é de uma letra (`"0"`).

---

# Anexo A — Manual de ativação gradual de dual-GPU

Este anexo documenta o procedimento operacional para experimentar uso das duas GPUs em compute, caso a Decisão 15 seja relaxada no futuro (upgrade de PSU ou exceção formal autorizada para Modo 2). O caminho é **em degraus**, cada um reversível em segundos. Organizado por risco crescente.

**Pré-requisito de leitura:** as Seções 1 a 4 deste documento, que cobrem o diagnóstico físico do incidente de reboot e o mapeamento GPU lógico × PCIe bus.

## A.1 — Os três modos de "usar duas GPUs"

Antes de qualquer execução, alinhar o vocabulário. Cada modo tem um perfil de risco diferente:

| Modo | O que significa | Risco de reboot | Ganho para o projeto |
|---|---|---|---|
| **1 — Alternância** | GPU 0 para compute, GPU 1 para desktop. Um único compute pesado por vez. | **Baixo** — é o que a Decisão 15 prescreve como default. | Zero ganho de capacidade combinada; só resolve mapeamento. |
| **2 — Divisão de carga (split)** | GPU 0 para workload pesado contínuo (treino, LLM serving); GPU 1 para workload leve contínuo (inferência FinBERT, embeddings). Ambas ativas, mas com perfis de transiente diferentes. | **Médio** — transientes podem ou não sincronizar conforme o padrão de carga. | 2× VRAM efetiva, paralelismo real de tarefas diferentes. |
| **3 — Paralelismo puro** | Um único job distribuído nas duas GPUs: `device_map="auto"` do HuggingFace, DDP/DataParallel do PyTorch, Ray distribuído, Accelerate multi-GPU, etc. Sincronização a cada step. | **Alto** — reproduz o cenário que causou os reboots. Transientes sincronizados quase garantidos. | Acelera treino 1,7–1,9×, permite modelo > 24 GB. |

**Para o FinAnalyticsAI:** Modo 1 é o default permanente pela Decisão 15. Modo 2 é exceção autorizada só para jobs offline não críticos com power cap. Modo 3 é proibido enquanto a PSU atual estiver instalada.

## A.2 — Decisão baseada na PSU

Matriz de viabilidade por classe de PSU:

| PSU instalada | Modo 1 | Modo 2 | Modo 3 |
|---|---|---|---|
| < 1.200 W, pré-ATX 3.0 | ok | **só com cap 320 W** + teste | proibido |
| 1.200–1.400 W ATX 3.0 | ok | ok sem cap | **só com cap 380 W** + teste |
| ≥ 1.500 W ATX 3.0 | ok | ok sem cap | ok com cap 380 W |
| ≥ 1.600 W ATX 3.0 Titanium | ok | ok sem cap | ok sem cap (testar antes) |
| **≥ 1.500 W, MESMA PSU dos crashes históricos** | ok | **Modo 2 com cap 320 W, jobs offline** | **proibido** (Decisão 15) |

**Situação atual (16/abr/2026):** PSU ≥ 1.500 W, mas **é a mesma unidade** em que o usuário teve reboots ao usar dual-GPU pesado. Linha aplicável: a última. Modo 2 virou exceção temporária; Modo 3 proibido até upgrade ou colocation.

## A.3 — Três caminhos de ação

### Caminho A — Ficar no Modo 1 (recomendado)

Manter o que já funciona, corrigindo apenas o mapeamento `CUDA_VISIBLE_DEVICES: "1"` → `"0"` no `docker-compose.override.yml`. Zero risco de reboot. Suficiente para todo o roadmap do FinAnalyticsAI v1.3 (todos os modelos cabem em 24 GB). A segunda RTX 4090 fica como reserva fria.

**Ganho real:** zero, porque o projeto não precisa. **Custo:** R$ 0, 1 caractere editado.

### Caminho B — Modo 2 com cap agressivo (exceção autorizada)

Ativar dual-GPU em workloads distintos por placa, com power cap de 320 W (71% do TDP). Corta ~30% da amplitude dos transientes. Usar só para jobs offline tolerantes a retry (treino, backtesting, CTGAN). **Nunca** em serving de produção em horário de pregão.

**Ganho:** 2× VRAM efetiva. **Custo:** < 5% de desempenho por GPU, mais risco residual de reboot em dia ruim.

### Caminho C — Upgrade de PSU (eliminar a incerteza)

Trocar por unidade ≥ 1.600 W ATX 3.0/3.1 80+ Titanium com 2 cabos 12V-2×6 nativos. Exemplos qualificados em abr/2026:

- Corsair AX1600i Titanium (ATX 3.0)
- Corsair HX1500i ATX 3.1 Titanium
- ASUS ROG Thor 1600W Titanium III ATX 3.1
- be quiet! Dark Power Pro 13 1600W Titanium ATX 3.0
- FSP Hydro PTM X PRO ATX 3.0 1650W

Faixa R$ 2.500–4.500 no Brasil. Resolve permanentemente o problema. Mas considerar que o roadmap de §16.4 do CLAUDE.md já prevê migração para colocation SP com hardware novo na Fase 4 (Mês 7-9) — PSU nova ali vai existir de qualquer forma. ROI de comprar PSU hoje depende de quanto tempo a estação de dev vai ser crítica antes da migração.

**Recomendação desta proposta:** Caminho A como default permanente enquanto a estação de dev existir. Caminho B só se um Sprint específico demonstrar necessidade real (S18 PPO ou S21 CTGAN podem, mas ainda não provado). Caminho C só se Caminho B virar rotina e risco residual incomodar.

## A.4 — Os 6 degraus de ativação (Degraus 0 a 5)

Independente do Caminho escolhido, a sequência técnica de ativação é a mesma. Cada degrau é independente e reversível.

### Degrau 0 — Identificar a PSU (5 minutos, zero toque em software)

Abrir a lateral do gabinete, fotografar a etiqueta da PSU. Dado a registrar na §2 do `CLAUDE.md`:

- Marca e modelo
- Rating nominal (W)
- Certificação ATX 3.0 / 3.1 / 2.x
- 80+ Gold / Platinum / Titanium
- Tipo de conectores PCIe saindo da PSU (nativo 12V-2×6 ou adaptador)

**Status em 16/abr:** ≥ 1.500 W confirmado pelo usuário, é a mesma PSU dos incidentes históricos. Marca/modelo específicos ainda não registrados — bloquear refino até receber.

### Degrau 1 — Corrigir o mapeamento lógico (30 segundos, rollback trivial)

Editar `D:\Projetos\finanalytics_ai_fresh\docker-compose.override.yml`, trocar `CUDA_VISIBLE_DEVICES: "1"` por `"0"` em cada serviço de compute (`api`, `worker`, `scheduler`, `worker_v2` se aplicável). Adicionar bloco de GPU reservation nos serviços que precisarem (ver §6 deste documento).

Aplicar:

```powershell
cd D:\Projetos\finanalytics_ai_fresh
git add -A
git commit -m "chore(gpu): corrigir CUDA_VISIBLE_DEVICES para GPU 0 (headless)"
git tag snapshot-pre-gpu-fix-$(Get-Date -Format yyyyMMdd)
docker compose up -d --no-deps api worker scheduler
```

**Rollback:**

```powershell
git reset --hard snapshot-pre-gpu-fix-20260419  # ajustar data da tag
docker compose up -d --no-deps api worker scheduler
```

### Degrau 2 — Snapshot de power limit + aplicar cap (5 segundos, reversível na hora)

Antes de qualquer teste dual-GPU, aplicar cap como hábito. **Requer PowerShell como Administrador.**

**Snapshot (backup dos valores default):**

```powershell
New-Item -ItemType Directory -Force -Path 'E:\FinAnalyticsAI\backups_manuais' | Out-Null
$backup = "E:\FinAnalyticsAI\backups_manuais\gpu_power_limits_$(Get-Date -Format 'yyyyMMdd_HHmm').csv"

nvidia-smi.exe --query-gpu=index,name,power.limit,power.default_limit,power.min_limit,power.max_limit `
               --format=csv |
    Out-File -FilePath $backup -Encoding ASCII

Write-Host "Snapshot salvo em: $backup"
Get-Content $backup
```

**Aplicar cap (320 W é o valor recomendado para a PSU histórica, mais conservador que os 380 W genéricos):**

```powershell
# Cap 320 W nas duas placas
nvidia-smi.exe -i 0 -pl 320
nvidia-smi.exe -i 1 -pl 320

# Conferir
nvidia-smi.exe --query-gpu=index,name,power.limit --format=csv
```

**Rollback a qualquer momento, mesmo sob carga:**

```powershell
# Restaurar default (450 W)
nvidia-smi.exe -i 0 -pl 450
nvidia-smi.exe -i 1 -pl 450
```

**Persistência:** no Windows o cap **NÃO sobrevive ao reboot**. Para tornar persistente, criar uma tarefa no Task Scheduler que executa os dois `nvidia-smi -pl` no startup do sistema, com trigger "At startup" e privilégio mais alto. Por enquanto deixar manual é melhor — só ativa quando vai usar dual.

### Degrau 3 — Teste controlado de carga (10 min, rollback por Ctrl+C)

Antes de colocar Modo 2 em produção, validar estabilidade com teste sintético. **Requer** GPU reservation já funcional (Degrau 1 aplicado) OU teste via container externo.

**Instalar `gpu-burn` no WSL2 (uma vez):**

```bash
# Dentro do WSL2 Ubuntu 22.04
sudo apt update
sudo apt install -y build-essential
cd ~
git clone https://github.com/wilicc/gpu-burn.git
cd gpu-burn
make
```

**Teste A — só GPU 0 sob carga por 10 minutos (valida que nada fundamental está quebrado):**

```powershell
# Terminal 1 (WSL2 bash)
cd ~/gpu-burn && CUDA_VISIBLE_DEVICES=0 ./gpu_burn 600

# Terminal 2 (PowerShell) — monitor
nvidia-smi.exe dmon -i 0 -s pucvmet
```

**Teste B — ambas GPUs em carga simultânea por 10 minutos (o teste que importa):**

```powershell
# Terminal 1 (WSL2 bash) — ambas as GPUs
cd ~/gpu-burn && ./gpu_burn 600

# Terminal 2 (PowerShell) — monitor GPU 0
nvidia-smi.exe dmon -i 0 -s pucvmet

# Terminal 3 (PowerShell) — monitor GPU 1
nvidia-smi.exe dmon -i 1 -s pucvmet
```

**Critérios de aborto (parar o teste imediatamente com Ctrl+C + aplicar rollback):**

- Reboot espontâneo (óbvio — PSU tripou OCP).
- Temperatura > 85 °C em qualquer placa por mais de 10 segundos.
- Power draw colado no cap por > 30 segundos com GPU-Util < 80% (sinal de throttling excessivo).
- Coil whine audível novo vindo da PSU.
- `nvidia-smi` parando de responder ou retornando erro (driver TDR).
- Evento em **Event Viewer → Windows Logs → System** com source "Kernel-Power" ID 41 (aconteceu reboot inesperado).

**Kill switch de emergência:**

```powershell
# Mata todos os processos CUDA imediatamente
# (pode travar aplicações GPU em curso — aceito, é emergência)
nvidia-smi.exe --gpu-reset -i 0
nvidia-smi.exe --gpu-reset -i 1
```

### Degrau 4 — Colocar Modo 2 em produção com rollback via git tag

Se o Degrau 3 passou sem incidente, pode-se usar Modo 2 em produção (sempre para jobs offline, nunca para serving crítico em horário de pregão).

**Backup completo antes de ativar:**

```powershell
# 1) Estado do git
cd D:\Projetos\finanalytics_ai_fresh
git add -A
git commit -m "snapshot: antes de ativar dual-GPU Modo 2"
git tag snapshot-pre-dual-gpu-$(Get-Date -Format yyyyMMdd)

# 2) Snapshot dos power limits
nvidia-smi.exe --query-gpu=index,power.default_limit,power.limit --format=csv |
    Out-File "E:\FinAnalyticsAI\backups_manuais\gpu_pl_$(Get-Date -Format yyyyMMdd_HHmm).csv" -Encoding ASCII

# 3) Compose renderizado (ajuda comparar depois)
docker compose config |
    Out-File "E:\FinAnalyticsAI\backups_manuais\compose_rendered_$(Get-Date -Format yyyyMMdd_HHmm).yaml" -Encoding ASCII
```

**Editar `docker-compose.override.yml` para Modo 2 — exemplo com worker pesado em GPU 0 e API de inferência leve em GPU 1:**

```yaml
services:
  worker:
    environment:
      CUDA_VISIBLE_DEVICES: "0"        # compute pesado na headless
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              device_ids: ["0"]
              capabilities: [gpu]

  api:
    environment:
      CUDA_VISIBLE_DEVICES: "1"        # inferência leve na GPU com desktop
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              device_ids: ["1"]
              capabilities: [gpu]
```

**Aplicar com cap já ativo:**

```powershell
# Garantir cap antes de subir
nvidia-smi.exe -i 0 -pl 320
nvidia-smi.exe -i 1 -pl 320

# Subir os serviços alterados
docker compose up -d --no-deps worker api
```

**Rollback completo (um comando para voltar ao estado anterior):**

```powershell
cd D:\Projetos\finanalytics_ai_fresh
git reset --hard snapshot-pre-dual-gpu-20260419
docker compose up -d --no-deps worker api
nvidia-smi.exe -i 0 -pl 450
nvidia-smi.exe -i 1 -pl 450
```

Tempo entre detectar problema e ter o estado anterior de volta: ~10 segundos.

### Degrau 5 — Modo 3 (paralelismo puro) — PROIBIDO com PSU atual

**Este degrau não deve ser executado enquanto a PSU instalada for a mesma dos incidentes históricos.** A Decisão 15 formaliza a proibição. Listado aqui apenas para referência de como seria caso a PSU fosse trocada (Caminho C), para evitar que o procedimento seja esquecido:

1. Confirmar PSU ≥ 1.600 W ATX 3.0/3.1 fisicamente instalada e testada.
2. Degrau 4 rodando em produção sem incidente por **pelo menos 30 dias**.
3. Power cap mantido `-pl 380` como hábito.
4. Monitoramento ativo via `dcgm-exporter` + alertas Prometheus em temp > 80 °C e power > 95% do cap.
5. Janela de manutenção isolada para o primeiro teste — não aplicar direto em produção.
6. Stress test de 4 horas com carga DDP real antes de considerar estável.
7. Registrar no `CLAUDE.md §14` a exceção formal autorizada, com data e justificativa.

## A.5 — Triggers de rollback e monitoramento contínuo

Uma vez em Modo 2, monitorar continuamente. Indicadores que justificam voltar para Modo 1:

**Automáticos (se você tiver Prometheus/Alertmanager):**

- `nvidia_gpu_temperature_celsius > 80` por 5 minutos consecutivos
- `nvidia_gpu_power_usage_watts / power_limit_watts > 0.95` por 5 minutos
- `nvidia_gpu_utilization_percent > 95` e `throttle_reasons != 0` (throttling)
- Event Viewer Windows capturado via WinEventLog exporter — alerta em Kernel-Power 41

**Manuais (observação do usuário):**

- Reboot espontâneo uma vez — suspender Modo 2 imediatamente, registrar incidente, revisar em 24 h.
- Dois reboots em 7 dias — encerrar Modo 2 definitivamente, assumir Caminho A.
- Instabilidade de driver (TDR) — investigar antes de reativar.
- Coil whine novo na PSU — sinal de estresse elétrico, suspender até resolver.

## A.6 — Checklist para cada ativação/desativação

Para reduzir erro operacional, usar este checklist sempre que trocar o modo GPU:

**Antes de ATIVAR Modo 2:**

- [ ] PSU identificada e documentada em `CLAUDE.md §2`.
- [ ] Snapshot de power limits em `E:\FinAnalyticsAI\backups_manuais\`.
- [ ] Git commit + tag com label `snapshot-pre-dual-gpu-YYYYMMDD`.
- [ ] Compose renderizado arquivado.
- [ ] Cap 320 W aplicado em ambas as GPUs (confirmado via `nvidia-smi`).
- [ ] Degrau 3 executado com sucesso nas últimas 24 h.
- [ ] Kill switch conhecido e testado.
- [ ] Janela de uso é off-hours (não horário de pregão).
- [ ] Workload é offline e tolerante a retry.

**Antes de DESATIVAR (voltar para Modo 1):**

- [ ] Job em curso concluído ou parado graciosamente.
- [ ] `docker compose down --no-deps <serviço_com_gpu_1>` ou rollback via git tag.
- [ ] Power limit restaurado para default (450 W).
- [ ] `nvidia-smi` confirma GPU 1 em P8 ou P0 com < 10 W (idle).
- [ ] Log do incidente (se houver) salvo em `logs/gpu_modo2_YYYYMMDD.log`.

## A.7 — O que NÃO fazer em nenhuma hipótese

- Rodar DDP/DataParallel de PyTorch com `device_ids=[0,1]` sem upgrade de PSU.
- `model.to("cuda")` com `device_map="auto"` em modelo > 22 GB (força split automático entre as duas GPUs).
- Serving crítico (inferência em horário de pregão, risk manager, execução de ordens) na GPU 1.
- Rodar `gpu-burn` nas duas GPUs simultaneamente **sem** power cap aplicado.
- Desabilitar o power cap durante workload ativo (aumenta transiente com placa quente — pior cenário).
- Usar um único cabo 12VHPWR com daisy-chain de 2 conectores 8-pin PCIe da PSU (cada 4090 precisa dos seus próprios pares de cabos dedicados à PSU).
- Ignorar Event Viewer reclamando de Kernel-Power 41 após um teste — é evidência técnica, não ruído.

---

*Anexo A redigido em 16/abr/2026 após confirmação do usuário de que a PSU é ≥ 1.500 W mas é a mesma unidade dos reboots históricos. Revisar ao trocar PSU ou migrar compute para servidor de colocation SP.*
