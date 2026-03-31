# Executa apos cada sync Fintz para manter a view atualizada
# Uso: & "scripts\refresh_fintz_dedup.ps1"
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -c `
  "REFRESH MATERIALIZED VIEW fintz_indicadores_dedup;"
Write-Host "View fintz_indicadores_dedup atualizada"
