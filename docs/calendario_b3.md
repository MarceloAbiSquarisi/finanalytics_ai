# Calendário B3 — Dias não negociados (2017–2035)

> **Gerado automaticamente** por `scripts/generate_b3_calendar.py`. Não editar à mão.
>
> Fontes:
>  1. lib Python `holidays` (BR + subdiv=SP, categorias public+optional)
>  2. tabela `b3_no_trading_days` (auto-populada quando backfill retorna 0 ticks)

## Como B3 fecha

**Feriados nacionais fixos:** Confraternização (1/jan), Tiradentes (21/abr), Trabalhador (1/mai), Independência (7/set), Aparecida (12/out), Finados (2/nov), República (15/nov), Natal (25/dez).

**Lei 14.759/2023:** Consciência Negra (20/nov) virou feriado nacional a partir de 2024.

**Móveis (calculados via Páscoa):** Carnaval (segunda+terça), Quarta de Cinzas (meio-pregão tratado como holiday), Sexta-feira Santa, Corpus Christi.

**Vésperas:** Véspera de Natal (24/dez) e Véspera de Ano-Novo (31/dez) — meio-pregão B3, tratado como holiday completo (liquidez ruim para backtests).

**Atípicos** (`b3_no_trading_days`): dias sem pregão por motivos não-fixos — Aniversário Bovespa antecipado, decisões pontuais da B3, feriados estaduais SP que historicamente fecharam.

## Resumo por ano

| Ano | Feriados B3 | Atípicos | Dias úteis |
|---|---|---|---|
| 2017 | 15 | 0 | 248 |
| 2018 | 15 | 0 | 247 |
| 2019 | 15 | 0 | 250 |
| 2020 | 15 | 0 | 248 |
| 2021 | 15 | 2 | 246 |
| 2022 | 15 | 1 | 249 |
| 2023 | 15 | 1 | 247 |
| 2024 | 16 | 0 | 250 |
| 2025 | 16 | 0 | 249 |
| 2026 | 16 | 0 | 246 |
| 2027 | 16 | 0 | 248 |
| 2028 | 16 | 0 | 247 |
| 2029 | 16 | 0 | 246 |
| 2030 | 16 | 0 | 249 |
| 2031 | 16 | 0 | 249 |
| 2032 | 16 | 0 | 249 |
| 2033 | 16 | 0 | 250 |
| 2034 | 16 | 0 | 247 |
| 2035 | 16 | 0 | 246 |

## Detalhe ano-a-ano

### 2017

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2017-01-01 | dom | Feriado B3 | Confraternização Universal |
| 2017-02-27 | seg | Feriado B3 | Carnaval |
| 2017-02-28 | ter | Feriado B3 | Carnaval |
| 2017-03-01 | qua | Feriado B3 | Início da Quaresma |
| 2017-04-14 | sex | Feriado B3 | Sexta-feira Santa |
| 2017-04-21 | sex | Feriado B3 | Tiradentes |
| 2017-05-01 | seg | Feriado B3 | Dia do Trabalhador |
| 2017-06-15 | qui | Feriado B3 | Corpus Christi |
| 2017-09-07 | qui | Feriado B3 | Independência do Brasil |
| 2017-10-12 | qui | Feriado B3 | Nossa Senhora Aparecida |
| 2017-11-02 | qui | Feriado B3 | Finados |
| 2017-11-15 | qua | Feriado B3 | Proclamação da República |
| 2017-12-24 | dom | Feriado B3 | Véspera de Natal |
| 2017-12-25 | seg | Feriado B3 | Natal |
| 2017-12-31 | dom | Feriado B3 | Véspera de Ano-Novo |

### 2018

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2018-01-01 | seg | Feriado B3 | Confraternização Universal |
| 2018-02-12 | seg | Feriado B3 | Carnaval |
| 2018-02-13 | ter | Feriado B3 | Carnaval |
| 2018-02-14 | qua | Feriado B3 | Início da Quaresma |
| 2018-03-30 | sex | Feriado B3 | Sexta-feira Santa |
| 2018-04-21 | sab | Feriado B3 | Tiradentes |
| 2018-05-01 | ter | Feriado B3 | Dia do Trabalhador |
| 2018-05-31 | qui | Feriado B3 | Corpus Christi |
| 2018-09-07 | sex | Feriado B3 | Independência do Brasil |
| 2018-10-12 | sex | Feriado B3 | Nossa Senhora Aparecida |
| 2018-11-02 | sex | Feriado B3 | Finados |
| 2018-11-15 | qui | Feriado B3 | Proclamação da República |
| 2018-12-24 | seg | Feriado B3 | Véspera de Natal |
| 2018-12-25 | ter | Feriado B3 | Natal |
| 2018-12-31 | seg | Feriado B3 | Véspera de Ano-Novo |

