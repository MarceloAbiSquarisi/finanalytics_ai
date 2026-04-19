# Verificação de Hardware — FinAnalyticsAI (Bloco 0 Pré-flight)

Baseado no `CLAUDE.md v1.3`, seções 2, 10 e Bloco 0 (item 1, 5 e 6).
Data: 16/abr/2026. Host: Windows 11 Pro + WSL2 Ubuntu 22.04.

## Atualização 16/abr/2026 noite — reboot liberado

O usuário confirmou em 16/abr (~22h BRT) que **reboot da estação de dev está autorizado** a partir de agora. Isso desbloqueia as pendências que estavam em fila esperando janela de manutenção:

- **P1** — preencher o `.wslconfig` vazio (requer `wsl --shutdown`)
- **P11** — habilitar XMP/EXPO para a RAM sair de 4000 MHz em direção ao rating 5600 CL40 (requer reboot + acesso à BIOS)
- **P12** — migrar os VHDXes de `C:` para `E:` (requer `wsl --shutdown` e recriação da distro via `wsl --export`/`--import`)

**A regra de "nada destrutivo" continua aplicando até 18/abr ~08:00 BRT** (depois da segunda execução do scheduler). Reboot não é "destrutivo" no sentido de apagar dados, mas interrompe ingestão em curso — então nenhum dos três itens acima pode ser feito durante as janelas do scheduler às 07:00 BRT. O corredor livre fica 18/abr após 08:00 em diante.

Ver `plano_proximos_passos_17-19abr.md` para o escalonamento atualizado.

## Resultados já coletados (16/abr/2026)

| Componente | Valor medido | Bate com CLAUDE.md §2? |
|---|---|---|
| OS | Windows 11 Pro **25H2**, build **26200.8037**, canal GA | ok |
| Motherboard | Gigabyte **Z790 AORUS XTREME X** (Intel LGA 1700, DDR5, PCIe 5.0) | preencher §2 |
| CPU | Intel **Core i9‑14900K** — 24C/32T (8P+16E), base 3,2 GHz, L3 36 MB | preencher §2 |
| RAM física | **192 GB** DDR5 (4 × Corsair `CMH96GX5M2B5600C40` 48 GB) | **diverge** — §2 diz 196 GB → corrigir para 192 GB |
| RAM — clock efetivo | **4000 MHz** (JEDEC 4800, kit rated 5600 CL40) | ver pendência P11 |
| RAM disponível ao Windows | 191,8 GB visível / 111,8 GB livre (58%) | ok |
| Hipervisor | `HyperVisorPresent = True` (Hyper‑V/WSL2 ativos) | ok |
| VT‑x / EPT (leitura direta) | `False` em `Win32_Processor` — artefato do hipervisor ativo, não problema real | ok |
| Canal Windows | GA (`WindowsSelfHost\UI\Selection` vazio) | ok |
| NVMe | Corsair **MP700 PRO** 2 TB (PCIe **Gen5**, `Healthy/OK`) + 2× Redragon Blaze 1 TB (Gen3, `Healthy/OK`) | preencher §2 |
| GPU | **2× NVIDIA GeForce RTX 4090** — 2 × 24 564 MiB = **48 GB VRAM total**. GPU 0 sem monitor (dedicada, 0 MiB usados). GPU 1 com display (2 087 MiB ocupados por ~35 processos de desktop). Driver **591.86**, CUDA reportada **13.1**, compute cap 8.9 (Ada) | **§2 diz 1 GPU — corrigir para dual-GPU** |
| Volume `C:` (Windows + VHDX WSL2 hoje) | 874 GB, **195 GB livres = 22,3%** — muito perto do gatilho de alerta (20%) | ver pendência P13 |
| Volume `D:` (pasta `Melhorias/`) | 954 GB, 391 GB livres (41%) | ok |
| Volume `E:` (Corsair MP700 PRO Gen5) | 1863 GB, **1611 GB livres (86,5%)** — drive ideal para dados de treino | ok |
| `ext4.vhdx` do WSL2 | **9,59 GB** hoje, hospedado no `C:` (drive Gen3 + quase cheio) | ver pendência P12 |
| Distros WSL instaladas | 6 total: `Ubuntu-22.04` (ativa) + `docker-desktop` (backend) + 4 paradas (`GPU_MercadoFinanceiro`, `TF-GPU`, `GPU_OTIMIZADA`, `Ubuntu`) — cada uma com seu VHDX em `C:` | ver pendência P14 |
| Versão do WSL | `2.4.13.0` store-based, kernel `5.15.167.4-1`, WSLg `1.0.65` | ok |
| `vmmemWSL` em idle | **27,71 GB WorkingSet / 39,74 GB PrivateMem** — consistente com defaults do Win11 (teto ~64 GB) | ok funcional; otimizar via `.wslconfig` |

Ações diretas resultantes destes achados:

