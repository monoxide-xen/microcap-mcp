<div align="center">

# microcap-mcp

**MCP-сервер для SPICE-симулятора Micro-Cap 12**

Даёт LLM-агенту считать аналоговые схемы: запускать анализы, гонять свипы,
получать данные кривых и графики.

[![tests](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#установка)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io/)

**Русский** · [English](README.en.md)

</div>

---

Работает через штатный batch-режим Micro-Cap (`MC12 @batch.bat`) — без GUI-автоматизации
и без модификации программы. Прогон проходит headless, процесс закрывается сам.

## Требования

- Windows
- Python 3.11+
- Установленный [Micro-Cap 12](https://spectrum-soft.com/) (фримварь)

## Установка

```bash
git clone https://github.com/monoxide-xen/microcap-mcp
cd microcap-mcp
uv sync
```

Подключение к MCP-клиенту:

```json
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/microcap-mcp", "run", "microcap-mcp"]
    }
  }
}
```

### Где лежит Micro-Cap

Драйвер сам просматривает типовые места (`MC12`, `Micro-Cap 12` в корне дисков и в
`Program Files`) и берёт `mc12_64.exe` либо `mc12.exe`. Но Micro-Cap не ставится в
`Program Files` — он пишет в собственную папку и требует прав на запись, — поэтому у
многих он лежит где угодно.

Если автопоиск не справился, укажите папку с исполняемым файлом через `MICROCAP_HOME` —
она используется как есть:

```jsonc
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/microcap-mcp", "run", "microcap-mcp"],
      "env": { "MICROCAP_HOME": "E:/Tools/MC12" }   // ← ваш путь
    }
  }
}
```

Или переменной окружения — PowerShell:

```powershell
$env:MICROCAP_HOME = "E:\Tools\MC12"
```

## Инструменты

| Тулза | Что делает |
|---|---|
| `simulate` | гоняет SPICE-нетлист, возвращает данные кривых и статистику солвера |
| `sweep` | прогоняет схему по значениям `.DEFINE`-параметра |
| `plot` | отдаёт график, отрисованный Micro-Cap, как JPEG |
| `simulate_schematic` | считает произвольную `.CIR`-схему — правишь референс и гоняешь копию |
| `generate_schematic` | рисует `.CIR` с нуля: источник + R/C/L последовательно и параллельно (геометрия из библиотеки MC) |
| `generate_amplifier` | рисует `.CIR` усилителя на ОУ (инвертирующий/неинвертирующий) под заданное усиление |
| `simulate_example` | считает одну из ~490 схем из поставки Micro-Cap |
| `describe_example` | какие анализы схема поддерживает и что строит — без запуска |
| `list_domains` | 43 домена эталонных схем и их размеры |
| `search_examples` | поиск по эталонным схемам |
| `get_example` | исходник эталонной схемы |

Комплексный вывод (AC, S-параметры, Smith) возвращается как `{"re", "im"}`.

Поддерживаемые анализы: `transient`, `ac`, `dc`, `harmonic_distortion`,
`intermodulation_distortion`, `stability`.

Помимо тулзов сервер отдаёт **ресурсы** — `microcap://guide` (как не дать
Micro-Cap молча соврать: выбор анализа, доверие к солверу, чтение комплексных
данных, правила SPICE) и `microcap://domains` (карта 43 доменов эталонных схем)
— и **промпт** `analyse_circuit` для типового рабочего цикла.

## Пример

Агент пишет нетлист и получает числа обратно:

```
RC Lowpass
V1 IN 0 AC 1
R1 IN OUT 1K
C1 OUT 0 159.155N
.AC DEC 21 10 100K
.PRINT AC V(OUT)
.END
```

```jsonc
// simulate(netlist, analysis="ac")
{
  "columns": ["F", "V(OUT)"],
  "units":   ["Hz", "V"],
  "points":  85,
  "data": {
    "F":      [10.0, 100.0, 1000.0, 10000.0],
    "V(OUT)": [1.0,  0.995, 0.70710, 0.09950]
  },
  "solver": { "nodes": 2, "iterations": 88, "rejected_solutions": 0,
              "rejected_fraction": 0.0, "iterations_per_solution": 2.0 }
}
```

Частота среза RC-цепочки `1/(2πRC)` = 1000 Гц, где усиление должно быть `1/√2 ≈ 0.70711`.

`solver.rejected_fraction` — доля отвергнутых шагов. Это сигнатура топологии, а не
тревога: у импульсных преобразователей 18–23%, у линейных схем 0–5% — солвер режет шаг на
каждом фронте коммутации, и это нормально. Свыше 15% в ответ добавляется пояснение.

## Рабочий пример: охарактеризовать фильтр

Цикл, который проповедует `microcap://guide` — найти референс, а не изобретать:

```python
search_examples("bandpass")          # → BPFILT (домен Filters); поиск семантический,
                                     #   хотя схема названа криптично
describe_example("BPFILT")           # → поддерживает AC; строит Mag(v(S3)/v(In))
r = simulate_example("BPFILT", analysis="ac")
```

Из данных: пик усиления **37.5 на 627 Гц**, полоса по −3 дБ **453…1115 Гц**
(Q ≈ 0.95). Дальше `get_example` + `simulate_schematic` — правишь номинал и гоняешь копию.

## Тесты

```bash
uv run pytest
```

98 юнит-тестов без Micro-Cap (парсер, `.CIR`, чтение лога — чистый текст) плюс 12
интеграционных, которые прогоняют весь стек против физики с известным ответом и требуют
установленного Micro-Cap:

```bash
MICROCAP_HOME=C:/MC12 uv run pytest tests/test_integration.py
```

Без Micro-Cap интеграционные пропускаются, поэтому CI остаётся зелёным.

## Оценка на корпусе

`eval/harness.py` прогоняет все схемы из поставки Micro-Cap и раскладывает провалы по
причинам:

```bash
uv run python eval/harness.py --all --window    # полный прогон с окном прогресса
uv run python eval/harness.py --domain Filters  # один домен
```

Текущий результат — 762 из 866 прогонов, на которые схема способна ответить (88%).
Остальные провалы в основном не на стороне драйвера: схемы без земли, битые ссылки на
узлы, ненастроенные DC-блоки.

Флаг `--compare <прошлый.jsonl>` диффит результат по схемам: суммарный процент прячет
размены, когда одна правка чинит одни схемы и ломает другие.

## Документация

- [Особенности Micro-Cap](docs/micro-cap-notes.md) — поведение, которого нет в мануале
  (а местами оно мануалу противоречит). Пригодится всем, кто автоматизирует MC12.

## Лицензия

[MIT](LICENSE) — на код в этом репозитории.

Micro-Cap 12 принадлежит Spectrum Software. Он не включён, не перезалит и не
модифицирован: проект использует его документированный интерфейс командной строки.
