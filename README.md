<div align="center">

# microcap-mcp

**ИИ-агент строит, считает и рисует аналоговые схемы в Micro-Cap 12.**

[![tests](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#установка)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io/)

**Русский** · [English](README.en.md)

<img src="docs/assets/hero-ce.svg" width="410" alt="Сгенерированный каскад с общим эмиттером, отрисованный 1:1 с Micro-Cap, с наложенной рабочей точкой"> <img src="docs/assets/plot-ac.jpg" width="410" alt="АЧХ, посчитанная Micro-Cap">

<sub>Схему нарисовал сам сервер (1:1 с Micro-Cap, с рабочей точкой) · график посчитал Micro-Cap</sub>

</div>

---

Работает через штатный batch-режим Micro-Cap (`MC12 @batch.bat`) — headless, без GUI-автоматизации и без модификации программы.

## Что делает

🔧 **Строит схемы с нуля** — 8 генераторов каскадов (общий эмиттер, повторитель, MOSFET, дифпара, токовое зеркало, каскод, ОУ, RLC) с авто-смещением. Каждый сверен с теорией: усиление, рабочая точка, резонанс.

📊 **Считает** — transient / AC / DC / искажения / устойчивость, рабочая точка, свипы. Комплексный вывод как `{re, im}`, диагностика солвера.

🎨 **Рисует** — сам рендерит `.CIR` в SVG **1:1 с Micro-Cap**: родные символы из библиотеки MC, сетка, все повороты и отражения, раскладка подписей. Накладывает напряжения рабочей точки. `/IC` в batch-режиме MC этого не умеет — а сервер умеет.

<div align="center">
<img src="docs/assets/diffpair.svg" height="200" alt="Дифференциальная пара"> <img src="docs/assets/cascode.svg" height="200" alt="Каскод"> <img src="docs/assets/opamp.svg" height="200" alt="Усилитель на ОУ">
</div>

## Установка

```bash
git clone https://github.com/monoxide-xen/microcap-mcp
cd microcap-mcp
uv sync
```

Нужен Windows, Python 3.11+ и установленный [Micro-Cap 12](https://spectrum-soft.com/) (фримварь). Подключение к MCP-клиенту:

```jsonc
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/microcap-mcp", "run", "microcap-mcp"],
      "env": { "MICROCAP_HOME": "E:/Tools/MC12" }   // если автопоиск не нашёл
    }
  }
}
```

## Инструменты

| Инструмент | Что делает |
|---|---|
| `simulate` · `sweep` · `plot` | посчитать SPICE-нетлист, свип по `.DEFINE`, график в JPEG |
| `generate_transistor_amplifier` · `_emitter_follower` · `_mosfet_amplifier` | каскады на транзисторе / MOSFET, авто-смещение под середину питания |
| `generate_differential_pair` · `_current_mirror` · `_cascode` · `_amplifier` | дифпара, зеркало, каскод, усилитель на ОУ |
| `generate_schematic` | источник + R/C/L последовательно и параллельно (RC/RL/RLC, делители, контуры) |
| `simulate_schematic` · `plot_schematic` | посчитать / построить график произвольной `.CIR` |
| `draw_schematic` · `annotate_schematic` | нарисовать `.CIR` в SVG (1:1 с MC) · с рабочей точкой на схеме |
| `simulate_example` · `search_examples` · `get_example` · `describe_example` · `list_domains` | ~490 эталонных схем из поставки MC: поиск, исходник, анализы |

Плюс **ресурсы** `microcap://guide` и `microcap://domains` и **промпт** `analyse_circuit` — чтобы агент не дал MC молча соврать.

## Тесты

```bash
uv run pytest        # 138 юнит без Micro-Cap; +30 интеграционных против физики (нужен MC)
```

Без Micro-Cap интеграционные пропускаются — CI остаётся зелёным.

## Как это устроено

- [Особенности Micro-Cap](docs/micro-cap-notes.md) — поведение, которого нет в мануале (а местами оно мануалу противоречит).
- `eval/harness.py` — прогон всех ~490 схем поставки с раскладкой провалов по причинам (сейчас 88% отвечают).

## Лицензия

[MIT](LICENSE) на код репозитория. Micro-Cap 12 принадлежит Spectrum Software — не включён, не перезалит, не модифицирован: используется его документированный CLI.