### 2019

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2019-01-01 | ter | Feriado B3 | Confraternização Universal |
| 2019-03-04 | seg | Feriado B3 | Carnaval |
| 2019-03-05 | ter | Feriado B3 | Carnaval |
| 2019-03-06 | qua | Feriado B3 | Início da Quaresma |
| 2019-04-19 | sex | Feriado B3 | Sexta-feira Santa |
| 2019-04-21 | dom | Feriado B3 | Tiradentes |
| 2019-05-01 | qua | Feriado B3 | Dia do Trabalhador |
| 2019-06-20 | qui | Feriado B3 | Corpus Christi |
| 2019-09-07 | sab | Feriado B3 | Independência do Brasil |
| 2019-10-12 | sab | Feriado B3 | Nossa Senhora Aparecida |
| 2019-11-02 | sab | Feriado B3 | Finados |
| 2019-11-15 | sex | Feriado B3 | Proclamação da República |
| 2019-12-24 | ter | Feriado B3 | Véspera de Natal |
| 2019-12-25 | qua | Feriado B3 | Natal |
| 2019-12-31 | ter | Feriado B3 | Véspera de Ano-Novo |

### 2020

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2020-01-01 | qua | Feriado B3 | Confraternização Universal |
| 2020-02-24 | seg | Feriado B3 | Carnaval |
| 2020-02-25 | ter | Feriado B3 | Carnaval |
| 2020-02-26 | qua | Feriado B3 | Início da Quaresma |
| 2020-04-10 | sex | Feriado B3 | Sexta-feira Santa |
| 2020-04-21 | ter | Feriado B3 | Tiradentes |
| 2020-05-01 | sex | Feriado B3 | Dia do Trabalhador |
| 2020-06-11 | qui | Feriado B3 | Corpus Christi |
| 2020-09-07 | seg | Feriado B3 | Independência do Brasil |
| 2020-10-12 | seg | Feriado B3 | Nossa Senhora Aparecida |
| 2020-11-02 | seg | Feriado B3 | Finados |
| 2020-11-15 | dom | Feriado B3 | Proclamação da República |
| 2020-12-24 | qui | Feriado B3 | Véspera de Natal |
| 2020-12-25 | sex | Feriado B3 | Natal |
| 2020-12-31 | qui | Feriado B3 | Véspera de Ano-Novo |

### 2021

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2021-01-01 | sex | Feriado B3 | Confraternização Universal |
| 2021-01-25 | seg | 🔴 Atípico | B3 sem pregão |
| 2021-02-15 | seg | Feriado B3 | Carnaval |
| 2021-02-16 | ter | Feriado B3 | Carnaval |
| 2021-02-17 | qua | Feriado B3 | Início da Quaresma |
| 2021-04-02 | sex | Feriado B3 | Sexta-feira Santa |
| 2021-04-21 | qua | Feriado B3 | Tiradentes |
| 2021-05-01 | sab | Feriado B3 | Dia do Trabalhador |
| 2021-06-03 | qui | Feriado B3 | Corpus Christi |
| 2021-07-09 | sex | 🔴 Atípico | B3 sem pregão |
| 2021-09-07 | ter | Feriado B3 | Independência do Brasil |
| 2021-10-12 | ter | Feriado B3 | Nossa Senhora Aparecida |
| 2021-11-02 | ter | Feriado B3 | Finados |
| 2021-11-15 | seg | Feriado B3 | Proclamação da República |
| 2021-12-24 | sex | Feriado B3 | Véspera de Natal |
| 2021-12-25 | sab | Feriado B3 | Natal |
| 2021-12-31 | sex | Feriado B3 | Véspera de Ano-Novo |

