#Requires -Version 5.1
<#
.SYNOPSIS
  为 dist\leidian.exe 做 Authenticode 签名

.DESCRIPTION
  - 正式环境：使用你从 CA 购买的 .pfx（设置环境变量或传参），需本机已装 Windows SDK 的 signtool.exe。
  - 自签名（-SelfSigned）：仅用于本机/测试；不能通过「智能应用控制」，也不能冒充「已认证发行商」。

  环境变量（可选）：
    LEIDIAN_PFX          .pfx 文件完整路径
    LEIDIAN_PFX_PASSWORD 证书密码（明文，仅本机临时用）

.EXAMPLE
  .\sign_leidian.ps1 -SelfSigned

.EXAMPLE
  .\sign_leidian.ps1 -PfxPath C:\certs\mycode.pfx -PfxPassword (Read-Host -AsSecureString)
#>
param(
    [string] $Exe = "",
    [string] $PfxPath = $env:LEIDIAN_PFX,
    [SecureString] $PfxPassword,
    [switch] $SelfSigned
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }

if (-not $Exe) {
    $Exe = Join-Path $Root "dist\leidian.exe"
}

if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Error "找不到可执行文件: $Exe （请先运行 build_exe.bat 打包）"
}

function Find-SignTool {
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    )
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        $candidates = Get-ChildItem -Path $r -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\" } |
            Sort-Object FullName -Descending
        if ($candidates) { return $candidates[0].FullName }
    }
    return $null
}

function Sign-WithPfx {
    param([string] $Tool, [string] $File, [string] $Pfx, [SecureString] $SecPwd)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecPwd)
    )
    try {
        $args = @(
            "sign",
            "/f", $Pfx,
            "/p", $plain,
            "/fd", "SHA256",
            "/tr", "http://timestamp.digicert.com",
            "/td", "SHA256",
            $File
        )
        & $Tool @args
        if ($LASTEXITCODE -ne 0) { throw "signtool 退出码 $LASTEXITCODE" }
    }
    finally {
        $plain = $null
    }
}

function Sign-SelfSigned {
    param([string] $File)
    $subject = "CN=Leidian Dev (Self-Signed)"
    $existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $subject }
    if ($existing) {
        $cert = $existing[0]
        Write-Host "使用已有证书: $($cert.Thumbprint)"
    }
    else {
        Write-Host "正在创建本机「当前用户」下的代码签名证书（自签名）..."
        $cert = New-SelfSignedCertificate `
            -Subject $subject `
            -Type CodeSigning `
            -KeySpec Signature `
            -KeyUsage DigitalSignature `
            -KeyAlgorithm RSA `
            -KeyLength 2048 `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -NotAfter (Get-Date).AddYears(5)
        Write-Host "已创建，指纹: $($cert.Thumbprint)"
    }
    Set-AuthenticodeSignature -FilePath $File -Certificate $cert -HashAlgorithm SHA256
    Write-Host "已签名（自签名）: $File"
    Write-Warning "自签名无法通过 Windows 智能应用控制；要公开发布需购买正式代码签名证书。"
}

# --- main ---
if ($SelfSigned) {
    Sign-SelfSigned -File $Exe
    exit 0
}

if (-not $PfxPath -or -not (Test-Path -LiteralPath $PfxPath)) {
    Write-Host @"
未指定有效的 .pfx 路径。

方式 A（正式签名）：
  1) 向 DigiCert / Sectigo 等购买「代码签名证书」，导出为 .pfx
  2) 安装 Windows SDK（含 signtool）
  3) 运行（密码用安全方式输入）：
     `$pwd = Read-Host -AsSecureString
     .\sign_leidian.ps1 -PfxPath 'C:\path\cert.pfx' -PfxPassword `$pwd

  或设置环境变量 LEIDIAN_PFX、LEIDIAN_PFX_PASSWORD 后执行本脚本（不推荐把密码长期写进环境变量）。

方式 B（仅本机试跑）：
  .\sign_leidian.ps1 -SelfSigned

"@
    exit 1
}

if (-not $PfxPassword) {
    if ($env:LEIDIAN_PFX_PASSWORD) {
        $PfxPassword = ConvertTo-SecureString -String $env:LEIDIAN_PFX_PASSWORD -AsPlainText -Force
    }
    else {
        $PfxPassword = Read-Host "请输入 .pfx 密码" -AsSecureString
    }
}

$signtool = Find-SignTool
if (-not $signtool) {
    Write-Error "未找到 signtool.exe。请安装「Windows 11 SDK」或「Windows 10 SDK」（勾选 Signing Tools for Desktop Apps）。"
}

Write-Host "使用 signtool: $signtool"
Sign-WithPfx -Tool $signtool -File $Exe -Pfx $PfxPath -SecPwd $PfxPassword
Write-Host "签名完成: $Exe"
Get-AuthenticodeSignature -FilePath $Exe | Format-List Status, StatusMessage, SignerCertificate
