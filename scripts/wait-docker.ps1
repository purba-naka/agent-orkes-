for ($i = 0; $i -lt 60; $i++) {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Output 'engine ready'
        exit 0
    }
    Start-Sleep -Seconds 5
}
Write-Output 'engine NOT ready'
exit 1
