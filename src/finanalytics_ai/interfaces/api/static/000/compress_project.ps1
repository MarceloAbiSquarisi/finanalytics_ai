# compress_project.ps1
# Uso: ja dentro do PowerShell do PyCharm, rode:
#   & "D:\Downloads\compress_project.ps1"

$projectRoot = Get-Location
$projectName = Split-Path $projectRoot -Leaf
$timestamp   = Get-Date -Format "yyyyMMdd_HHmm"
$outputZip   = "D:\Downloads\${projectName}_${timestamp}.zip"

$excludeDirs = @(
    ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".git",
    "dist", "build",
    ".idea"
)

$excludeExts = @(
    "*.pyc", "*.pyo", "*.pyd",
    "*.log", "*.tmp",
    "*.db", "*.sqlite3"
)

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  FinAnalytics AI -- Compactador" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Projeto : $projectRoot"
Write-Host "Destino : $outputZip"
Write-Host ""

$files = Get-ChildItem -Path $projectRoot -Recurse -File | Where-Object {
    $file = $_

    foreach ($ext in $excludeExts) {
        if ($file.Name -like $ext) { return $false }
    }

    $relativePath = $file.FullName.Substring($projectRoot.Path.Length + 1)
    $segments = $relativePath -split "\\"

    foreach ($dir in $excludeDirs) {
        foreach ($seg in $segments) {
            if ($seg -like $dir) { return $false }
        }
    }

    return $true
}

$totalSize = ($files | Measure-Object -Property Length -Sum).Sum
$totalMB   = [math]::Round($totalSize / 1MB, 2)

Write-Host "Arquivos encontrados : $($files.Count)"
Write-Host "Tamanho total        : $totalMB MB"
Write-Host ""

if ($files.Count -eq 0) {
    Write-Host "Nenhum arquivo encontrado." -ForegroundColor Red
    exit 1
}

if (Test-Path $outputZip) {
    Remove-Item $outputZip -Force
    Write-Host "Zip anterior removido." -ForegroundColor Yellow
}

Write-Host "Compactando..." -ForegroundColor Yellow

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($outputZip, 'Create')

foreach ($file in $files) {
    $entryName = $file.FullName.Substring($projectRoot.Path.Length + 1)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, $file.FullName, $entryName,
        [System.IO.Compression.CompressionLevel]::Optimal
    ) | Out-Null
}

$zip.Dispose()

$zipSizeMB = [math]::Round((Get-Item $outputZip).Length / 1MB, 2)
$ratio     = if ($totalMB -gt 0) { [math]::Round((1 - $zipSizeMB / $totalMB) * 100, 1) } else { 0 }

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  Concluido!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host "Arquivo  : $outputZip"
Write-Host "Tamanho  : $zipSizeMB MB"
Write-Host "Reducao  : $ratio porcento"
Write-Host ""