### 2022

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2022-01-01 | sab | Feriado B3 | Confraternização Universal |
| 2022-02-28 | seg | Feriado B3 | Carnaval |
| 2022-03-01 | ter | Feriado B3 | Carnaval |
| 2022-03-02 | qua | Feriado B3 | Início da Quaresma |
| 2022-04-15 | sex | Feriado B3 | Sexta-feira Santa |
| 2022-04-21 | qui | Feriado B3 | Tiradentes |
| 2022-05-01 | dom | Feriado B3 | Dia do Trabalhador |
| 2022-06-16 | qui | Feriado B3 | Corpus Christi |
| 2022-09-07 | qua | Feriado B3 | Independência do Brasil |
| 2022-10-12 | qua | Feriado B3 | Nossa Senhora Aparecida |
| 2022-11-02 | qua | Feriado B3 | Finados |
| 2022-11-15 | ter | Feriado B3 | Proclamação da República |
| 2022-12-24 | sab | Feriado B3 | Véspera de Natal |
| 2022-12-25 | dom | Feriado B3 | Natal |
| 2022-12-30 | sex | 🔴 Atípico | B3 sem pregão |
| 2022-12-31 | sab | Feriado B3 | Véspera de Ano-Novo |

### 2023

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2023-01-01 | dom | Feriado B3 | Confraternização Universal |
| 2023-02-20 | seg | Feriado B3 | Carnaval |
| 2023-02-21 | ter | Feriado B3 | Carnaval |
| 2023-02-22 | qua | Feriado B3 | Início da Quaresma |
| 2023-04-07 | sex | Feriado B3 | Sexta-feira Santa |
| 2023-04-21 | sex | Feriado B3 | Tiradentes |
| 2023-05-01 | seg | Feriado B3 | Dia do Trabalhador |
| 2023-06-08 | qui | Feriado B3 | Corpus Christi |
| 2023-09-07 | qui | Feriado B3 | Independência do Brasil |
| 2023-10-12 | qui | Feriado B3 | Nossa Senhora Aparecida |
| 2023-11-02 | qui | Feriado B3 | Finados |
| 2023-11-15 | qua | Feriado B3 | Proclamação da República |
| 2023-12-24 | dom | Feriado B3 | Véspera de Natal |
| 2023-12-25 | seg | Feriado B3 | Natal |
| 2023-12-29 | sex | 🔴 Atípico | B3 sem pregão |
| 2023-12-31 | dom | Feriado B3 | Véspera de Ano-Novo |

### 2024

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2024-01-01 | seg | Feriado B3 | Confraternização Universal |
| 2024-02-12 | seg | Feriado B3 | Carnaval |
| 2024-02-13 | ter | Feriado B3 | Carnaval |
| 2024-02-14 | qua | Feriado B3 | Início da Quaresma |
| 2024-03-29 | sex | Feriado B3 | Sexta-feira Santa |
| 2024-04-21 | dom | Feriado B3 | Tiradentes |
| 2024-05-01 | qua | Feriado B3 | Dia do Trabalhador |
| 2024-05-30 | qui | Feriado B3 | Corpus Christi |
| 2024-09-07 | sab | Feriado B3 | Independência do Brasil |
| 2024-10-12 | sab | Feriado B3 | Nossa Senhora Aparecida |
| 2024-11-02 | sab | Feriado B3 | Finados |
| 2024-11-15 | sex | Feriado B3 | Proclamação da República |
| 2024-11-20 | qua | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2024-12-24 | ter | Feriado B3 | Véspera de Natal |
| 2024-12-25 | qua | Feriado B3 | Natal |
| 2024-12-31 | ter | Feriado B3 | Véspera de Ano-Novo |

### 2025

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2025-01-01 | qua | Feriado B3 | Confraternização Universal |
| 2025-03-03 | seg | Feriado B3 | Carnaval |
| 2025-03-04 | ter | Feriado B3 | Carnaval |
| 2025-03-05 | qua | Feriado B3 | Início da Quaresma |
| 2025-04-18 | sex | Feriado B3 | Sexta-feira Santa |
| 2025-04-21 | seg | Feriado B3 | Tiradentes |
| 2025-05-01 | qui | Feriado B3 | Dia do Trabalhador |
| 2025-06-19 | qui | Feriado B3 | Corpus Christi |
| 2025-09-07 | dom | Feriado B3 | Independência do Brasil |
| 2025-10-12 | dom | Feriado B3 | Nossa Senhora Aparecida |
| 2025-11-02 | dom | Feriado B3 | Finados |
| 2025-11-15 | sab | Feriado B3 | Proclamação da República |
| 2025-11-20 | qui | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2025-12-24 | qua | Feriado B3 | Véspera de Natal |
| 2025-12-25 | qui | Feriado B3 | Natal |
| 2025-12-31 | qua | Feriado B3 | Véspera de Ano-Novo |

