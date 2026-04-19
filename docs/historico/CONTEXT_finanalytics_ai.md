
---

## Convencao de Scripts PowerShell

**Regra:** Toda mudanca em arquivos do projeto deve ser entregue como script `.ps1`.

- **Local de armazenamento:** `D:\Downloads\`
- **Como executar:** No terminal do PyCharm:
  ```
  powershell -ExecutionPolicy Bypass -File "D:\Downloads\nome_do_script.ps1"
  ```
- **Nomenclatura:** `finanalytics_<descricao_da_mudanca>.ps1`
- **Nunca usar em dashes (--) em comentarios PS** - usar hifens simples para evitar erro de encoding
- **Nunca usar template literals (backticks) em here-strings PS** - usar concatenacao vanilla para JS/texto dinamico
- **Sempre usar `@'...'@` (aspas simples)** para blocos de codigo Python dentro dos scripts, evitando interpolacao indevida de `$`
- **Sempre normalizar line endings** ao ler/escrever arquivos Python:
  ```
  $content = $content.Replace("`r`n", "`n")
  [System.IO.File]::WriteAllText($file, $content, [System.Text.Encoding]::UTF8)
  ```
- **Verificar ancora antes de aplicar** - os scripts abortam com [ERRO] se o trecho alvo nao for encontrado, sem modificar o arquivo

### Scripts entregues

| Script | Descricao |
|--------|-----------|
| `finanalytics_profit_tickers_db.ps1` | Move PROFIT_SUBSCRIBE_TICKERS do .env para tabela `profit_subscribed_tickers` no TimescaleDB. Adiciona endpoints GET /tickers, POST /tickers/add, POST /tickers/remove |

