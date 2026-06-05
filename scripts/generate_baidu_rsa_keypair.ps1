param(
    [string]$OutputDir = "deploy-only/baidu-token-rsa",
    [int]$Bits = 3072
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001 | Out-Null

if ($Bits -lt 2048) {
    throw "RSA key size must be at least 2048 bits."
}

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if ($null -eq $openssl) {
    throw "openssl is required to generate the deployment RSA key pair."
}

$resolvedOutputDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDir))
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null

$privateKeyPath = Join-Path $resolvedOutputDir "baidu-token-private.pem"
$publicKeyPath = Join-Path $resolvedOutputDir "baidu-token-public.pem"

& $openssl.Source genpkey -algorithm RSA -pkeyopt "rsa_keygen_bits:$Bits" -out $privateKeyPath
& $openssl.Source rsa -pubout -in $privateKeyPath -out $publicKeyPath

Write-Host "Generated RSA private key: $privateKeyPath"
Write-Host "Generated RSA public key:  $publicKeyPath"
Write-Host "Keep the private key local to trusted clients. Submit only the public key to cloud-api."
