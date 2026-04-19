# Plano operacional — 17 a 19 de abril de 2026

**Contexto:** o `finanalytics_scheduler` roda ingestão OHLCV diariamente às 07:00 BRT, com duração de ~25 minutos. As duas últimas execuções programadas para concluir a ingestão são **17/abr 07:00 BRT** e **18/abr 07:00 BRT**. Nenhuma ação destrutiva deve ser executada durante essas janelas. Este documento organiza o que fazer em cada momento das próximas ~60 horas.

**Fuso horário de referência:** BRT (UTC-3).

## Atualização 16/abr ~22h — reboot autorizado

O usuário confirmou que reboot da estação de dev está autorizado. Três pendências que estavam em fila são desbloqueadas e entram no **Bloco E (19/abr, pós-ingestão)** deste plano:

- **P1**: preencher `.wslconfig` vazio (requer `wsl --shutdown`)
- **P11**: habilitar XMP/EXPO para RAM ir de 4000 MHz para 5200–5600 MHz (requer reboot + BIOS)
- **P12**: migrar VHDXes (Ubuntu-22.04 e docker-desktop) de `C:` para `E:` (requer `wsl --shutdown` + recriação)

Sequência dentro do Bloco E é importante: primeiro P12 (migração VHDX) e P1 (`.wslconfig`) juntos, em uma única janela de `wsl --shutdown`, para minimizar interrupções. Depois, em uma segunda janela separada, P11 (BIOS) — porque exige reboot físico e entrar no setup, não é trivialmente reversível.

---

## Linha do tempo

```
16/abr 21:30  (agora) ─┐
                       │  TODO encerramento da sessão de diagnóstico
                       │  3 drafts produzidos na pasta Melhorias/
                       │
17/abr 07:00 ──────────┤  JANELA 1 — Scheduler roda (~25 min)
                       │  Não tocar no stack. Não rodar docker/wsl commands
                       │  destrutivos. Checagem de leitura tudo bem.
17/abr 07:30 ──────────┤  Ingestão terminada. Log esperado em
                       │  finanalytics_scheduler com "scheduler.ohlcv.next"
17/abr tarde-noite ────┤  Zona verde para diagnóstico do bash WSL2
                       │  (zero risco), revisão de drafts, planejamento
                       │
18/abr 07:00 ──────────┤  JANELA 2 — Scheduler roda (última)
18/abr 07:30 ──────────┤  Fim da ingestão. Dados consolidados.
18/abr 08:00+ ─────────┤  ZONA VERDE para ações destrutivas autorizadas
                       │
19/abr ────────────────┤  Pacote de faxina + pg_dump + compose updates
```

---

## Blocos operacionais

### Bloco A — 17/abr manhã (07:00 BRT): deixar o scheduler rodar

**Janela:** 07:00–07:30 BRT.

**O que NÃO fazer (bloqueado):**

- `docker compose down`, `docker stop finanalytics_*`, `docker kill`, `docker system prune`, `docker volume prune`, `docker image prune`, `docker builder prune`
- `wsl --shutdown`, `wsl --unregister <qualquer>`, `Restart-Computer`
- Editar `docker-compose.yml` ou `docker-compose.override.yml`
- `VACUUM FULL` em qualquer tabela (regular `VACUUM ANALYZE` OK)
- Reiniciar Docker Desktop

**O que é seguro (opcional):**

- Consultar log do scheduler em tempo real:
    ```powershell
    docker logs finanalytics_scheduler --follow --tail 20
    ```
- Consultar `nvidia-smi.exe` no host (não afeta containers).
- Abrir Grafana (`http://localhost:3000`), Kafka UI (`http://localhost:8080`), pgAdmin (`http://localhost:5050`) — só leitura.
- Ler os drafts produzidos nesta sessão:
    - `D:\Investimentos\FinAnalytics_AI\Melhorias\proposta_claude_md_v14_novo.md`
    - `D:\Investimentos\FinAnalytics_AI\Melhorias\proposta_decisao_15_dualgpu.md`
    - `D:\Investimentos\FinAnalytics_AI\Melhorias\verificacao_hardware_finanalyticsai.md`

