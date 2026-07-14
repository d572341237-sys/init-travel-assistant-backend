param(
  [int]$MaxTimeSeconds = 120
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $root "manifest.json"
$codeDir = Join-Path $root "code"
$shotDir = Join-Path $root "screenshots"

New-Item -ItemType Directory -Force -Path $codeDir, $shotDir | Out-Null

$items = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

foreach ($item in $items) {
  if ($item.codeUrl) {
    $codeOut = Join-Path $root $item.codeFile
    Write-Host "Downloading code: $($item.title) -> $($item.codeFile)"
    curl.exe -L $item.codeUrl -o $codeOut --max-time $MaxTimeSeconds --show-error --fail
  }

  if ($item.screenshotUrl -and $item.screenshotFile) {
    $shotOut = Join-Path $root $item.screenshotFile
    Write-Host "Downloading screenshot: $($item.title) -> $($item.screenshotFile)"
    curl.exe -L $item.screenshotUrl -o $shotOut --max-time $MaxTimeSeconds --show-error --fail
  }
}

Write-Host "Done."
