# N1 (histórico)

Arquivos da primeira versão do projeto (N1), quando um único ESP32 media o
nível e acionava a bomba, simulada por um LED no GPIO26. Na N3 eles foram
substituídos e ficam aqui só como registro de onde o projeto partiu.

| Arquivo | Na N1 | Na N3 foi substituído por |
| --- | --- | --- |
| `sketch.ino` | firmware do ESP32 único | `medidor/` e `controlador/` |
| `painel-reservatorio.html` | painel falando direto com o broker | `painel/`, que usa só a API do backend |
| `collector/` | coletor MQTT para o PostgreSQL | `backend/` |
| `db/` | tabelas `leituras` e `eventos` e views | migrações Alembic em `backend/alembic/` |
| `limpar.sh` | zerava as tabelas da N1 | (sem equivalente) |
| `relay-mqtt.py`, `.env.publico` | bridge com o broker público | broker próprio com TLS no compose |
| `wokwi.toml`, `diagram.json`, `protoboard/`, `protoboard-bancada/` | simulação do `sketch.ino` no Wokwi | placas reais |
| `EXECUCAO.md` | guia de execução da N1 | `README.md` e `TESTE-DOIS-ESP32.md` |

O repositório na versão da N1 (com estes arquivos na raiz e o compose
original) é o commit `27c8613`: `git checkout 27c8613`.

Para compilar o `sketch.ino` hoje: `./compilar.sh n1` na raiz do repositório,
com um `credenciais.h` em `MQTT_TLS 0` (a N1 não fala com o broker com TLS).
