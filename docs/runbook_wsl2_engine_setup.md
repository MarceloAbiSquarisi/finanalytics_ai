# Runbook — Migração Docker Desktop → Docker Engine WSL2

> **Sprint I1 — concluído 01/mai/2026**. Este runbook consolida o processo
> completo (Fase A + B.1 + B.2) numa sequência reproduzível. Útil para:
> - Recriar a stack em outra máquina Windows (e.g. notebook backup)
> - Recovery se WSL distro for resetada/corrompida
> - Onboarding de novos devs

## Pré-requisitos

| Item | Versão / Estado |
|---|---|
| Windows 11 Pro | 26200+ |
| WSL2 + Ubuntu-22.04 distro instalada | `wsl --status` mostra |
| NVIDIA driver Windows | 591.86+ (CUDA 13.1) |
| Docker Desktop instalado | OK como fallback (`docker context use default`) |
| Repo clonado | `D:\Projetos\finanalytics_ai_fresh\` (acessível via `/mnt/d/`) |
| Volumes legacy | `E:\finanalytics_data\docker\{postgres,timescale}` (NTFS) |
| `profit_agent` NSSM service | Running, bind `127.0.0.1:8002` |

## Fase A — Engine WSL2 + nvidia toolkit (~10min)

### A.1 — Habilitar systemd em `/etc/wsl.conf`

```powershell
wsl -d Ubuntu-22.04 -u root -- bash -c 'grep -q "^systemd=true" /etc/wsl.conf || printf "\n[boot]\nsystemd=true\n" >> /etc/wsl.conf'
wsl --shutdown
Start-Sleep 8
wsl -d Ubuntu-22.04 -- bash -c 'ps -p 1 -o comm='   # esperado: systemd
```

### A.2 — Instalar Docker Engine (Ubuntu official)

```powershell
$dockerInstall = @'
set -e
apt update -qq
apt install -y -qq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update -qq
apt install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker abi
systemctl enable --now docker
'@
wsl -d Ubuntu-22.04 -u root -- bash -c $dockerInstall
```

### A.3 — NVIDIA Container Toolkit

```powershell
$nvidiaInstall = @'
set -e
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt update -qq
apt install -y -qq nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
'@
wsl -d Ubuntu-22.04 -u root -- bash -c $nvidiaInstall
```

### A.4 — Validações

```powershell
# Docker funciona
wsl -d Ubuntu-22.04 -- docker run --rm hello-world

# GPU passthrough (esperado: 2x RTX 4090, bus 01:00.0 + 08:00.0)
wsl -d Ubuntu-22.04 -- docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

---

## Fase B.1 — Cutover containers + host networking (~30min)

### B.1.1 — Habilitar dockerd TCP loopback (pra context PowerShell-side)

```powershell
$mergedJson = @'
{
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    },
    "hosts": ["unix:///var/run/docker.sock", "tcp://127.0.0.1:2375"]
}
'@
$mergedJson | wsl -d Ubuntu-22.04 -u root -- bash -c 'cat > /etc/docker/daemon.json'

# systemd override (resolve conflito -H flag do socket file)
wsl -d Ubuntu-22.04 -u root -- bash -c '
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/dockerd
EOF
systemctl daemon-reload
systemctl restart docker
'
```

### B.1.2 — Docker context PowerShell-side

```powershell
docker context create wsl-engine --docker host=tcp://127.0.0.1:2375 --description "Engine WSL2"
docker context use wsl-engine
docker version --format '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}'  # esperado linux/amd64
```

### B.1.3 — profit_agent bind 0.0.0.0 (necessário pra Engine WSL2 alcançar)

Em `src/finanalytics_ai/workers/profit_agent.py` (~linha 6946):
```python
bind_host = os.getenv("PROFIT_AGENT_BIND", "0.0.0.0")
server = ThreadingHTTPServer((bind_host, port), Handler)
```

Restart NSSM (PowerShell admin):
```powershell
Restart-Service FinAnalyticsAgent
netstat -ano | Select-String ":8002\s+0.0.0.0"  # confirma bind 0.0.0.0
```