### 2026

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2026-01-01 | qui | Feriado B3 | Confraternização Universal |
| 2026-02-16 | seg | Feriado B3 | Carnaval |
| 2026-02-17 | ter | Feriado B3 | Carnaval |
| 2026-02-18 | qua | Feriado B3 | Início da Quaresma |
| 2026-04-03 | sex | Feriado B3 | Sexta-feira Santa |
| 2026-04-21 | ter | Feriado B3 | Tiradentes |
| 2026-05-01 | sex | Feriado B3 | Dia do Trabalhador |
| 2026-06-04 | qui | Feriado B3 | Corpus Christi |
| 2026-09-07 | seg | Feriado B3 | Independência do Brasil |
| 2026-10-12 | seg | Feriado B3 | Nossa Senhora Aparecida |
| 2026-11-02 | seg | Feriado B3 | Finados |
| 2026-11-15 | dom | Feriado B3 | Proclamação da República |
| 2026-11-20 | sex | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2026-12-24 | qui | Feriado B3 | Véspera de Natal |
| 2026-12-25 | sex | Feriado B3 | Natal |
| 2026-12-31 | qui | Feriado B3 | Véspera de Ano-Novo |

### 2027

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2027-01-01 | sex | Feriado B3 | Confraternização Universal |
| 2027-02-08 | seg | Feriado B3 | Carnaval |
| 2027-02-09 | ter | Feriado B3 | Carnaval |
| 2027-02-10 | qua | Feriado B3 | Início da Quaresma |
| 2027-03-26 | sex | Feriado B3 | Sexta-feira Santa |
| 2027-04-21 | qua | Feriado B3 | Tiradentes |
| 2027-05-01 | sab | Feriado B3 | Dia do Trabalhador |
| 2027-05-27 | qui | Feriado B3 | Corpus Christi |
| 2027-09-07 | ter | Feriado B3 | Independência do Brasil |
| 2027-10-12 | ter | Feriado B3 | Nossa Senhora Aparecida |
| 2027-11-02 | ter | Feriado B3 | Finados |
| 2027-11-15 | seg | Feriado B3 | Proclamação da República |
| 2027-11-20 | sab | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2027-12-24 | sex | Feriado B3 | Véspera de Natal |
| 2027-12-25 | sab | Feriado B3 | Natal |
| 2027-12-31 | sex | Feriado B3 | Véspera de Ano-Novo |

### 2028

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2028-01-01 | sab | Feriado B3 | Confraternização Universal |
| 2028-02-28 | seg | Feriado B3 | Carnaval |
| 2028-02-29 | ter | Feriado B3 | Carnaval |
| 2028-03-01 | qua | Feriado B3 | Início da Quaresma |
| 2028-04-14 | sex | Feriado B3 | Sexta-feira Santa |
| 2028-04-21 | sex | Feriado B3 | Tiradentes |
| 2028-05-01 | seg | Feriado B3 | Dia do Trabalhador |
| 2028-06-15 | qui | Feriado B3 | Corpus Christi |
| 2028-09-07 | qui | Feriado B3 | Independência do Brasil |
| 2028-10-12 | qui | Feriado B3 | Nossa Senhora Aparecida |
| 2028-11-02 | qui | Feriado B3 | Finados |
| 2028-11-15 | qua | Feriado B3 | Proclamação da República |
| 2028-11-20 | seg | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2028-12-24 | dom | Feriado B3 | Véspera de Natal |
| 2028-12-25 | seg | Feriado B3 | Natal |
| 2028-12-31 | dom | Feriado B3 | Véspera de Ano-Novo |