**Checkpoint esperado (07:30):**

```powershell
docker logs finanalytics_scheduler --tail 5
# Última linha deve conter "scheduler.ohlcv.next" com next_utc para 18/abr
```

Se o scheduler não concluir dentro de 40 minutos ou registrar erro, **pausar este plano** e investigar antes de qualquer outra ação.

### Bloco B — 17/abr durante o dia: diagnóstico do bash WSL2

Este bloco era o último item da verificação de hardware e pode ser executado com segurança durante o dia 17/abr. Nada aqui toca em containers ou bancos. Serve para completar o Bloco 0 Pré-flight do `CLAUDE.md §17`.

Abrir **Windows Terminal → perfil Ubuntu-22.04** (NÃO usar `cmd.exe`). Os comandos rodam dentro do WSL2.

**B.1 — Identificação e kernel:**

```bash
lsb_release -a
uname -a
cat /etc/os-release | head -n 4
```

Esperado: `Ubuntu 22.04.x LTS`, `uname -r` contém `microsoft-standard-WSL2`.

**B.2 — CPU, RAM e disco vistos de dentro do WSL2:**

```bash
lscpu | grep -E "Model name|Socket|Core|Thread|Virtualization"
free -h
df -h /
df -h "$HOME"
```

Esperado: 32 threads visíveis, RAM perto do teto do `.wslconfig` (hoje ainda está em defaults do Win11 → ~64 GB; vai para 128 GB quando P1 for aplicada), virtualização `full` ou `VT-x`.

**B.3 — systemd, git, timezone:**

```bash
ps -p 1 -o pid,comm
sudo cat /etc/wsl.conf 2>/dev/null || echo "(wsl.conf nao existe)"
date
timedatectl 2>/dev/null | head -n 10
git --version
git config --global --get core.autocrlf
```

Esperado: `PID 1 = systemd` (se vier `init`, o systemd não está habilitado — pendência para o bloco de aplicação da P1). `git` presente. Se `core.autocrlf` vier vazio, aplicar a quente:

```bash
git config --global core.autocrlf input
```

**B.4 — GPU passthrough no WSL2:**

```bash
nvidia-smi
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

Esperado: as duas RTX 4090 visíveis, driver `591.86`, 2×24564 MiB. Valores devem bater com o `nvidia-smi.exe` do host.

**B.5 — NVIDIA Container Toolkit no WSL2:**

```bash
dpkg -l | grep -E "nvidia-container-toolkit|nvidia-docker" || echo "(toolkit ausente)"
nvidia-ctk --version 2>/dev/null || echo "(nvidia-ctk nao esta no PATH)"
```

O Docker Desktop já configura o runtime `nvidia` no daemon.json (confirmado em `docker info` na sessão de 16/abr). Esta verificação é redundância — se `nvidia-ctk` não estiver no PATH do Ubuntu mas o `docker run --gpus all` funciona, está OK.

**B.6 — Python e ferramentas de build:**

```bash
python3 --version
which pyenv && pyenv versions || echo "(pyenv nao instalado)"
dpkg -l build-essential curl jq unzip 2>/dev/null | grep ^ii | awk '{print $2, $3}'
```

Python 3.10 vem no Ubuntu 22.04 por padrão. O projeto usa `uv` e `pyproject.toml` (visto em `D:\Projetos\finanalytics_ai_fresh\`). Dentro do WSL2 pode-se instalar Python 3.11 via `pyenv` se quiser paridade com produção (Ubuntu Server).

**B.7 — (opcional) Teste de container GPU dentro do WSL2:**

Este comando agora roda no bash do WSL2 (diferente do `docker run` que fizemos no PowerShell):

```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

Esperado: mesmas duas GPUs, mesmos bus IDs. Confirma end-to-end: WSL2 Ubuntu → Docker Desktop → nvidia runtime → driver Windows → GPUs físicas. Se falhar aqui, há quebra na cadeia de passthrough.

