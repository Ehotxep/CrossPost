param([string]$Version, [string]$Folder = "artifacts/win-x64", [string]$Output = "artifacts")
$ErrorActionPreference = "Stop"
$root = (Resolve-Path $Folder).Path
$files = @(Get-ChildItem $root -File -Recurse | Where-Object Name -ne 'native-package.json' | ForEach-Object {
    @{ Path = [IO.Path]::GetRelativePath($root, $_.FullName).Replace('\','/'); Length = $_.Length; Sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
})
$manifest = @{Product="CrossPost.Native"; Version=$Version; Rid="win-x64"; Files=$files} | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $root 'native-package.json'), $manifest, [Text.UTF8Encoding]::new($false))
$archive = [IO.Path]::GetFullPath((Join-Path $Output "CrossPost-Native-Prototype-$Version-win-x64.zip"))
if (Test-Path $archive) { Remove-Item $archive }
$zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem $root -File -Recurse | ForEach-Object {
        $name = [IO.Path]::GetRelativePath($root,$_.FullName).Replace('\','/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$_.FullName,$name,[IO.Compression.CompressionLevel]::Fastest) | Out-Null
    }
} finally { $zip.Dispose() }
[IO.File]::WriteAllText("$archive.sha256", (Get-FileHash $archive -Algorithm SHA256).Hash, [Text.UTF8Encoding]::new($false))
Write-Output $archive
