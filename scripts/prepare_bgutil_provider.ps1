param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$version = '1.3.2'
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$vendorRoot = Join-Path $projectRoot 'vendor\bgutil-provider'
$pluginDir = Join-Path $projectRoot 'yt-dlp-plugins'
$pluginPath = Join-Path $pluginDir 'bgutil-ytdlp-pot-provider.zip'
$serverEntry = Join-Path $vendorRoot 'server\build\main.js'
$nodeTarget = Join-Path $vendorRoot 'node.exe'

if (-not $Force -and (Test-Path $serverEntry) -and (Test-Path $nodeTarget) -and (Test-Path $pluginPath)) {
    Write-Host "[OK] BgUtils PO Token Provider $version is ready."
    exit 0
}

$nodeCommand = Get-Command node -ErrorAction Stop
$npmCommand = Get-Command npm -ErrorAction Stop
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("vidoon-bgutil-" + [Guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $tempRoot 'source.zip'
$extractPath = Join-Path $tempRoot 'source'
$stagePath = Join-Path $tempRoot 'stage'

try {
    New-Item -ItemType Directory -Path $tempRoot, $extractPath, $stagePath -Force | Out-Null
    Write-Host "[1/5] Download official BgUtils Provider $version source..."
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/$version.zip" `
        -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force

    $sourceRoot = Get-ChildItem -LiteralPath $extractPath -Directory | Select-Object -First 1
    if (-not $sourceRoot) {
        throw 'Provider source archive is empty.'
    }
    $serverRoot = Join-Path $sourceRoot.FullName 'server'
    $mainSource = Join-Path $serverRoot 'src\main.ts'

    Write-Host '[2/5] Restrict Provider server to 127.0.0.1...'
    $mainText = Get-Content -LiteralPath $mainSource -Raw -Encoding UTF8
    $mainText = $mainText.Replace('host: "::"', 'host: "127.0.0.1"')
    $mainText = $mainText.Replace('host: "0.0.0.0"', 'host: "127.0.0.1"')
    [IO.File]::WriteAllText($mainSource, $mainText, (New-Object Text.UTF8Encoding($false)))

    Write-Host '[3/5] Install dependencies and compile server...'
    Push-Location $serverRoot
    try {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        & $npmCommand.Source exec -- tsc
        if ($LASTEXITCODE -ne 0) { throw 'TypeScript compilation failed.' }
        & $npmCommand.Source prune --omit=dev
        if ($LASTEXITCODE -ne 0) { throw 'npm prune failed.' }
    } finally {
        Pop-Location
    }

    Write-Host '[4/5] Assemble local runtime and plugin...'
    $stageVendor = Join-Path $stagePath 'bgutil-provider'
    New-Item -ItemType Directory -Path $stageVendor -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot.FullName 'LICENSE') -Destination $stageVendor
    Copy-Item -LiteralPath (Join-Path $sourceRoot.FullName 'README.md') -Destination $stageVendor
    Copy-Item -LiteralPath $nodeCommand.Source -Destination (Join-Path $stageVendor 'node.exe')
    Copy-Item -LiteralPath $serverRoot -Destination $stageVendor -Recurse

    $modificationNotice = @"
BgUtils PO Token Provider $version
Upstream: https://github.com/Brainicism/bgutil-ytdlp-pot-provider
License: GPL-3.0-only

Vidoon modification: server binding addresses were changed to 127.0.0.1 so the
bundled provider is reachable only from the local computer.
"@
    [IO.File]::WriteAllText(
        (Join-Path $stageVendor 'MODIFICATIONS.txt'),
        $modificationNotice,
        (New-Object Text.UTF8Encoding($false))
    )

    New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/download/$version/bgutil-ytdlp-pot-provider.zip" `
        -OutFile $pluginPath

    if (Test-Path $vendorRoot) {
        $resolvedVendor = [IO.Path]::GetFullPath($vendorRoot)
        $expectedParent = [IO.Path]::GetFullPath((Join-Path $projectRoot 'vendor'))
        if (-not $resolvedVendor.StartsWith($expectedParent, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace unexpected path: $resolvedVendor"
        }
        Remove-Item -LiteralPath $vendorRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $vendorRoot) -Force | Out-Null
    Move-Item -LiteralPath $stageVendor -Destination $vendorRoot

    Write-Host '[5/5] Verify prepared runtime...'
    foreach ($requiredFile in @($serverEntry, $nodeTarget, $pluginPath)) {
        if (-not (Test-Path $requiredFile)) {
            throw "Prepared Provider is missing: $requiredFile"
        }
    }
    Write-Host "[OK] BgUtils PO Token Provider $version prepared successfully."
} finally {
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