**B.8 — Gerar `baseline.md` (Bloco 0 item 6 do `CLAUDE.md §17`):**

Dentro do repositório do projeto:

```bash
mkdir -p /mnt/d/Projetos/finanalytics_ai_fresh/docs
OUT=/mnt/d/Projetos/finanalytics_ai_fresh/docs/baseline_$(date +%Y%m%d_%H%M).md
{
  echo "# Baseline de ambiente — $(date -Iseconds)"
  echo
  echo "## WSL"
  powershell.exe -Command "wsl -l -v"
  powershell.exe -Command "wsl --version"
  echo
  echo "## GPU (WSL2)"
  nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv
  echo
  echo "## Ubuntu"
  lsb_release -a 2>/dev/null
  uname -a
  echo
  echo "## CPU / RAM / Disco (WSL2)"
  lscpu | grep -E "Model name|Socket|Core|Thread"
  free -h
  df -h / "$HOME"
  echo
  echo "## Docker"
  docker version 2>&1 | head -n 15
  docker info --format 'ServerVersion: {{.ServerVersion}} | OS: {{.OperatingSystem}} | Runtimes: {{.Runtimes}}' 2>&1
  echo
  echo "## Python"
  python3 --version 2>&1
} > "$OUT"
echo "Baseline gravado em: $OUT"
```

O arquivo `baseline_YYYYMMDD_HHMM.md` fica dentro do próprio repositório (via bind NTFS), versionável pelo git.

### Bloco C — 18/abr 07:00 BRT: última ingestão

Mesma regra do Bloco A. Deixar rodar. Acompanhar os logs. Nada destrutivo.

### Bloco D — 18/abr tarde em diante: `pg_dump` preventivo

**Gate:** só entra depois que o scheduler terminou, logs limpos, `wsl -l -v` e `docker ps` intactos.

**D.1 — Criar pasta e fazer dumps:**

```powershell
New-Item -ItemType Directory -Force -Path 'E:\FinAnalyticsAI\backups_manuais' | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmm'

# Dump Postgres principal (custom format, compressão embutida)
docker exec finanalytics_postgres pg_dump -U finanalytics -d finanalytics `
    --format=custom --compress=9 --file=/tmp/finanalytics_$stamp.dump

# Copiar para o host
docker cp finanalytics_postgres:/tmp/finanalytics_$stamp.dump `
    E:\FinAnalyticsAI\backups_manuais\finanalytics_$stamp.dump
docker exec finanalytics_postgres rm /tmp/finanalytics_$stamp.dump

# Mesmo para TimescaleDB (atenção: dumps de hypertables exigem flag especial)
docker exec finanalytics_timescale pg_dump -U finanalytics -d market_data `
    --format=custom --compress=9 --file=/tmp/market_data_$stamp.dump

docker cp finanalytics_timescale:/tmp/market_data_$stamp.dump `
    E:\FinAnalyticsAI\backups_manuais\market_data_$stamp.dump
docker exec finanalytics_timescale rm /tmp/market_data_$stamp.dump

Get-ChildItem 'E:\FinAnalyticsAI\backups_manuais\' |
    Select Name, @{N='Size_GB';E={[math]::Round($_.Length/1GB,2)}}, LastWriteTime |
    Format-Table -AutoSize
```

**D.2 — Considerações sobre TimescaleDB:**

Dump de hypertable com `pg_dump` custom format **funciona**, mas:

- Tempo de dump: ~5-15 min para 88 GB, indo para E:.
- Tamanho do dump custom: ~15-25 GB (compressão ~4:1 sobre dados já compressed pelo TSDB).
- Restore posterior exige recriar as hypertables com `create_hypertable()` antes de restaurar, ou usar `--no-tablespaces` + scripts dedicados do Timescale.

