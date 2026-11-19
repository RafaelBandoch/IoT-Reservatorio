# Certificados raiz extras para o build

Se um antivírus (ex.: Avast com varredura de HTTPS) ou proxy intercepta o
HTTPS desta máquina, o `pip install` dentro do Docker falha com
`CERTIFICATE_VERIFY_FAILED`. Coloque aqui a raiz dele em PEM, com extensão
`.crt`, e o Dockerfile passa a confiar nela no build e em tempo de execução
(chamadas ao Telegram).

Os `.crt` desta pasta são específicos de cada máquina e ficam fora do git.
No Windows, a raiz do Avast pode ser exportada pelo `certmgr.msc`
(Autoridades de Certificação Raiz Confiáveis, "Avast Web/Mail Shield Root",
exportar como Base-64).