### 2029

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2029-01-01 | seg | Feriado B3 | Confraternização Universal |
| 2029-02-12 | seg | Feriado B3 | Carnaval |
| 2029-02-13 | ter | Feriado B3 | Carnaval |
| 2029-02-14 | qua | Feriado B3 | Início da Quaresma |
| 2029-03-30 | sex | Feriado B3 | Sexta-feira Santa |
| 2029-04-21 | sab | Feriado B3 | Tiradentes |
| 2029-05-01 | ter | Feriado B3 | Dia do Trabalhador |
| 2029-05-31 | qui | Feriado B3 | Corpus Christi |
| 2029-09-07 | sex | Feriado B3 | Independência do Brasil |
| 2029-10-12 | sex | Feriado B3 | Nossa Senhora Aparecida |
| 2029-11-02 | sex | Feriado B3 | Finados |
| 2029-11-15 | qui | Feriado B3 | Proclamação da República |
| 2029-11-20 | ter | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2029-12-24 | seg | Feriado B3 | Véspera de Natal |
| 2029-12-25 | ter | Feriado B3 | Natal |
| 2029-12-31 | seg | Feriado B3 | Véspera de Ano-Novo |

### 2030

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2030-01-01 | ter | Feriado B3 | Confraternização Universal |
| 2030-03-04 | seg | Feriado B3 | Carnaval |
| 2030-03-05 | ter | Feriado B3 | Carnaval |
| 2030-03-06 | qua | Feriado B3 | Início da Quaresma |
| 2030-04-19 | sex | Feriado B3 | Sexta-feira Santa |
| 2030-04-21 | dom | Feriado B3 | Tiradentes |
| 2030-05-01 | qua | Feriado B3 | Dia do Trabalhador |
| 2030-06-20 | qui | Feriado B3 | Corpus Christi |
| 2030-09-07 | sab | Feriado B3 | Independência do Brasil |
| 2030-10-12 | sab | Feriado B3 | Nossa Senhora Aparecida |
| 2030-11-02 | sab | Feriado B3 | Finados |
| 2030-11-15 | sex | Feriado B3 | Proclamação da República |
| 2030-11-20 | qua | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2030-12-24 | ter | Feriado B3 | Véspera de Natal |
| 2030-12-25 | qua | Feriado B3 | Natal |
| 2030-12-31 | ter | Feriado B3 | Véspera de Ano-Novo |

### 2031

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2031-01-01 | qua | Feriado B3 | Confraternização Universal |
| 2031-02-24 | seg | Feriado B3 | Carnaval |
| 2031-02-25 | ter | Feriado B3 | Carnaval |
| 2031-02-26 | qua | Feriado B3 | Início da Quaresma |
| 2031-04-11 | sex | Feriado B3 | Sexta-feira Santa |
| 2031-04-21 | seg | Feriado B3 | Tiradentes |
| 2031-05-01 | qui | Feriado B3 | Dia do Trabalhador |
| 2031-06-12 | qui | Feriado B3 | Corpus Christi |
| 2031-09-07 | dom | Feriado B3 | Independência do Brasil |
| 2031-10-12 | dom | Feriado B3 | Nossa Senhora Aparecida |
| 2031-11-02 | dom | Feriado B3 | Finados |
| 2031-11-15 | sab | Feriado B3 | Proclamação da República |
| 2031-11-20 | qui | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2031-12-24 | qua | Feriado B3 | Véspera de Natal |
| 2031-12-25 | qui | Feriado B3 | Natal |
| 2031-12-31 | qua | Feriado B3 | Véspera de Ano-Novo |

### 2032

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2032-01-01 | qui | Feriado B3 | Confraternização Universal |
| 2032-02-09 | seg | Feriado B3 | Carnaval |
| 2032-02-10 | ter | Feriado B3 | Carnaval |
| 2032-02-11 | qua | Feriado B3 | Início da Quaresma |
| 2032-03-26 | sex | Feriado B3 | Sexta-feira Santa |
| 2032-04-21 | qua | Feriado B3 | Tiradentes |
| 2032-05-01 | sab | Feriado B3 | Dia do Trabalhador |
| 2032-05-27 | qui | Feriado B3 | Corpus Christi |
| 2032-09-07 | ter | Feriado B3 | Independência do Brasil |
| 2032-10-12 | ter | Feriado B3 | Nossa Senhora Aparecida |
| 2032-11-02 | ter | Feriado B3 | Finados |
| 2032-11-15 | seg | Feriado B3 | Proclamação da República |
| 2032-11-20 | sab | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2032-12-24 | sex | Feriado B3 | Véspera de Natal |
| 2032-12-25 | sab | Feriado B3 | Natal |
| 2032-12-31 | sex | Feriado B3 | Véspera de Ano-Novo |

