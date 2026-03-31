param([switch]$SkipML, [switch]$DryRun)

$skipMlVal = if ($SkipML) {"true"} else {"false"}
$dryRunVal = if ($DryRun) {"true"} else {"false"}

Write-Host "Manutencao: SkipML=$SkipML DryRun=$DryRun" -ForegroundColor Cyan

docker exec -e MAINTENANCE_SKIP_ML=$skipMlVal -e MAINTENANCE_DRY_RUN=$dryRunVal `
    finanalytics_api python3 -m finanalytics_ai.workers.maintenance_worker
