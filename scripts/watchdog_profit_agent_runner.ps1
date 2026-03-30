# Auto-gerado por install_profit_watchdog.ps1 - nao editar manualmente
Set-Location "D:\Projetos\finanalytics_ai_fresh"
$env:PROFIT_WATCHDOG_INTERVAL_SEC  = "60"
$env:PROFIT_WATCHDOG_LOG_DIR       = "D:\Logs\finanalytics"
$env:PROFIT_WATCHDOG_PROJECT_DIR   = "D:\Projetos\finanalytics_ai_fresh"
$env:PROFIT_WATCHDOG_PORT          = "8001"
$env:PROFIT_WATCHDOG_MAX_RESTARTS  = "5"
& "D:\Projetos\finanalytics_ai_fresh\scripts\watchdog_profit_agent.ps1"