# Liest data/.host-mounts.json und generiert docker-compose.override.yml
# mit Volume-Mounts fuer externe Windows-Ordner.
# Bestehende GPU-Konfiguration wird beibehalten.

$mountsFile = Join-Path $PSScriptRoot "data\.host-mounts.json"
$overrideFile = Join-Path $PSScriptRoot "docker-compose.override.yml"

if (-not (Test-Path $mountsFile)) { exit 0 }

$mounts = Get-Content $mountsFile -Raw | ConvertFrom-Json
$volumes = @()

if ($mounts.watch) {
    $volumes += "      - `"$($mounts.watch):/app/external/watch`""
}
if ($mounts.export) {
    $volumes += "      - `"$($mounts.export):/app/external/export`""
}

if ($volumes.Count -eq 0) { exit 0 }

# GPU-Konfiguration aus bestehender Override-Datei lesen
$gpuSection = ""
if (Test-Path $overrideFile) {
    $existing = Get-Content $overrideFile -Raw
    if ($existing -match "ollama:") {
        # Ollama-Block extrahieren (alles ab "  ollama:")
        if ($existing -match "(  ollama:[\s\S]*)") {
            $gpuSection = "`n" + $Matches[1].TrimEnd()
        }
    }
}

# Override-Datei schreiben
$yaml = "services:`n  backend:`n    volumes:`n"
$yaml += ($volumes -join "`n")
$yaml += $gpuSection
$yaml += "`n"

[System.IO.File]::WriteAllText($overrideFile, $yaml)
Write-Host "  Host-Ordner konfiguriert."
