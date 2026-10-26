# 把仓库占位符替换成你的 GitHub 用户名
#
# 用法（在插件目录下执行）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-repo.ps1 -Username 你的用户名
#
# 作用：把 metadata.yaml / README.md / LICENSE 里的 your-github-username 全部替换掉。

param(
    [Parameter(Mandatory = $true)][string]$Username
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @('metadata.yaml', 'README.md', 'LICENSE')
$utf8 = New-Object System.Text.UTF8Encoding($false)

foreach ($name in $files) {
    $path = Join-Path $root $name
    if (-not (Test-Path $path)) { Write-Host "skip (absent): $name"; continue }
    $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    $before = ([regex]::Matches($text, 'your-github-username')).Count
    if ($before -eq 0) { Write-Host "no placeholder: $name"; continue }
    $text = $text.Replace('your-github-username', $Username)
    [IO.File]::WriteAllText($path, $text, $utf8)
    Write-Host "updated $name ($before 处)"
}

Write-Host ""
Write-Host "仓库地址：https://github.com/$Username/astrbot_plugin_deepseek_balance"
Write-Host "下一步："
Write-Host "  git init; git add .; git commit -m 'feat: 初始化'"
Write-Host "  git remote add origin https://github.com/$Username/astrbot_plugin_deepseek_balance.git"
Write-Host "  git push -u origin main"