- **Corrigir §2 do CLAUDE.md:** `RAM: 196 GB DDR5` → `RAM: 192 GB DDR5 (4× Corsair CMH96GX5M2B5600C40, rated 5600 CL40)`.
- **Preencher §2 do CLAUDE.md:** `CPU: Intel Core i9‑14900K (24C/32T, L3 36 MB)`, `Motherboard: Gigabyte Z790 AORUS XTREME X`, `OS: Windows 11 Pro 25H2 build 26200.8037`.
- **Reescrever a Seção 2 (GPU) e revisitar a Seção 3 (decisões imutáveis):** a estação tem **2× RTX 4090 = 48 GB VRAM total**, não 1×24 GB. GPU 0 (sem monitor) é a dedicada para compute; GPU 1 (com display) serve o desktop e ~35 processos Windows. Implicações:
  - Regra "NUNCA treinar e inferir ao mesmo tempo" (§2) deixa de ser necessária em geral — dá para treinar na GPU 0 e inferir na GPU 1 com isolamento por `CUDA_VISIBLE_DEVICES`. Continua valendo como regra para uma mesma GPU.
  - "Limite seguro 20 GB VRAM total" (§2) foi calibrado para single-GPU. Sugestão: **20 GB por GPU, 40 GB total**, mantendo 4 GB de headroom em cada placa.
  - Decisão 3 (FinGPT-8B GGUF q4_K_M) continua ótima por latência, mas agora 13B FP16 ou 70B Q4_K_M passam a caber quando necessários.
  - Decisão 4 (Llama 70B só off-hours IQ2_M) pode ser revista — 70B Q4_K_M (~40 GB) cabe isoladamente na GPU 0 durante off-hours.
  - `CUDA_VISIBLE_DEVICES=0` (§9) continua o default correto: compute crítico na GPU limpa. Para treino distribuído, passa a existir a opção `0,1`.
  - **Serving de produção nunca deve rodar na GPU 1** — Chrome, Edge, Kaspersky e afins podem preempter/desestabilizar inferência crítica em horário de mercado.

### Histórico de instabilidade dual-GPU (documentar na §3 do CLAUDE.md)

O usuário relatou: "no início usava as duas GPUs e isso fazia a máquina travar e rebotar; por isso deixei só para treinamento e outra para os monitores".

**Diagnóstico provável:** a assinatura "reboot imediato sem BSOD" é clássica de proteção da fonte (OCP) disparando sob **transientes de ~1,5–2× TDP** que o RTX 4090 produz em microssegundos sob carga de compute. Dois 4090 em compute simultâneo podem somar picos de 1.400–1.800 W em alguns ms, excedendo a reserva de transiente de PSUs pré-ATX 3.0. Não é bug de driver — por isso não existe "patch NVIDIA" que resolva o fenômeno físico. O que mudou ao longo do tempo foi o ecossistema (PSUs ATX 3.0/3.1 com reserva de transiente dedicada, conector 12V-2×6 revisado), não os drivers.

**Como a configuração atual evita o problema:** com CUDA_VISIBLE_DEVICES=0 (§9 do CLAUDE.md), toda carga pesada fica na GPU 0, enquanto a GPU 1 roda apenas tráfego leve de desktop. Os transientes **não se somam** porque só uma GPU entra em regime de compute pesado por vez. Esta é exatamente a operação atualmente em curso e deve ser mantida como padrão dos Sprints S01–S24 — nenhum modelo do roadmap exige mais de 24 GB de VRAM.

**Quando o risco reapareceria:** apenas se houver intenção de explorar os 48 GB combinados em compute simultâneo (ex.: LLM 70B split entre as duas placas, treino distribuído 2-GPU, `device_map='auto'` do HuggingFace). Antes de tentar:

1. Confirmar PSU ≥ 1.500 W ATX 3.0 Platinum (idealmente 1.600 W Titanium) com conectores nativos 12V-2×6 — sem adaptador 4×8-pin → 12VHPWR.
2. Aplicar power cap via `nvidia-smi.exe -i 0 -pl 380` e `-i 1 -pl 380` (cap a ~84% do TDP derruba a maior parte dos transientes, com perda < 5% de desempenho). Comando aplica em segundos, reversível, sem reboot.
3. Monitorar `nvidia-smi dmon -s pucvmet` durante cargas pesadas para ver se temperatura/power ficam estáveis.

**Sugestão de nova Decisão Imutável para o CLAUDE.md §3:**

> **Decisão 15:** dual-GPU em compute simultâneo só com power limit ≤ 380 W por placa (`nvidia-smi -pl 380`) E PSU ATX 3.0 ≥ 1.500 W confirmada. Enquanto a PSU instalada não for validada, toda carga pesada fica restrita à GPU 0 via `CUDA_VISIBLE_DEVICES=0`.

**Item investigativo (não-bloqueante, a quente):** identificar marca/modelo/rating da PSU instalada e registrar em §2 do CLAUDE.md. Via BIOS raramente aparece; caminho mais confiável é foto da etiqueta na lateral da PSU em janela de manutenção.

> **Regra operacional desta rodada:** o hardware **NÃO pode ser reiniciado** agora.
> Qualquer correção que exija `wsl --shutdown`, restart do Windows, troca de
> driver NVIDIA ou habilitação de recurso do Windows vai para a seção
> **"Pendências — requerem reinício"** no final. Só rodar diagnóstico e,
> onde possível, correções a quente.

Convenção de prefixos (mesma do CLAUDE.md, Seção 10):

- `# [Windows host]` → executar no **PowerShell** do Windows
- sem prefixo → executar no **bash do WSL2** (Ubuntu 22.04)

---

## 1. Identificação do host Windows

```powershell
# [Windows host] — build do Windows 11 Pro
winver                                                # abre diálogo
Get-ComputerInfo | Select-Object `
    WindowsProductName, WindowsVersion, OsBuildNumber, `
    OsHardwareAbstractionLayer, CsManufacturer, CsModel

# [Windows host] — arquitetura e virtualização habilitada na BIOS
Get-ComputerInfo | Select-Object `
    CsSystemType, HyperVRequirementVirtualizationFirmwareEnabled, `
    HyperVRequirementVMMonitorModeExtensions, `
    HyperVRequirementSecondLevelAddressTranslation
