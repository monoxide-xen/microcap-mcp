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
| `simulate_example` | считает одну из ~490 схем из поставки Micro-Cap |
| `describe_example` | какие анализы схема поддерживает и что строит — без запуска |
| `list_domains` | 44 домена эталонных схем и их размеры |
| `search_examples` | поиск по эталонным схемам |
| `get_example` | исходник эталонной схемы |

Поддерживаемые анализы: `transient`, `ac`, `dc`, `harmonic_distortion`,
`intermodulation_distortion`, `stability`.

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
  "solver": { "nodes": 2, "iterations": 88, "rejected_solutions": 0 }
}
```

Частота среза RC-цепочки `1/(2πRC)` = 1000 Гц, где усиление должно быть `1/√2 ≈ 0.70711`.

`solver.rejected_solutions` — сколько решений отверг солвер. Ненулевое значение означает,
что прогон формально прошёл, но кривой лучше не доверять.

## Тесты

```bash
uv run pytest
```

60 тестов, Micro-Cap не требуется: парсер, работа с `.CIR` и чтение лога — чистая обработка
текста. Каждый тест закрывает конкретный баг на реальных данных Micro-Cap.

## Оценка на корпусе

`eval/harness.py` прогоняет все схемы из поставки Micro-Cap и раскладывает провалы по
причинам:

```bash
uv run python eval/harness.py --all --window    # полный прогон с окном прогресса
uv run python eval/harness.py --domain Filters  # один домен
```

Текущий результат — 728 из 866 прогонов, на которые схема способна ответить. Остальные
провалы в основном не на стороне драйвера: схемы без земли, битые ссылки на узлы,
ненастроенные DC-блоки.

## Документация

- [Особенности Micro-Cap](docs/micro-cap-notes.md) — поведение, которого нет в мануале
  (а местами оно мануалу противоречит). Пригодится всем, кто автоматизирует MC12.

## Лицензия

[MIT](LICENSE) — на код в этом репозитории.

Micro-Cap 12 принадлежит Spectrum Software. Он не включён, не перезалит и не
модифицирован: проект использует его документированный интерфейс командной строки.
