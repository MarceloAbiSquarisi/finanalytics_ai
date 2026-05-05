<#
.SYNOPSIS
    Compacta logs rotacionados do profit_agent (NSSM) em um arquivo .gz por dia/stream.

.DESCRIPTION
    NSSM rotaciona stdout/stderr a cada restart, gerando milhares de arquivos pequenos
    (ex: profit_agent.stdout-20260428T114428.184.log). Este script agrupa os arquivos
    com mais de N dias por data + stream e produz um unico .gz consolidado:
        profit_agent-2026-04-28-stdout.log.gz
        profit_agent-2026-04-28-stderr.log.gz
    Os originais sao deletados apenas apos verificar que o .gz foi escrito com sucesso.

.PARAMETER LogDir
    Diretorio dos logs. Default: D:\Projetos\finanalytics_ai_fresh\logs

.PARAMETER DaysToKeep
    Mantem N dias mais recentes intactos (default 7).

.PARAMETER DryRun
    Mostra o que faria sem escrever nada nem deletar.

.EXAMPLE
    .\scripts\compact_profit_agent_logs.ps1 -DryRun
    .\scripts\compact_profit_agent_logs.ps1
    .\scripts\compact_profit_agent_logs.ps1 -DaysToKeep 3
#>
param(
    [string]$LogDir = "D:\Projetos\finanalytics_ai_fresh\logs",
    [int]$DaysToKeep = 7,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $LogDir)) {
    Write-Error "LogDir nao existe: $LogDir"
    exit 1
}

$cutoff = (Get-Date).Date.AddDays(-$DaysToKeep)
Write-Host "Compactando profit_agent logs em $LogDir" -ForegroundColor Cyan
Write-Host "Cutoff: arquivos com data anterior a $($cutoff.ToString('yyyy-MM-dd')) (DaysToKeep=$DaysToKeep)" -ForegroundColor Cyan
if ($DryRun) { Write-Host "MODO DRY-RUN - nenhuma alteracao sera feita" -ForegroundColor Yellow }

# Pattern: profit_agent.{stream}-YYYYMMDDTHHMMSS.NNN.log
$pattern = '^profit_agent\.(?<stream>stdout|stderr)-(?<date>\d{8})T\d{6}\.\d+\.log$'

$files = Get-ChildItem -LiteralPath $LogDir -Filter 'profit_agent*.log' -File
$groups = @{}
$skipped = 0

foreach ($f in $files) {
    if ($f.Name -notmatch $pattern) { $skipped++; continue }
    $stream = $matches['stream']
    $dateStr = $matches['date']
    $fileDate = [datetime]::ParseExact($dateStr, 'yyyyMMdd', $null)
    if ($fileDate -ge $cutoff) { continue }  # dentro da janela de retencao
    $key = "$($fileDate.ToString('yyyy-MM-dd'))|$stream"
    if (-not $groups.ContainsKey($key)) { $groups[$key] = New-Object System.Collections.ArrayList }
    [void]$groups[$key].Add($f)
}

if ($skipped -gt 0) {
    Write-Host "Aviso: $skipped arquivos nao casaram com o pattern esperado, ignorados" -ForegroundColor DarkYellow
}

if ($groups.Count -eq 0) {
    Write-Host "Nada a compactar." -ForegroundColor Green
    exit 0
}

$totalBytesIn = 0
$totalBytesOut = 0
$totalFilesIn = 0
$totalArchives = 0
$errors = 0

foreach ($key in ($groups.Keys | Sort-Object)) {
    $parts = $key -split '\|'
    $date = $parts[0]
    $stream = $parts[1]
    $bucket = $groups[$key] | Sort-Object Name  # timestamp ordena lexicograficamente
    $bytesIn = ($bucket | Measure-Object Length -Sum).Sum
    $outName = "profit_agent-$date-$stream.log.gz"
    $outPath = Join-Path $LogDir $outName

    if (Test-Path -LiteralPath $outPath) {
        Write-Host "  [SKIP] $outName ja existe ($($bucket.Count) arquivos do dia ignorados)" -ForegroundColor DarkYellow
        continue
    }

    Write-Host ("  {0}  {1,4} arquivos  {2,8:N1} KB  ->  {3}" -f $date, $bucket.Count, ($bytesIn/1KB), $outName)

    if ($DryRun) {
        $totalBytesIn += $bytesIn
        $totalFilesIn += $bucket.Count
        $totalArchives++
        continue
    }

    $tmpPath = "$outPath.tmp"
    try {
        $outStream = [System.IO.File]::Create($tmpPath)
        $gzipStream = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionLevel]::Optimal)
        $buffer = New-Object byte[] 81920
        foreach ($f in $bucket) {
            # Header simples por arquivo pra preservar rastreabilidade
            $header = [System.Text.Encoding]::UTF8.GetBytes("`n===== $($f.Name) ($($f.LastWriteTime.ToString('s'))) =====`n")
            $gzipStream.Write($header, 0, $header.Length)
            $inStream = [System.IO.File]::OpenRead($f.FullName)
            try {
                while (($read = $inStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $gzipStream.Write($buffer, 0, $read)
                }
            } finally { $inStream.Dispose() }
        }
        $gzipStream.Dispose()
        $outStream.Dispose()
        Move-Item -LiteralPath $tmpPath -Destination $outPath
        $bytesOut = (Get-Item -LiteralPath $outPath).Length

        # So deleta originais apos o .gz existir e ter tamanho > 0
        if ((Test-Path -LiteralPath $outPath) -and $bytesOut -gt 0) {
            foreach ($f in $bucket) { Remove-Item -LiteralPath $f.FullName -Force }
        } else {
            throw "Archive vazio ou ausente: $outPath"
        }

        $ratio = if ($bytesIn -gt 0) { 100 * $bytesOut / $bytesIn } else { 0 }
        Write-Host ("       OK  {0,8:N1} KB gz  ({1:N1}% do original)" -f ($bytesOut/1KB), $ratio) -ForegroundColor Green
        $totalBytesIn += $bytesIn
        $totalBytesOut += $bytesOut
        $totalFilesIn += $bucket.Count
        $totalArchives++
    } catch {
        $errors++
        Write-Host "       ERRO: $_" -ForegroundColor Red
        if (Test-Path -LiteralPath $tmpPath) { Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue }
    }
}

Write-Host ""
Write-Host "Resumo:" -ForegroundColor Cyan
Write-Host ("  Arquivos consolidados: {0}" -f $totalFilesIn)
Write-Host ("  Arquivos .gz gerados:  {0}" -f $totalArchives)
Write-Host ("  Tamanho original:      {0:N1} MB" -f ($totalBytesIn/1MB))
if (-not $DryRun -and $totalBytesIn -gt 0) {
    Write-Host ("  Tamanho compactado:    {0:N1} MB ({1:N1}% do original)" -f ($totalBytesOut/1MB), (100*$totalBytesOut/$totalBytesIn))
    Write-Host ("  Espaco liberado:       {0:N1} MB" -f (($totalBytesIn-$totalBytesOut)/1MB))
}
if ($errors -gt 0) {
    Write-Host "  Erros: $errors" -ForegroundColor Red
    exit 1
}