**Regra:** este dump é **segurança contra acidente**, não substitui o backup automatizado do container `finanalytics_backup`. Se em 19/abr algum `docker volume prune` erroneamente apagar o volume de 37 GB (`finanalytics_ai_fresh_pgdata`), o dump não ajuda porque esse volume é órfão; se algum comando afetar os dados reais em `E:\finanalytics_data\docker\`, aí o dump é o ripcord.

**D.3 — Opcional — arquivar o volume órfão antes de avaliar remoção:**

```powershell
# Criar tar do volume 'finanalytics_ai_fresh_pgdata' (37 GB)
# para o caso do conteúdo ser útil (snapshot histórico)
docker run --rm `
    -v finanalytics_ai_fresh_pgdata:/volume:ro `
    -v 'E:\FinAnalyticsAI\backups_manuais:/out' `
    alpine:latest `
    tar czf /out/volume_finanalytics_ai_fresh_pgdata_$(Get-Date -Format 'yyyyMMdd').tar.gz -C /volume .
```

Depois de confirmar que o tar tem tamanho razoável (> 0), pode remover o volume com `docker volume rm finanalytics_ai_fresh_pgdata`. Mas deixar essa remoção para **depois** de passar pelo item E.

### Bloco E — 19/abr: pacote de faxina

**Pré-requisitos:**

- Bloco D concluído (dumps em `E:\FinAnalyticsAI\backups_manuais\` validados).
- Ingestão dos dois dias anteriores confirmada.
- Logs do scheduler mostrando última execução OK.

**E.1 — Corrigir o compose (dual-GPU) — ver `proposta_decisao_15_dualgpu.md` §6.**

Editar `D:\Projetos\finanalytics_ai_fresh\docker-compose.override.yml`:

- Trocar `CUDA_VISIBLE_DEVICES: "1"` → `CUDA_VISIBLE_DEVICES: "0"` em `api`, `worker`, `scheduler`.
- Adicionar bloco `deploy.resources.reservations.devices` nos serviços que forem usar GPU no próximo sprint (não necessariamente todos agora — só o que o Sprint S03 requer).
- Atualizar Dockerfile para instalar `nvidia-utils-535` se você quiser `nvidia-smi` acessível dentro dos containers.

Aplicar com:

```powershell
cd D:\Projetos\finanalytics_ai_fresh
docker compose up -d --no-deps api worker scheduler
# ou, se alterou Dockerfile:
docker compose up -d --no-deps --build api worker scheduler
```

**E.2 — Limpar imagens `<none>` e build cache velho:**

```powershell
# Dry-run primeiro para saber o que vai sair
docker image prune --dry-run
docker builder prune --filter "until=90d" --filter "keep-storage=20GB" --dry-run

# Se os totais forem esperados (dezenas de GB), executar
docker image prune --filter "dangling=true" -f
docker builder prune --filter "until=90d" --filter "keep-storage=20GB" -f
```

**Não rodar** `docker system prune -a --volumes` (apaga volumes — risco P17).

**E.3 — (Decisão do usuário) remover o volume órfão `finanalytics_ai_fresh_pgdata`:**

Depois do arquivamento do Bloco D.3:

```powershell
docker volume inspect finanalytics_ai_fresh_pgdata  # confirmar LINKS = 0
docker volume rm finanalytics_ai_fresh_pgdata
```

Libera 37 GB no VHDX do `docker-desktop` em `C:`.

**E.4 — Consolidar worker vs worker_v2 (opcional, só se migração terminou):**

Se a migração para `event_worker_v2` estiver completa, remover o serviço `worker` do `docker-compose.yml` e fazer `docker rmi finanalytics-worker:latest` (libera 8,56 GB). **Fica como decisão do usuário** — não automatizar.

**E.5 — Remover o container `finanalytics_ohlc_ingestor` (quebrado desde 13/abr):**

```powershell
# Parar e remover o container
docker stop finanalytics_ohlc_ingestor
docker rm finanalytics_ohlc_ingestor

