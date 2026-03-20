# Uso: .\zip_projeto.ps1 [-ProjectPath "caminho\do\projeto"]
# Se não passar caminho, usa o diretório atual.

param(
    [string]$ProjectPath = "."
)

$projectPath   = Resolve-Path $ProjectPath
$projectName   = Split-Path $projectPath -Leaf
$outputFile    = Join-Path (Split-Path $projectPath -Parent) "${projectName}_sem_venv.zip"

$excludeDirs = @(
    ".venv", "__pycache__", ".git", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "dist", "build", "node_modules"
)
$excludeExts = @("*.pyc", "*.egg-info")

Write-Host "Zipando '$projectName' -> '$outputFile' ..." -ForegroundColor Cyan
Write-Host "Excluindo: $($excludeDirs -join ', '), $($excludeExts -join ', ')" -ForegroundColor DarkGray

# Coleta todos os arquivos respeitando os filtros
$files = Get-ChildItem -Path $projectPath -Recurse -File | Where-Object {
    $file = $_
    $isExcludedDir = $false

    foreach ($dir in $excludeDirs) {
        if ($file.FullName -match [regex]::Escape("\$dir\")) {
            $isExcludedDir = $true
            break
        }
    }

    $isExcludedExt = $false
    foreach ($ext in $excludeExts) {
        if ($file.Name -like $ext) {
            $isExcludedExt = $true
            break
        }
    }

    -not $isExcludedDir -and -not $isExcludedExt
}

# Remove zip anterior se existir
if (Test-Path $outputFile) {
    Remove-Item $outputFile -Force
}

# Cria o zip mantendo a estrutura de pastas relativa
$files | ForEach-Object {
    $relativePath = $_.FullName.Substring($projectPath.Path.Length + 1)
    $entryPath    = Join-Path $projectName $relativePath

    $zip = [System.IO.Compression.ZipFile]::Open($outputFile, [System.IO.Compression.ZipArchiveMode]::Update)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, $_.FullName, $entryPath, [System.IO.Compression.CompressionLevel]::Optimal
    ) | Out-Null
    $zip.Dispose()
}

$sizeMB = [math]::Round((Get-Item $outputFile).Length / 1MB, 2)
Write-Host ""
Write-Host "Arquivo gerado: $outputFile" -ForegroundColor Green
Write-Host "Tamanho: ${sizeMB} MB"      -ForegroundColor Green
