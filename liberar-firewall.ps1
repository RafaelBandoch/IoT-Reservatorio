# Libera no firewall do Windows o que os ESP32 precisam para falar com
# este computador: broker MQTT com TLS, gravação pelo Wi-Fi (OTA) e mDNS.
# Rodar uma vez, no PowerShell como administrador:
#
#   powershell -ExecutionPolicy Bypass -File .\liberar-firewall.ps1
#
# - 8883/TCP de entrada: os ESP32 conectam no broker (Mosquitto com TLS).
# - 3233/TCP de entrada: o ESP32 se conecta de volta ao computador nesta
#   porta para baixar o firmware (o ota.sh fixa a porta com -P 3233).
# - 5353/UDP de entrada: respostas do mDNS, para achar a placa por
#   rsv-medidor-01.local e rsv-controlador-01.local, e para os ESP32
#   acharem este computador como aquanivel.local (anunciar_broker.py).
#
# O hotspot do celular costuma entrar no Windows como rede "Pública", por
# isso as regras valem para todos os perfis. Para desfazer:
#   Remove-NetFirewallRule -DisplayName "AquaNivel*"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Output "ERRO: abra o PowerShell como administrador (botao direito > Executar como administrador) e rode de novo."
    exit 1
}

$regras = @(
    @{ Nome = "AquaNivel broker MQTT TLS (8883/TCP)"; Protocolo = "TCP"; Porta = 8883 },
    @{ Nome = "AquaNivel OTA (3233/TCP)"; Protocolo = "TCP"; Porta = 3233 },
    @{ Nome = "AquaNivel mDNS (5353/UDP)"; Protocolo = "UDP"; Porta = 5353 }
)

foreach ($r in $regras) {
    if (Get-NetFirewallRule -DisplayName $r.Nome -ErrorAction SilentlyContinue) {
        Write-Output "ja existe: $($r.Nome)"
        continue
    }
    try {
        New-NetFirewallRule -DisplayName $r.Nome -Direction Inbound -Action Allow `
            -Protocol $r.Protocolo -LocalPort $r.Porta -Profile Any -ErrorAction Stop | Out-Null
        Write-Output "criada: $($r.Nome)"
    } catch {
        Write-Output "FALHOU: $($r.Nome): $($_.Exception.Message)"
    }
}