```

**O que procurar:**
- `WindowsProductName` → `Windows 11 Pro`
- `HyperVRequirementVirtualizationFirmwareEnabled` → `True` (se `False`, habilitar VT-x/AMD-V na BIOS — **PENDÊNCIA**, requer reboot)

---

## 2. CPU e RAM do host (itens abertos na Seção 2 do CLAUDE.md)

```powershell
# [Windows host] — CPU (atualizar Seção 2 do CLAUDE.md com estes dados)
Get-CimInstance Win32_Processor | Select-Object `
    Name, NumberOfCores, NumberOfLogicalProcessors, `
    MaxClockSpeed, L3CacheSize, Manufacturer

# [Windows host] — RAM física instalada (deve somar ~196 GB)
Get-CimInstance Win32_PhysicalMemory | Select-Object `
    BankLabel, @{N='Capacity_GB';E={[math]::Round($_.Capacity/1GB,0)}}, `
    Speed, ConfiguredClockSpeed, Manufacturer, PartNumber
"{0:N1} GB" -f ((Get-CimInstance Win32_PhysicalMemory | `
    Measure-Object Capacity -Sum).Sum / 1GB)

# [Windows host] — memória atualmente disponível
Get-CimInstance Win32_OperatingSystem | Select-Object `
    @{N='TotalVisible_GB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}}, `
    @{N='FreePhysical_GB';E={[math]::Round($_.FreePhysicalMemory/1MB,1)}}
```

**Esperado (CLAUDE.md §2):** DDR5, total ≥ 196 GB. Se o total diferir, atualizar a Seção 2 e a Seção 14 (Estado Atual).

---

## 3. Armazenamento NVMe (item aberto na Seção 2)

```powershell
# [Windows host] — modelos e saúde dos SSDs NVMe
Get-PhysicalDisk | Where-Object MediaType -eq 'SSD' | Select-Object `
    FriendlyName, MediaType, BusType, `
    @{N='Size_GB';E={[math]::Round($_.Size/1GB,0)}}, `
    HealthStatus, OperationalStatus

# [Windows host] — espaço livre por volume
Get-Volume | Where-Object DriveLetter | Select-Object `
    DriveLetter, FileSystemLabel, FileSystem, `
    @{N='Size_GB';E={[math]::Round($_.Size/1GB,0)}}, `
    @{N='Free_GB';E={[math]::Round($_.SizeRemaining/1GB,0)}}, `
    @{N='Free_%';E={[math]::Round($_.SizeRemaining/$_.Size*100,1)}}
```

**Critério:** `Free_%` < 20% dispara alerta (CLAUDE.md §17, item 11). Se o volume do WSL2 (`ext4.vhdx`) estiver ficando cheio, ver seção 11 deste documento.

---

## 4. GPU pelo lado Windows (driver NVIDIA 535+)

```powershell
# [Windows host] — driver, VRAM e utilização
nvidia-smi.exe

# [Windows host] — versão completa do driver
nvidia-smi.exe --query-gpu=driver_version,name,memory.total,memory.free,memory.used,compute_cap `
               --format=csv

# [Windows host] — verifica compatibilidade CUDA do driver (linha "CUDA Version:")
nvidia-smi.exe | Select-String "CUDA Version"
```

**Critérios (CLAUDE.md §2 e §3):**
- `name` contém `RTX 4090`
- `memory.total` ≈ `24564 MiB`
- `driver_version` ≥ `535.xx`
- linha `CUDA Version:` ≥ `12.1`

**Se driver < 535:** atualização de driver NVIDIA entra como **PENDÊNCIA** (exige reboot do Windows).

---

## 5. WSL2 — status da distro

```powershell
# [Windows host] — lista distros e versão WSL
wsl --status
wsl -l -v
wsl --version
```

**Esperado:**
- `Ubuntu-22.04` com `STATE = Running` e `VERSION = 2`
- `WSL version` ≥ 2.0 (idealmente 2.x store-based)
- `Default Distribution: Ubuntu-22.04`

```powershell
# [Windows host] — memória consumida pela VM do WSL2 (Hyper-V utility VM)
Get-Process vmmemWSL -ErrorAction SilentlyContinue | Select-Object `
    Name, @{N='WorkingSet_GB';E={[math]::Round($_.WorkingSet64/1GB,2)}}, `
    @{N='PrivateMem_GB';E={[math]::Round($_.PrivateMemorySize64/1GB,2)}}
# Em builds antigos o processo chama-se apenas "vmmem".
Get-Process vmmem    -ErrorAction SilentlyContinue | Select-Object `
    Name, @{N='WorkingSet_GB';E={[math]::Round($_.WorkingSet64/1GB,2)}}
```

---

## 6. Conteúdo do `.wslconfig` (CLAUDE.md §2)

```powershell
# [Windows host] — confere limites atuais do WSL2
$cfg = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $cfg) {
    Write-Host "Encontrado: $cfg"
    Get-Content $cfg
} else {
    Write-Warning "Arquivo $cfg NAO existe — WSL2 esta usando defaults."
}
```

**Esperado (CLAUDE.md §2):**

```ini
[wsl2]
memory=160GB
processors=auto
swap=32GB
```

**Se ausente ou divergente:** criar/editar o arquivo entra como **PENDÊNCIA** — a mudança só se aplica após `wsl --shutdown`, que reinicia todas as distros e mata os containers Docker.

---

## 7. Recursos do Windows necessários (WSL2 + Hyper-V + VMP)

```powershell
# [Windows host] — precisa PowerShell como Administrador
Get-WindowsOptionalFeature -Online -FeatureName `
    Microsoft-Windows-Subsystem-Linux,            `
    VirtualMachinePlatform,                       `
    Microsoft-Hyper-V-All |                       `
    Select-Object FeatureName, State
```

**Esperado:** os três com `State = Enabled` (CLAUDE.md §2). Habilitar/desabilitar qualquer um é **PENDÊNCIA** — exige reboot.

---

## 8. Docker Desktop (backend WSL2)

```powershell
# [Windows host] — servico rodando?
Get-Service com.docker.service, com.docker.backend -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType

# [Windows host] — cliente e engine
docker version
docker info --format 'Server Version: {{.ServerVersion}} | OS: {{.OperatingSystem}} | Runtimes: {{.Runtimes}}'
```

**Critérios:**
- Docker Desktop ≥ `4.26`
- `docker info` lista runtime `nvidia` (senão, NVIDIA Container Toolkit não está instalado corretamente no WSL2)
- Sem erros "Cannot connect to the Docker daemon"

---

## 9. Validação do ambiente WSL2 (bash do Ubuntu 22.04)

Todos os comandos desta seção rodam no bash do WSL2.

### 9.1. Identificação da distro e kernel

```bash
lsb_release -a
cat /etc/os-release | head -n 4
uname -a              # kernel WSL2 (deve conter "-microsoft-")
```

**Esperado:** `Ubuntu 22.04.x LTS`; `uname -r` contém `microsoft-standard-WSL2`.

### 9.2. CPU, RAM e disco vistos de dentro do WSL2

```bash
lscpu | grep -E "Model name|Socket|Core|Thread|CPU MHz|Virtualization"
free -h
df -h /
df -h "$HOME"          # mesmo FS do VHDX do WSL2
# Tamanho real do vhdx (pelo lado Windows, chamar do bash via interop):
powershell.exe -Command "Get-ChildItem \$env:LOCALAPPDATA\Packages\CanonicalGroupLimited* -Recurse -Filter ext4.vhdx | Select FullName, @{N='GB';E={[math]::Round(\$_.Length/1GB,2)}}"
```

**Atenção:** `free -h` reflete o teto do `.wslconfig`. Se apenas ~8 GB aparecerem com 196 GB físicos, o `.wslconfig` está ausente/errado (ver seção 6).

### 9.3. systemd habilitado?

```bash
# Se systemd estiver ativo, PID 1 e' systemd
ps -p 1 -o pid,comm
# Conteudo atual de wsl.conf
sudo cat /etc/wsl.conf 2>/dev/null || echo "(/etc/wsl.conf nao existe)"
```

**Esperado (CLAUDE.md §2):** `PID 1 = systemd`, e `/etc/wsl.conf` contém:

```ini
[boot]
systemd=true
```

**Se faltar:** editar `wsl.conf` é trivial, mas a mudança **só entra com `wsl --shutdown`** → **PENDÊNCIA**.

### 9.4. GPU dentro do WSL2 (passthrough)

```bash
# Driver NVIDIA via passthrough
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.free,memory.total --format=csv,noheader

# CUDA disponivel no Ubuntu
ls /usr/local/ | grep cuda || echo "(sem toolkit CUDA nativo — OK, usamos via Docker/PyTorch)"
nvcc --version 2>/dev/null || echo "(nvcc nao instalado — OK se for usar CUDA via imagem Docker)"
```

**Cross-check crítico (CLAUDE.md §2, §7):** VRAM total reportada aqui deve ser igual à reportada por `nvidia-smi.exe` no Windows. Se o WSL2 não enxergar a GPU, o problema normalmente é driver Windows < 535 → **PENDÊNCIA**.

### 9.5. NVIDIA Container Toolkit no WSL2

```bash
dpkg -l | grep -E "nvidia-container-toolkit|nvidia-docker" || echo "(pacote ausente)"
nvidia-ctk --version 2>/dev/null || echo "(nvidia-ctk nao esta no PATH)"
cat /etc/docker/daemon.json 2>/dev/null || echo "(daemon.json nao existe)"
```

**Esperado:** `nvidia-container-toolkit` instalado; `daemon.json` (se configurado) lista `"runtimes": { "nvidia": ... }`.

### 9.6. Teste real de GPU via container Docker (Bloco 0, item 5)

```bash
# NAO requer reboot. Baixa imagem CUDA 12.1 e roda nvidia-smi dentro do container.
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

**Resultado esperado:** mesma GPU (`RTX 4090`, 24 GB) que `nvidia-smi` do host e do WSL2. Se falhar com "could not select device driver", o runtime `nvidia` não está registrado no Docker Desktop → ver seção 12.

### 9.7. Ferramentas de trabalho

```bash
git --version
python3 --version                 # espera-se >= 3.11 (Bloco 0 item 3)
which pyenv && pyenv versions || echo "(pyenv nao instalado)"
dpkg -l build-essential curl jq unzip 2>/dev/null | grep ^ii

# Regra do CLAUDE.md §2 (Notas de ambiente)
git config --global --get core.autocrlf || echo "(core.autocrlf NAO configurado — precisa ser 'input')"
```

### 9.8. PyTorch + CUDA (só se o venv/Python do projeto ja existir)

```bash
# Rodar dentro do venv do projeto
python - <<'PY'
try:
    import torch
    print("torch           :", torch.__version__)
    print("cuda available  :", torch.cuda.is_available())
    print("cuda version    :", torch.version.cuda)
    print("cudnn           :", torch.backends.cudnn.version())
    if torch.cuda.is_available():
        print("device 0        :", torch.cuda.get_device_name(0))
        print("vram total (GB) :", round(torch.cuda.get_device_properties(0).total_memory/1024**3, 2))
        print("flash_attn_2    :", torch.backends.cuda.flash_sdp_enabled())
except ImportError:
    print("torch nao instalado — OK se ainda estivermos no Bloco 0.")
PY
```

---

## 10. Relógio do WSL2 e permissão do socket Docker (correções a quente do CLAUDE.md §11)

Diagnóstico:

```bash
# Relogio (erros de TLS/JWT aparecem se >30s fora)
date -u
timedatectl 2>/dev/null | grep -i "System clock"

# Permissao do socket
ls -l /var/run/docker.sock
id -nG "$USER" | tr ' ' '\n' | grep -x docker || echo "(usuario NAO esta no grupo docker)"
```

Correções aceitáveis agora (sem reboot de hardware):

```bash
# Resync do relogio do WSL2 (seguro, nao reinicia nada)
sudo hwclock -s

# Entrar no grupo docker sem deslogar (CLAUDE.md §11)
sudo usermod -aG docker "$USER"
newgrp docker          # afeta apenas a shell atual
```

---

## 11. Estado da árvore do projeto (CLAUDE.md §6 e Bloco 0 item 2)

```bash
# Raiz do projeto precisa estar no FS nativo do WSL2, NAO em /mnt/c
ls -ld ~/finanalyticsai 2>/dev/null || echo "(~/finanalyticsai nao existe — criar no Bloco 0)"
[ -f ~/finanalyticsai/CLAUDE.md ] && echo "CLAUDE.md presente" || echo "CLAUDE.md AUSENTE no repo"
[ -f ~/finanalyticsai/.env ]      && echo ".env presente"      || echo ".env AUSENTE"
[ -f ~/finanalyticsai/.env.example ] && echo ".env.example ok" || echo ".env.example AUSENTE"

# Confirma que NADA de dados de treino caiu em /mnt/c/... por engano
find /mnt/c -maxdepth 4 -type d -iname "finanalyticsai*" 2>/dev/null | head
```

**Se o repo ainda não existe:** criar localmente no WSL2 (não requer reboot).
**Se existe em `/mnt/c/...`:** violação da regra NUNCA do §7 — mover para `~/finanalyticsai/` (ação a quente, sem reboot).

---

## 12. Containers de infra do projeto (CLAUDE.md §5 e §10)

```bash
# Sanity check geral
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker ps -a --filter "name=finai" --format "table {{.Names}}\t{{.Status}}"

# Portas que o CLAUDE.md reserva (§5). Nada rodando ainda no Sprint S01,
# mas util para confirmar que as portas nao estao tomadas por outro app.
for p in 5432 6379 9092 9093 9094 9000 9001 8000 8080 5000 8090 3000 9090; do
    ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN \
        && echo "PORTA $p: EM USO (quem esta ouvindo?)" \
        || echo "PORTA $p: livre"
done
```

---

## 13. Relatório consolidado (gerar em `~/finanalyticsai/docs/baseline.md` — Bloco 0 item 6)

Script de uma via, só leitura, salva tudo num arquivo para documentar o estado antes de qualquer mudança:

```bash
mkdir -p ~/finanalyticsai/docs
OUT=~/finanalyticsai/docs/baseline_$(date +%Y%m%d_%H%M).md
{
  echo "# Baseline de ambiente — $(date -Iseconds)"
  echo
  echo "## Host (via interop)"
  powershell.exe -Command "(Get-ComputerInfo | Select WindowsProductName,OsBuildNumber,CsManufacturer,CsModel | Format-List | Out-String).Trim()"
  echo
  echo "## WSL"
  powershell.exe -Command "wsl -l -v"
  powershell.exe -Command "wsl --version"
  echo
  echo "## GPU (WSL2)"
  nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv
  echo
  echo "## Ubuntu"
  lsb_release -a 2>/dev/null
  uname -a
  echo
  echo "## CPU/RAM/Disco (WSL2)"
  lscpu | grep -E "Model name|Socket|Core|Thread"
  free -h
  df -h / "$HOME"
  echo
  echo "## Docker"
  docker version 2>&1
  echo
  echo "## Python"
  python3 --version 2>&1
} > "$OUT"
echo "Baseline gravado em: $OUT"
```

Este relatório alimenta diretamente a atualização pendente da **Seção 2 (hardware)** e da **Seção 14 (Estado Atual)** do `CLAUDE.md`.

---

## 14. Pendências — requerem reinício (NÃO executar agora)

O hardware não pode ser reiniciado nesta janela. Tudo que se segue fica
**pendente** e deve ser agendado em janela de manutenção. Criar um ticket/linha
na Seção 14 do `CLAUDE.md` para cada item confirmado pelo diagnóstico acima.

| # | Condição detectada | Ação necessária | Tipo de reinício |
|---|---|---|---|
| P1 | **CONFIRMADA 16/abr/2026.** `C:\Users\marce\.wslconfig` existe mas está **vazio** (0 bytes, criado em 24/fev/2026). WSL2 roda com defaults do Windows 11 (teto ~64 GB de RAM). | Preencher o arquivo com conteúdo abaixo. Swap apontado para `E:` (Gen5) e `sparseVhd=true` para não reservar espaço à frente. Depois do save, aplicar via `wsl --shutdown`: ```ini\n[wsl2]\nmemory=128GB\nprocessors=auto\nswap=32GB\nswapFile=E:\\WSL\\swap.vhdx\nlocalhostForwarding=true\nautoMemoryReclaim=gradual\nsparseVhd=true\n``` | `wsl --shutdown` (derruba todas as distros e containers) |
| P2 | `/etc/wsl.conf` sem `[boot] systemd=true` ou `ps -p 1` não mostra `systemd` | `sudo tee /etc/wsl.conf` com o bloco correto | `wsl --shutdown` |
| P3 | Driver NVIDIA host < 535 ou incompatível com CUDA 12.1 | Baixar driver NVIDIA ≥ 535 (Game Ready ou Studio), "clean install" | **reboot do Windows** |
| P4 | `nvidia-smi` do WSL2 não enxerga GPU apesar do driver ok | Atualizar/reinstalar Docker Desktop + reiniciar WSL2 | `wsl --shutdown` + restart Docker Desktop |
| P5 | ~~Recursos do Windows ausentes~~ | **Resolvido:** verificação em 16/abr/2026 confirmou os três com `State = Enabled` (WSL, VirtualMachinePlatform, Hyper-V-All). | — |
| P6 | Virtualização (`HyperVRequirementVirtualizationFirmwareEnabled = False`) | Habilitar VT-x/AMD-V + SVM no setup da BIOS | **reboot + acesso à BIOS** |
| P7 | Build do Windows 11 muito antigo para WSL2 GPU passthrough | Windows Update cumulativo | **reboot do Windows** |
| P8 | NVIDIA Container Toolkit instalado, mas `docker run --gpus all ...` falha | Reinstalar toolkit + `sudo systemctl restart docker` (não resolve sem systemd ativo, cai em P2) | pode exigir `wsl --shutdown` |
| P9 | `.env.example` ausente e `.env` commitado por engano | Limpar histórico do git (`git filter-repo`), rotacionar quaisquer segredos expostos | sem reboot, mas rotação de credenciais é obrigatória |
| P10 | Volume do VHDX do WSL2 com `Free_%` < 20% | `wsl --shutdown` + `Optimize-VHD` ou `diskpart compact` | `wsl --shutdown` |
| P11 | RAM rodando a 4000 MHz com kit rated 5600 CL40 (4 DIMMs DDR5 em LGA 1700) | Habilitar XMP/EXPO na BIOS do Z790 AORUS XTREME X. Se 5600 MHz for instável com 4 DIMMs, cair para 5200 ou 5400 MHz com aumento controlado de `VDIMM`/`VDD2`/`VCCSA`. Rodar `MemTest86` 4 horas após ajuste. | **reboot + acesso à BIOS** |
| P12 | `ext4.vhdx` do WSL2 está em `C:` (Redragon Blaze Gen3, 22,3% livre). O drive certo é `E:` (Corsair MP700 PRO Gen5, 1,6 TB livres). Além disso, o CLAUDE.md §2 proíbe dados de treino em `/mnt/c`, então todo o crescimento do projeto vai engordar esse VHDX. Deixar em `C:` estrangula I/O e enche o Windows drive. | Migrar a distro para `E:\WSL\Ubuntu-22.04\`: `wsl --shutdown` → `wsl --export Ubuntu-22.04 E:\WSL\ubuntu2204.tar` → `wsl --unregister Ubuntu-22.04` → `wsl --import Ubuntu-22.04 E:\WSL\Ubuntu-22.04 E:\WSL\ubuntu2204.tar --version 2` → reconfigurar default user via `/etc/wsl.conf` (`[user] default=<usuario>`) → `wsl --shutdown` para aplicar. Fazer antes da Sprint S02 (ingestão de 2,5M linhas). | `wsl --shutdown` (duas vezes); destrói a distro atual — exportar é mandatório |
| P13 | ~~Volume `C:` a 22,3% livre~~ | **PARCIALMENTE RESOLVIDA em 16/abr/2026.** Após executar P14 (limpeza das 4 distros órfãs), `C:` subiu de 22,3% para **30,8% livres (269 GB)**. Saiu da zona de alerta. Meta de 40% ainda não atingida — será alcançada quando a P12 (migrar VHDX do Ubuntu-22.04 ativo para `E:`) for executada. Passos opcionais ainda abertos: `cleanmgr /sagerun:1`, reduzir pagefile, mover `OneDrive`/`Downloads` — executar apenas se necessário. | aguardando P12 para fechar completamente |
| P14 | ~~Quatro distros WSL2 órfãs~~ | **RESOLVIDA em 16/abr/2026.** Todas as quatro removidas via `wsl --unregister`. Backups `.tar` em `E:\WSL\backups\`: TF-GPU (23,94 GB), Ubuntu genérico (43,32 GB), GPU_MercadoFinanceiro (2,16 GB — já existente de export anterior; novo export deu `E_ACCESSDENIED` por conflito com AV, mas backup antigo cobriu), GPU_OTIMIZADA (~32,9 GB). `wsl -l -v` final mostra apenas `Ubuntu-22.04` (ativa) + `docker-desktop` (backend). Nota: `E_ACCESSDENIED` em `--export` da GPU_MercadoFinanceiro foi atribuído a Kaspersky/Defender mantendo handle no VHDX — procedimento de mitigação registrado para casos futuros (exclusão temporária via `Add-MpPreference` ou pausa de scan). | — |
| P15 | Ausência de storage de rede (NAS) para backup off-host de artefatos do projeto (WSL `.tar`, MinIO cold tier, audit trail 5 anos §16.8), sync dev↔produção a partir da Fase 4. | **Avaliar no início da Fase 4** (Mês 7-9 do §16.7), junto com a decisão de colocation. Spec de partida: NAS 2-4 bay classe Synology DS224+/DS423+ ou QNAP TS-464, HDD NAS-grade (IronWolf/WD Red Plus) em RAID 1 ou 5, rede 2.5 GbE ou 10 GbE, orçamento R$ 4-8 k. **Regra imutável:** NAS é só backup/cold-tier/audit — **NUNCA hospedar VHDX do WSL2 ou dados de treino em NAS** (violaria §2 do CLAUDE.md por penalidade de I/O maior ainda que `/mnt/c/...`). Enquanto Fase 1-3, manter §16.7 "zero CAPEX". | — (operação a quente quando chegar a hora) |
| P21 | **Backup do Postgres+TimescaleDB falhou nos dias 12 e 13/abr/2026** — pasta `backups/` tem 7 dias (08, 09, 10, 11, 14, 15, 16) em vez dos últimos 7 consecutivos. Coincide com o período em que o `finanalytics_ohlc_ingestor` entrou em erro (logs desde 13/abr). Possível causa: container `finanalytics_backup` dependendo de `postgres: condition: service_healthy` e `timescale: condition: service_healthy` — se ambos ficaram unhealthy durante incidente, o backup não subiu. | Pós-ingestão: (a) adicionar healthcheck próprio no container `backup`; (b) alertar via Telegram/email se um dia não rodar (monitorar a pasta esperando YYYY-MM-DD do dia corrente); (c) avaliar adicionar `restart: on-failure` com `retries:5` no Dockerfile.backup. | — operação a quente pós-ingestão |
| P22 | **Correção 16/abr 21h: os containers `api/worker/worker_v2/scheduler` não têm acesso a GPU nenhuma.** O `docker-compose.override.yml` define `CUDA_VISIBLE_DEVICES: "1"` mas **não há bloco `deploy.resources.reservations.devices` em nenhum serviço** — Docker não passa a GPU pra dentro do container sem essa reserva explícita. `nvidia-smi` nos containers retornou `not found in $PATH` (imagem sem pacote `nvidia-utils`), mas mesmo se tivesse, não veria nada. Além disso, confirmado pelo usuário que **cabos de vídeo foram trocados entre as GPUs em algum momento**, invertendo o mapeamento lógico de monitor vs headless face ao que o override pressupunha. Todo workload atual (scheduler, api, worker, worker_v2) roda 100% em CPU. Funcional porque as cargas atuais são CPU-bound. | Em duas frentes, antes do Sprint S03 (HMM) do plano v1.3: **(A) Compose** — adicionar em cada serviço que precisar de GPU: `deploy.resources.reservations.devices: [{driver: nvidia, count: 1, device_ids: ["0"], capabilities: [gpu]}]`. **(B) Dockerfile** — incluir `nvidia-utils` (ou equivalente) para permitir `nvidia-smi` dentro do container. **(C) Confirmar mapeamento** — rodar `docker run --rm --gpus '"device=0"' nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi --query-gpu=index,name,pci.bus_id --format=csv` e verificar que index 0 = bus `01:00.0` (a headless/dedicada). Formalizar no CLAUDE.md §3 como nova Decisão 15: "compute ML sempre em GPU 0 (headless), desktop em GPU 1; toda GPU reservation explícita no compose". | — operação a quente (restart do serviço alterado) quando aplicada |
| P19 | ~~Pasta `E:\finanalytics_data\docker\backups\` com 120,33 GB~~ | **ANALISADA 16/abr.** 14 arquivos `.sql` em 7 pastas diárias. Retenção de 7 dias **já funciona** (confirmado pelo padrão de dumps, ~8,6 GB por arquivo). 120 GB é proporcional aos 113 GB de bancos com ~6:1 de compressão. Não há problema de design. Ver P21 para a falha de 12‑13/abr. | — |
| P20 | **Projeto muito maior que o documentado no CLAUDE.md v1.3.** TimescaleDB com **545.374.696 linhas** em `market_history_trades` (85 GB), 11 hypertables ativas (`profit_ticks`, `fintz_cotacoes_ts`, `ohlc_1m`, etc), Postgres principal com 17 GB em `fintz_itens_contabeis`, 5 GB em `fintz_indicadores`. Repositório maduro em `D:\Projetos\finanalytics_ai_fresh\` com alembic, migrations, pyproject.toml/uv.lock, `.venv`, `_backups_sprint_V3` e `sprint1_legacy` (indicando iterações anteriores formalizadas). **Existem 2 CLAUDE.md distintos**: o v1.3 na pasta `Melhorias/` (forward-looking, greenfield S01-S24) e o interno do repo `D:\Projetos\finanalytics_ai_fresh\CLAUDE.md` (14/abr, provavelmente o estado-real). | Consolidar os 2 CLAUDE.md em um único baseline. Reescrever §14 reconhecendo o que já foi construído: (a) ingestão OHLCV e tick-a-tick operacional (Sprint S02 praticamente concluído); (b) schemas Postgres/TimescaleDB prontos (Sprint S01 parcialmente concluído); (c) API REST ativa na porta 8000; (d) scheduler diário de coleta. O que falta de verdade: HMM, XGBoost factor, FinBERT, LSTM/TFT, GAT, DRL, CTGAN (todos os modelos de §13). | — trabalho de documentação |
| P17 | **Stack FinAnalyticsAI Era 4 — descoberta 16/abr/2026 19h45: dado operacional mora em `E:\finanalytics_data\docker\` (Corsair MP700 PRO Gen5), não no volume `finanalytics_ai_fresh_pgdata`.** Bind mounts mapeados: `finanalytics_postgres` → `E:\finanalytics_data\docker\postgres\` (DB: `finanalytics`, user: `finanalytics`), `finanalytics_timescale` → `E:\finanalytics_data\docker\timescale\` (DB: `market_data`). Init SQL lido de `D:\Projetos\finanalytics_ai_fresh\init\`. O volume nomeado `finanalytics_ai_fresh_pgdata` (37,47 GB) é **legado de iteração anterior** — não recebe tráfego há dias. O `ohlc_ingestor` está quebrado desde 13/abr; quem ingere é o `finanalytics_scheduler` rodando às 07:00 BRT diário (~25 min, 1.858 tickers brasileiros). Últimas execuções sucesso; próximas em 17 e 18/abr 07:00 BRT. API reporta `kafka:false, producer:false` (bug de config interno a resolver depois). Era 1 (curso DSA) e Era 2 (platform abandonada) já limpas. Source code: `D:\Projetos\finanalytics_ai_fresh\`. | **Até 18/abr ~08:00 BRT: zero destrutivo** — proteger as 2 próximas janelas do scheduler. Permitido: leitura + medir `E:\finanalytics_data\docker\` + queries com user `finanalytics`. Pós-ingestão: (a) `pg_dump` do DB `finanalytics` + `market_data` para `E:\FinAnalyticsAI\backups\pg_YYYYMMDD.dump` como rede de segurança; (b) avaliar o volume `finanalytics_ai_fresh_pgdata` — se conteúdo for espelho das iterações antigas, exportar como tarball arquivado em `E:` e remover; (c) `docker image prune` + `docker builder prune -a` (~28,87 GB cache + ~9,5 GB imagens represadas). | diagnóstico a quente permitido; ação destrutiva só a partir de 19/abr |
| P18 | Docker Desktop consumindo ~181 GB do `C:` (via VHDX de `docker-desktop` em `C:\...\Docker\wsl\main`), com **132 GB reclaimable** pela análise do `docker system df`. Principal fonte de pressão histórica do `C:` (maior que as distros WSL órfãs). | Plano em 3 etapas: (1) **autorizado 16/abr** — limpeza cirúrgica de Era 1 (DSA+Ollama) e Era 2 (platform+observability+full): ~19 GB recuperados dentro do VHDX; (2) **pós-ingestão 19/abr** — `docker image prune`, `docker volume prune`, `docker builder prune -a` sobre caches > 3 meses: ~50-70 GB adicionais; (3) **pós-faxina** — `Optimize-VHD` ou Docker Desktop "Clean/Purge data" para encolher o VHDX e devolver espaço ao `C:`. Alternativa final: migrar o VHDX do `docker-desktop` para `E:` via Docker Desktop Settings → Resources → Advanced → "Disk image location" (exige desligar Docker Desktop). | Etapas 1-2 sem reboot; etapa 3 exige desligar Docker Desktop / `wsl --shutdown` |
| P16 | **Decisão de infraestrutura Fase 4: colocation vs VPN-only.** Pergunta levantada em 16/abr/2026 — "VPN pode substituir colocation?". Resposta: **VPN já está no plano (§17 Bloco 6 com VPS SP + WireGuard) mas não substitui colocation em live trading agressivo**. VPN resolve roteamento, não latência física (home→B3 fica em 10-60 ms, colocation em < 5 ms), não resolve uptime 24/7 (dependência de energia/ISP/host domésticos) e viola §16.1 (separação dev/prod). | **Escalonar a decisão em 3 passos**: (a) Fase 1-2: zero CAPEX/VPN, stack local; (b) Fase 3 (paper trading 60d): VPS SP + WireGuard **sem colocation** — latência tolerável, valida pipeline fim-a-fim; (c) Fase 4: decisão formal de colocation baseada nos resultados do paper trading — se Sharpe > 1,5 E capital-alvo justificar (break-even de colocation ~R$ 5 k/mês vs risco de downtime em DC-menos infraestrutura doméstica), aprova CAPEX; caso contrário, mantém VPS-only e aceita trade-off de latência para estratégias holding/swing. **Regra:** live trading intraday agressivo (scalping/MM) requer colocation; swing trading diário pode viver com VPN-only. | — (decisão a ser tomada no gate de saída do Bloco 6 de §17, após 60 dias de paper trading) |

**Ações que NÃO exigem reboot e podem ser feitas agora, se os diagnósticos acima apontarem:**

- Ajustar `git config --global core.autocrlf input` no WSL2
- `sudo hwclock -s` para resync do relógio
- `sudo usermod -aG docker $USER && newgrp docker`
- Mover o repositório de `/mnt/c/...` para `~/finanalyticsai/`
- Gerar `.env` a partir do `.env.example` (preenchendo só as variáveis que
  já estão disponíveis — as marcadas `[USUÁRIO FORNECE]` ficam vazias por ora)
- Criar/ajustar estrutura de diretórios da §6

---

## 15. Ordem sugerida de execução agora (sem reiniciar nada)

1. Seção 1-4 (Windows) — diagnóstico do host.
2. Seção 5-8 (WSL + Docker) — diagnóstico do ambiente de trabalho.
3. Seção 9 (bash WSL2) — validação da distro, GPU, toolkit e ferramentas.
4. Seção 10 — correções a quente se detectadas.
5. Seção 11-12 — árvore do projeto e portas livres.
6. Seção 13 — gerar `baseline.md`.
7. Seção 14 — **listar por escrito** cada item que caiu como pendência e
   registrar na Seção 14 do `CLAUDE.md` (campo "NOTAS") para agendar em
   janela com reboot permitido.

Sem validar as seções 1-9 com `OK`, **não inicie o Sprint S01** (CLAUDE.md §12).