### B.1.4 — Firewall Windows inbound rule (PowerShell admin)

```powershell
# Identifica subnet WSL atual:
wsl -d Ubuntu-22.04 -- bash -c 'ip route show default'   # ex: 172.17.80.1 → subnet 172.17.80.0/20

New-NetFirewallRule -DisplayName "Profit Agent WSL Inbound" `
  -Direction Inbound -LocalPort 8002 -Protocol TCP `
  -Action Allow -RemoteAddress 172.17.80.0/20 -Profile Any
```

> ⚠️ **WSL gateway IP pode mudar após `wsl --shutdown` ou reboot Windows.**
> Verificar com `wsl -d Ubuntu-22.04 -- ip route show default` periodicamente.
> Se mudar, atualizar (a) regra firewall via `Set-NetFirewallRule -RemoteAddress NEW_SUBNET`
> e (b) `docker-compose.wsl.yml` `extra_hosts`.

### B.1.5 — `docker-compose.wsl.yml` (override path NTFS → /mnt/e/)

Já versionado. Inclui:
- Volumes Postgres/Timescale/Kafka/Zookeeper/Evolution/Backup mapeados pra /mnt/e + /mnt/h
- `extra_hosts: host.docker.internal:172.17.80.1` (IP direto do WSL gateway — `:host-gateway` resolve pra docker bridge interna em Engine WSL2 puro, NÃO pro Windows host)

### B.1.6 — Cutover

Stop Docker Desktop containers + start Engine WSL2:
```powershell
docker context use default
docker compose down
docker context use wsl-engine
wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/Projetos/finanalytics_ai_fresh && DATA_DIR_HOST=/mnt/e/finanalytics_data docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d'
```

> ⚠️ **`docker compose` sempre rodar de DENTRO do WSL** (`wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/... && docker compose ...'`). Se rodar do PowerShell direto, paths relativos do compose viram Windows-absolute (`D:\Projetos\...\init`) e Engine WSL2 falha com "invalid volume specification".

### B.1.7 — Validação

```powershell
# Container -> agent via host.docker.internal
docker exec finanalytics_api python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8002/health',timeout=3).read())"
# esperado: b'{"ok": true}'

# Windows side -> api via WSL2 port forward
curl http://localhost:8000/api/v1/agent/health
# esperado: {"ok":true}
```

---

## Fase B.2 — Volumes ext4 nativo WSL (~50min, downtime obrigatório)

### B.2.1 — Pre-flight

```powershell
wsl -d Ubuntu-22.04 -u root -- bash -c '
df -h /                                                 # ext4 livre (>= 220GB)
du -sh /mnt/e/finanalytics_data/docker/postgres /mnt/e/finanalytics_data/docker/timescale  # confirmar 36G + 183G
docker ps --format "{{.Names}} {{.Status}}" | grep -E "postgres|timescale"   # devem estar healthy
'
```

### B.2.2 — Stop containers

```powershell
wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/Projetos/finanalytics_ai_fresh && DATA_DIR_HOST=/mnt/e/finanalytics_data docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml down'
```

### B.2.3 — Cópia raw (~50min total)

```powershell
wsl -d Ubuntu-22.04 -u root -- bash -c '
mkdir -p /home/abi/finanalytics/data
time cp -a /mnt/e/finanalytics_data/docker/postgres /home/abi/finanalytics/data/postgres   # ~7-10min
time cp -a /mnt/e/finanalytics_data/docker/timescale /home/abi/finanalytics/data/timescale  # ~30-45min
chown -R 999:999 /home/abi/finanalytics/data/postgres
chown -R 999:999 /home/abi/finanalytics/data/timescale
du -sh /home/abi/finanalytics/data/postgres /home/abi/finanalytics/data/timescale  # confirmar sizes batem
'
```

### B.2.4 — Compose update (já versionado em master)

`docker-compose.wsl.yml` aponta pra `/home/abi/finanalytics/data/{postgres,timescale}`. **NÃO deletar `/mnt/e/...` por 1 semana** — fallback de rollback.

### B.2.5 — Up + validação