# Remover o serviço do docker-compose.yml (editar e deletar a sessão ohlc_ingestor)
# Committar no git
```

Scheduler já faz o trabalho; o container era redundância que quebrou.

**E.6 e E.7 agora formam uma janela consolidada de `wsl --shutdown` + reboot.** Como o usuário liberou reboot em 16/abr ~22h, podemos executar P1 + P12 em sequência sem voltar a abrir Docker Desktop entre elas, minimizando tempo de downtime dos containers. E.8 (P11 — RAM XMP) vai em janela separada mais tarde, por exigir BIOS.

**E.6 — Aplicar P1 (`.wslconfig`) — janela consolidada com E.7:**

Editar `C:\Users\marce\.wslconfig`:

```ini
[wsl2]
memory=128GB
processors=auto
swap=32GB
swapFile=E:\\WSL\\swap.vhdx
localhostForwarding=true
autoMemoryReclaim=gradual
sparseVhd=true
```

**Não aplicar ainda** — continue com E.7 antes do `wsl --shutdown`.

**E.7 — Migrar VHDX do Ubuntu‑22.04 ativo para `E:` (P12, parte 1):**

Operação: exporta a distro ativa para tarball, desregistra, reimporta a partir do tarball agora em `E:\WSL\Ubuntu-22.04\`. Isso move o VHDX (atualmente 9,59 GB em `C:\Users\marce\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu22.04LTS_...\LocalState\`) para `E:\WSL\Ubuntu-22.04\ext4.vhdx`.

**Preparação (sem downtime):**

```powershell
# PowerShell Admin
New-Item -ItemType Directory -Force -Path 'E:\WSL\Ubuntu-22.04' | Out-Null
New-Item -ItemType Directory -Force -Path 'E:\WSL\backups_distro' | Out-Null

# Antes de tocar, descobrir o usuario default atual (necessario para reconfigurar depois)
$defaultUser = wsl -d Ubuntu-22.04 --exec whoami
Write-Host "Usuario default atual: $defaultUser"
```

**Execução (downtime começa aqui; estime 5-15 min):**

```powershell
# PowerShell Admin
$stamp = Get-Date -Format 'yyyyMMdd_HHmm'

# 1) Parar TODOS os containers graciosamente antes do shutdown
cd D:\Projetos\finanalytics_ai_fresh
docker compose stop       # stop != down: preserva config, volumes, networks

# 2) Exportar a distro ativa (backup completo)
wsl --export Ubuntu-22.04 "E:\WSL\backups_distro\Ubuntu-22.04_antes_migracao_$stamp.tar"
# Esperar conclusão — exporta 9,59 GB para ~7-8 GB em tar

# 3) Desregistrar (apaga o VHDX antigo do C:)
wsl --unregister Ubuntu-22.04

# 4) Reimportar apontando para E:
wsl --import Ubuntu-22.04 'E:\WSL\Ubuntu-22.04' `
    "E:\WSL\backups_distro\Ubuntu-22.04_antes_migracao_$stamp.tar" --version 2

# 5) Restaurar usuario default (substitui 'marce' pelo valor de $defaultUser capturado antes)
wsl -d Ubuntu-22.04 -u root bash -c "echo -e '[user]\ndefault=$defaultUser' >> /etc/wsl.conf"
```

**E.7b — Migrar VHDX do `docker-desktop` para `E:` (P12, parte 2):**

Via UI do Docker Desktop (ainda não iniciar):

1. Confirmar Docker Desktop está **fechado** (ícone da tray deve estar cinza ou ausente).
2. Abrir Docker Desktop.
3. Settings (engrenagem) → Resources → Advanced → "Disk image location".
4. Mudar para `E:\Docker\wsl\`.
5. Clicar "Apply & restart". Docker Desktop vai copiar o VHDX do `docker-desktop` (atualmente ~181 GB em `C:\Users\marce\AppData\Local\Docker\wsl\main\`) para `E:`. **Demora estimada: 15–30 minutos.** Watch progresso no Task Manager (I/O no disco E:).

**E.6+E.7 — Finalização: aplicar `.wslconfig` e subir stack:**

```powershell
# Só agora aplicar o .wslconfig (conteúdo do E.6 acima, já gravado no arquivo)
wsl --shutdown
# Aguardar 10 segundos

# Reiniciar Docker Desktop (se ainda não reiniciou sozinho após E.7b)