### 2033

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2033-01-01 | sab | Feriado B3 | Confraternização Universal |
| 2033-02-28 | seg | Feriado B3 | Carnaval |
| 2033-03-01 | ter | Feriado B3 | Carnaval |
| 2033-03-02 | qua | Feriado B3 | Início da Quaresma |
| 2033-04-15 | sex | Feriado B3 | Sexta-feira Santa |
| 2033-04-21 | qui | Feriado B3 | Tiradentes |
| 2033-05-01 | dom | Feriado B3 | Dia do Trabalhador |
| 2033-06-16 | qui | Feriado B3 | Corpus Christi |
| 2033-09-07 | qua | Feriado B3 | Independência do Brasil |
| 2033-10-12 | qua | Feriado B3 | Nossa Senhora Aparecida |
| 2033-11-02 | qua | Feriado B3 | Finados |
| 2033-11-15 | ter | Feriado B3 | Proclamação da República |
| 2033-11-20 | dom | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2033-12-24 | sab | Feriado B3 | Véspera de Natal |
| 2033-12-25 | dom | Feriado B3 | Natal |
| 2033-12-31 | sab | Feriado B3 | Véspera de Ano-Novo |

### 2034

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2034-01-01 | dom | Feriado B3 | Confraternização Universal |
| 2034-02-20 | seg | Feriado B3 | Carnaval |
| 2034-02-21 | ter | Feriado B3 | Carnaval |
| 2034-02-22 | qua | Feriado B3 | Início da Quaresma |
| 2034-04-07 | sex | Feriado B3 | Sexta-feira Santa |
| 2034-04-21 | sex | Feriado B3 | Tiradentes |
| 2034-05-01 | seg | Feriado B3 | Dia do Trabalhador |
| 2034-06-08 | qui | Feriado B3 | Corpus Christi |
| 2034-09-07 | qui | Feriado B3 | Independência do Brasil |
| 2034-10-12 | qui | Feriado B3 | Nossa Senhora Aparecida |
| 2034-11-02 | qui | Feriado B3 | Finados |
| 2034-11-15 | qua | Feriado B3 | Proclamação da República |
| 2034-11-20 | seg | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2034-12-24 | dom | Feriado B3 | Véspera de Natal |
| 2034-12-25 | seg | Feriado B3 | Natal |
| 2034-12-31 | dom | Feriado B3 | Véspera de Ano-Novo |

### 2035

| Data | DoW | Tipo | Motivo |
|---|---|---|---|
| 2035-01-01 | seg | Feriado B3 | Confraternização Universal |
| 2035-02-05 | seg | Feriado B3 | Carnaval |
| 2035-02-06 | ter | Feriado B3 | Carnaval |
| 2035-02-07 | qua | Feriado B3 | Início da Quaresma |
| 2035-03-23 | sex | Feriado B3 | Sexta-feira Santa |
| 2035-04-21 | sab | Feriado B3 | Tiradentes |
| 2035-05-01 | ter | Feriado B3 | Dia do Trabalhador |
| 2035-05-24 | qui | Feriado B3 | Corpus Christi |
| 2035-09-07 | sex | Feriado B3 | Independência do Brasil |
| 2035-10-12 | sex | Feriado B3 | Nossa Senhora Aparecida |
| 2035-11-02 | sex | Feriado B3 | Finados |
| 2035-11-15 | qui | Feriado B3 | Proclamação da República |
| 2035-11-20 | ter | Feriado B3 | Dia Nacional de Zumbi e da Consciência Negra |
| 2035-12-24 | seg | Feriado B3 | Véspera de Natal |
| 2035-12-25 | ter | Feriado B3 | Natal |
| 2035-12-31 | seg | Feriado B3 | Véspera de Ano-Novo |

## Adicionar dia atípico manualmente

Quando descobrir um dia novo (B3 anuncia ou backfill detecta 0 ticks):

```sql
INSERT INTO b3_no_trading_days (target_date, notes)
VALUES ('YYYY-MM-DD', 'razão');
```

**Auto-populate:** `backfill_runner.run_one_item` chama `mark_b3_no_trading_day` quando `final='ok'` AND `ticks_returned=0`. Sem intervenção manual em 99% dos casos — basta tentar coletar pelo fluxo *Preencher agora* em /admin → Banco de Dados → Gaps.

## Re-gerar este calendário

```bash
docker exec finanalytics_api python scripts/generate_b3_calendar.py \
  > docs/calendario_b3.md
```
