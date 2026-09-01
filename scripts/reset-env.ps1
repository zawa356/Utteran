[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = "Stop"
$CachePath = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) `
    "utteran\utteran\Cache\openvino-genai-compiled"
$ResolvedParent = [IO.Path]::GetFullPath((Split-Path -Parent $CachePath))
$ExpectedParent = [IO.Path]::GetFullPath((Join-Path `
            ([Environment]::GetFolderPath("LocalApplicationData")) "utteran\utteran\Cache"))

if ($ResolvedParent -ne $ExpectedParent) {
    throw "OpenVINO GenAIキャッシュの解決先がutteran管理下ではありません: $CachePath"
}
if (Test-Path -LiteralPath $CachePath -PathType Container) {
    if ($PSCmdlet.ShouldProcess($CachePath, "OpenVINO GenAIコンパイルキャッシュを削除")) {
        Remove-Item -LiteralPath $CachePath -Recurse -Force
        Write-Host "削除しました: $CachePath"
    }
}
else {
    Write-Host "OpenVINO GenAIキャッシュはありません: $CachePath"
}