# Validar
wsl -l -v
# Ubuntu-22.04 Running 2 (agora vivendo em E:\WSL\Ubuntu-22.04)
# docker-desktop Running 2 (vivendo em E:\Docker\wsl)

# Validar memoria disponivel — deve refletir ~128 GB agora
wsl -d Ubuntu-22.04 -- free -h

# Subir containers
cd D:\Projetos\finanalytics_ai_fresh
docker compose up -d

# Esperar healthchecks verdes (1–3 min)
docker ps --format "table {{.Names}}\t{{.Status}}" | Out-String -Width 500
```

**Ganho após E.7 + E.7b:** libera ~190 GB em `C:` (9,6 GB do VHDX Ubuntu + ~181 GB do VHDX docker-desktop). `C:` deve sair de 30,8% livre para algo como **55–60% livre**. E: reduz de 1.509 GB livres para ~1.320 GB livres.

**Rollback do E.7 (caso a reimportação dê errado):**

```powershell
# PowerShell Admin
wsl --unregister Ubuntu-22.04
wsl --import Ubuntu-22.04 "C:\Users\marce\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu22.04LTS_79rhkp1fndgsc\LocalState" `
    "E:\WSL\backups_distro\Ubuntu-22.04_antes_migracao_$stamp.tar" --version 2
```

O tar de backup em `E:\WSL\backups_distro\` é o ripcord. Não apagar até o ambiente novo em `E:` estar operacional e validado por alguns dias.

**E.8 — Aplicar P11 (RAM XMP/EXPO — em janela separada):**

Esta é a única mudança que exige **reboot físico + entrar no setup da BIOS**. Recomendo fazer **depois** que E.6+E.7 estiverem rodando estáveis por algumas horas. Motivo: você vai querer descartar variáveis — se algo der errado com XMP habilitado, sabe que não foi o `.wslconfig` ou o VHDX movido.

**Procedimento:**

1. Rebootar o Windows.
2. Ao ver a logo da Gigabyte, pressionar **Delete** repetidamente para entrar no setup da BIOS.
3. Navegar até **Tweaker** → **Extreme Memory Profile (X.M.P.)** (Gigabyte às vezes chama de "AMD EXPO" se for perfil EXPO, mas o kit Corsair é XMP).
4. Habilitar "Profile 1" (geralmente 5600 MHz CL40).
5. Save & Exit (F10). Máquina reinicia.

**Validação imediata após boot:**

- Se o Windows subir normalmente → ótimo. Rodar no PowerShell:

    ```powershell
    Get-CimInstance Win32_PhysicalMemory |
        Select-Object BankLabel, Speed, ConfiguredClockSpeed |
        Format-Table -AutoSize
    ```

    Esperado: `ConfiguredClockSpeed` próximo ou igual ao Speed rated (5200–5600 MHz em vez dos 4000 atuais).

- Se o Windows **não** subir (tela preta, BSOD no boot, travamento no logo da motherboard):
    - A BIOS do Z790 AORUS XTREME X tem safety de fallback (Memory Flash Button no rear I/O em alguns modelos, ou CMOS clear).
    - Entrar no setup novamente pelo Delete. Se travar, use o botão CMOS Clear na traseira (segurar 5 seg) para voltar aos defaults.
    - Com XMP desabilitado, RAM volta aos 4000 MHz safe. Windows sobe.
    - Aí testar perfis mais conservadores: 5200 MHz ou 5400 MHz em vez de 5600, ou aumentar `VDIMM` para 1,35–1,40 V manualmente.

**Memtest86 — opcional mas recomendado após sucesso inicial:**

Com XMP habilitado e Windows estável, rodar MemTest86 por pelo menos 4 horas (ideal: uma passada completa de 12 horas overnight) para confirmar estabilidade. Baixar de `memtest86.com`, criar pen drive bootável. 192 GB × 1 passada ≈ 2-4 horas por teste.

Se passar MemTest86 → **P11 resolvida**, ganho de ~40% de bandwidth de RAM efetiva.

### Bloco F — 19/abr noite ou 20/abr: reescrever `§14`

Se você concordar com o draft em `proposta_claude_md_v14_novo.md`:

1. Copiar o conteúdo da seção `## 14. Estado Atual do Projeto` do draft para o `CLAUDE.md` canônico (`Melhorias/` ou `D:\Projetos\finanalytics_ai_fresh\`, conforme decisão de fonte de verdade).
2. Aplicar também a **Decisão 15** em `§3`.
3. Commit: `git commit -m "docs: consolidar §14 e Decisão 15 após auditoria de 16/abr"`.
4. Marcar no `verificacao_hardware_finanalyticsai.md` P17 e P22 como resolvidas.