```powershell
wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/Projetos/finanalytics_ai_fresh && DATA_DIR_HOST=/mnt/e/finanalytics_data docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d'

# Wait healthy + sanity:
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -tAc "SELECT version_num FROM alembic_version"
docker exec finanalytics_timescale psql -U finanalytics -d market_data -tAc "SELECT COUNT(DISTINCT ticker) FROM fintz_cotacoes_ts"
curl http://localhost:8000/api/v1/agent/health
```

### B.2.6 — Rollback (se necessário)

Edita `docker-compose.wsl.yml` revertendo paths pra `/mnt/e/...` e `compose down + up -d`. Como `/mnt/e/...` ficou intocado, o estado é exato do momento pré-cópia.

---

## Operação contínua

### Comandos PS-side (com `docker context use wsl-engine`)

```powershell
docker ps                                           # lista containers
docker logs -f finanalytics_api
docker exec -it finanalytics_postgres psql -U finanalytics -d finanalytics
docker stats
```

### Subir/parar stack completa

```powershell
wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/Projetos/finanalytics_ai_fresh && DATA_DIR_HOST=/mnt/e/finanalytics_data docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml up -d'

wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/Projetos/finanalytics_ai_fresh && DATA_DIR_HOST=/mnt/e/finanalytics_data docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.wsl.yml down'
```

### Trocar pra Docker Desktop (debug emergencial)

```powershell
docker context use default     # volta pra Docker Desktop daemon
# ... debug ...
docker context use wsl-engine  # volta pra produção
```

---

## Troubleshooting

### `host.docker.internal` resolve pra IP errado

Engine WSL2 puro: `:host-gateway` resolve pra docker bridge interna (`172.18.0.x`), NÃO pro Windows host. **Solução**: usar IP direto do WSL gateway (`172.17.80.1` ou similar) no `extra_hosts` do compose. Verificar com `wsl -d Ubuntu-22.04 -- ip route show default`.

### "Cannot open service" em PS sem admin

`Restart-Service FinAnalyticsAgent` requer PowerShell admin. Alternativa: API endpoint `/api/v1/agent/restart` (sudo token via `/api/v1/auth/sudo`, password `admin123`).

### "Multiple head revisions" em alembic upgrade

Projeto tem 2 branches: `0xxx_*` (Postgres) + `ts_xxxx_*` (Timescale). `alembic upgrade head` falha. Usar revisão específica: `alembic upgrade 0024_robot_pair_positions`.

### Container em loop de restart com "Can't locate revision"

Imagem stale — built antes da migration nova. Workaround temporário: `docker cp alembic/versions/0xxx_*.py finanalytics_<service>:/app/alembic/versions/`. Permanente: `docker compose build api worker` (~5min cache, evite `--no-cache` que pode falhar transient em pip install torch+prophet).

### "invalid volume specification 'D:\\...\\init'"

Compose rodando do PowerShell — paths relativos viram Windows-absolute. Solução: rodar de DENTRO do WSL via `wsl -d Ubuntu-22.04 -- bash -c 'cd /mnt/d/... && docker compose ...'`.

### WSL gateway IP mudou após reboot

```powershell
$newIp = (wsl -d Ubuntu-22.04 -- bash -c 'ip route show default') -replace 'default via (\S+).*', '$1'
echo "New IP: $newIp"
# Atualizar regra firewall:
$newSubnet = ($newIp -split '\.' | Select-Object -First 3) -join '.'
Set-NetFirewallRule -DisplayName "Profit Agent WSL Inbound" -RemoteAddress "$newSubnet.0/20"
# Atualizar docker-compose.wsl.yml extra_hosts manualmente se IP saiu da subnet 172.17.80.0/20
```

---

## Histórico

- 01/mai/2026 — Sprint I1 fechada. Commits chave: `ab0ea8b` (Fase A), `950ac35` (Fase B.1 cutover live), `ffcd06c` (Fase B.2 ext4). Sessão maratona ~11h, 34+ commits.
- Decisão arquitetural canônica: CLAUDE.md §Decisão 22.
