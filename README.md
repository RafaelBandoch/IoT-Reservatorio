# Projeto N1 — Medidor de Nível de Água do Reservatório

## 1. Integrantes

* Anttonio Osorio Molinaro Maccagnini - @anttonio06
* Gabriel Lengert Guedes - @GabrielLengertGuedes
* João Pedro Alves de Lima - @CapJao
* João Vitor Paranhos - @joaoparanhoss
* Rafael Alexandre Alves Bandoch - @RafaelBandoch

---

## 2. Família temática

**Família 4 — Reservatório e nível**

---

## 3. Projeto

**Sistema de medição do nível de água de um reservatório utilizando Arduino, sensor ultrassônico e display LCD.**

---

## 4. Problema

É importante acompanhar a quantidade de água disponível em um reservatório para evitar que ele fique vazio ou transborde.

A verificação manual do nível pode ser imprecisa e exige que uma pessoa confira o reservatório constantemente. Além disso, saber apenas se o reservatório está "baixo", "médio" ou "alto" não fornece uma informação muito precisa sobre a quantidade de água disponível.

O projeto propõe desenvolver um sistema capaz de medir o nível de água do reservatório e apresentar essa informação em **porcentagem de 0% a 100%**, utilizando um display LCD.

---

## 5. Usuário ou contexto de uso

O sistema poderá ser utilizado em **residências, pequenos estabelecimentos, laboratórios ou outros ambientes que possuam um reservatório de água**.

O usuário poderá consultar o display para saber rapidamente a quantidade aproximada de água disponível no reservatório, sem precisar realizar uma verificação manual.

Exemplo:

```text
+----------------+
| NIVEL DA AGUA  |
|      73%       |
+----------------+
```

---

## 6. Objetivo da N1

Desenvolver um protótipo utilizando **Arduino** capaz de medir o nível de água de um reservatório e apresentar o resultado em porcentagem em um **display LCD**.

O sistema utilizará um **sensor ultrassônico** instalado na parte superior do reservatório para medir a distância entre o sensor e a superfície da água.

O Arduino irá processar essa distância, calcular o percentual aproximado de preenchimento do reservatório e apresentar o resultado no display.

### Exemplo

Considerando um reservatório com 40 cm de altura:

| Distância até a água | Nível aproximado |
| -------------------: | ---------------: |
|                40 cm |               0% |
|                30 cm |              25% |
|                20 cm |              50% |
|                10 cm |              75% |
|                 0 cm |             100% |

Os valores poderão ser ajustados de acordo com as dimensões do reservatório utilizado nos testes.

---

## 7. Componentes previstos

* Arduino Uno;
* Sensor ultrassônico;
* Display LCD 16x2 com módulo I2C;
* Protoboard;
* Jumpers;
* Cabo USB;
* Computador para programação;
* Reservatório para realização dos testes.

Os componentes poderão ser alterados conforme os testes realizados em laboratório.

---

## 8. Arquitetura inicial

O funcionamento inicial do sistema será:

```text
┌──────────────────────┐
│     RESERVATÓRIO     │
│                      │
│      ~~~~~~~~~       │
│      ~~~~~~~~~       │
│                      │
│  Sensor ultrassônico│
└──────────┬───────────┘
           │
           │ Distância
           ▼
    ┌──────────────┐
    │    ARDUINO   │
    │              │
    │ Processa a   │
    │ distância e  │
    │ calcula o %  │
    └──────┬───────┘
           │
           │ Nível (%)
           ▼
    ┌──────────────┐
    │  DISPLAY LCD │
    │              │
    │ NIVEL: 73%   │
    └──────────────┘
```

### Funcionamento

1. O sensor ultrassônico mede a distância entre ele e a superfície da água.
2. O Arduino recebe essa distância.
3. O programa utiliza a altura conhecida do reservatório para calcular o nível de preenchimento.
4. O resultado é convertido para uma porcentagem entre **0% e 100%**.
5. O percentual é apresentado no display LCD.
6. O sistema atualiza a informação continuamente.

---

## 9. Exemplo de visualização

O display poderá apresentar:

```text
+----------------+
| NIVEL DA AGUA  |
|      73%       |
+----------------+
```

Também poderá ser utilizada uma barra visual para facilitar a identificação do nível:

```text
+----------------+
| NIVEL: 73%     |
| [███████---]   |
+----------------+
```

A forma definitiva de apresentação será definida durante os testes.

---

## 10. Backlog inicial

| Tarefa                                     | Status  |
| ------------------------------------------ | ------- |
| Criar repositório                          | Feito |
| Preencher README inicial                   | Feito |
| Testar Arduino com semáforo                | Feito |
| Identificar e testar sensor ultrassônico   | A fazer |
| Identificar display LCD adequado           | A fazer |
| Listar componentes necessários             | A fazer |
| Testar comunicação do LCD com Arduino      | A fazer |
| Testar leitura da distância pelo sensor    | A fazer |
| Definir a altura do reservatório utilizado | A fazer |
| Criar cálculo do nível em porcentagem      | A fazer |
| Exibir o nível no LCD                      | A fazer |
| Testar diferentes níveis de água           | A fazer |
| Desenhar arquitetura do sistema            | A fazer |
| Registrar primeiro risco técnico           | A fazer |

---

## 11. Primeiro risco técnico

### Risco

O sensor ultrassônico pode apresentar **leituras imprecisas ou instáveis** dependendo da posição do sensor, do formato do reservatório e da movimentação da água.

### Possível impacto

Uma leitura incorreta da distância poderá fazer com que o Arduino calcule uma porcentagem de nível diferente da quantidade real de água.

Por exemplo, o reservatório poderá estar com aproximadamente 70% de água, mas o sistema apresentar 65% ou 75%.

### Como investigar

Serão realizados testes com diferentes níveis de água no reservatório, comparando a distância medida pelo sensor com a altura real da água.

Também serão avaliados:

* posicionamento do sensor;
* distância mínima e máxima de medição;
* estabilidade das leituras;
* necessidade de realizar várias medições e calcular uma média;
* precisão do cálculo da porcentagem.

Caso necessário, o código poderá utilizar uma média de várias leituras para reduzir pequenas oscilações.

---

## 12. Dúvidas para o professor

* O sensor ultrassônico é adequado para a medição do nível de água proposta?
* Podemos utilizar um display LCD 16x2 com módulo I2C?
* O nível deve ser apresentado obrigatoriamente de 0% a 100%?
* É necessário implementar algum alerta para nível muito baixo ou muito alto?
* Podemos utilizar uma barra visual junto com a porcentagem no display?
* A precisão da medição será um critério de avaliação do projeto?
* Podemos utilizar a média de várias medições para melhorar a estabilidade da leitura?

---

## 13. Próximos passos

1. Testar o sensor ultrassônico.
2. Testar o display LCD.
3. Definir as dimensões do reservatório.
4. Montar o circuito na protoboard.
5. Desenvolver o código de leitura do sensor.
6. Implementar o cálculo da porcentagem.
7. Exibir o nível no LCD.
8. Realizar testes com diferentes quantidades de água.
11. Avaliar a precisão das medições.
12. Registrar os resultados e atualizar o README.