---

## Checkpoints de controle

Para cada bloco, o "está ok?" é:

| Bloco | Checkpoint |
|---|---|
| A (17/abr 07:00) | Último log do scheduler contém `scheduler.ohlcv.next` |
| B (17/abr tarde) | Arquivo `baseline_YYYYMMDD_HHMM.md` gerado no repo |
| C (18/abr 07:00) | Mesmo que A para segunda execução |
| D (18/abr pós-ingestão) | 2 arquivos `.dump` em `E:\FinAnalyticsAI\backups_manuais\` com tamanhos esperados |
| E.1–E.5 (19/abr manhã) | Compose atualizado + git commit; imagens `<none>` e build cache antigo limpos |
| E.6+E.7+E.7b (19/abr tarde) | `.wslconfig` ativo, VHDX Ubuntu em `E:\WSL\Ubuntu-22.04\`, VHDX docker-desktop em `E:\Docker\wsl\`; `wsl -l -v` mostra Ubuntu‑22.04 + docker-desktop rodando; containers saudáveis em `docker ps` |
| E.8 (19/abr noite OU 20/abr) | XMP/EXPO habilitado na BIOS; `ConfiguredClockSpeed` ≥ 5200 MHz; MemTest86 aprovado |
| F (20/abr+) | `CLAUDE.md §14` reescrito; P17 e P22 marcadas como resolvidas; documentos sincronizados |

---

## O que fazer se algo quebrar no meio do caminho

**Se o scheduler 17/abr ou 18/abr falhar:**

- `docker logs finanalytics_scheduler --tail 200` — salvar o output em arquivo.
- `docker logs finanalytics_postgres --tail 50`
- `docker logs finanalytics_timescale --tail 50`
- Não reiniciar o scheduler — o que não foi ingerido hoje será re-tentado amanhã (scheduling diário). Se o erro for terminal, reagendar uma execução manual depois.

**Se o `pg_dump` do bloco D falhar:**

- Checar espaço em `E:`.
- Checar se o DB está `ready` (healthcheck verde).
- Tentar dump parcial por schema: `pg_dump -U finanalytics -d finanalytics --schema=public --format=custom`.
- Em último caso: dump plano texto (`--format=plain`) e recomprimir fora: `gzip -9`.

**Se a aplicação da P1 (wsl --shutdown) quebrar containers:**

- Docker Desktop vai sozinho reiniciar os containers quando voltar. Se algum ficar em estado "Exited", `docker compose up -d` na raiz do repo resolve.
- Se o VHDX do `docker-desktop` estiver corrompido por algum motivo (improvável), o `E:\FinAnalyticsAI\backups_manuais\*.dump` do bloco D é a rede de segurança — restaurar em containers limpos.

**Se algo der muito errado:**

Parar tudo e abrir uma nova sessão de diagnóstico. Tudo que foi feito até aqui está documentado:

- `verificacao_hardware_finanalyticsai.md` (estado atual e pendências numeradas P1-P22)
- `proposta_claude_md_v14_novo.md` (draft da §14 consolidada)
- `proposta_decisao_15_dualgpu.md` (Decisão 15 + base técnica)
- Este arquivo (`plano_proximos_passos_17-19abr.md`)

Com esses quatro documentos qualquer sessão futura (humana ou IA) consegue retomar onde paramos sem perder contexto.
