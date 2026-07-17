# Особенности Micro-Cap 12

Поведение, найденное экспериментом при написании драйвера. В мануале этого нет, а один
пункт мануал прямо опровергает. Micro-Cap заброшен с 2019 года — ни поддержки, ни сайта,
спросить не у кого, поэтому записано здесь.

Проверено на Micro-Cap 12.2.0.3 (64-bit).

## Запуск

**Пути — только плоские имена в папке `DATA` самого Micro-Cap.** Подпапка приводит к
`Error No such file or directory` и пустому результату:

```
_mcp/circuit.CKT /A /NOF "_mcp/out"    → ничего не создаётся
circuit.CKT /A /NOF "out"              → работает
```

**Micro-Cap пишет `.DOC`-лог рядом с batch-файлом.** В нём настоящий текст ошибок и
статистика солвера — узлы, итерации Ньютона-Рафсона, отвергнутые решения, тайминги:

```
Circuit     Analog  Total       Rejected   Run    Setup
BATCH.CIR /A    2   1004        0          1.969  1.938
Total runs with Error/Warnings 0
```

Строки ошибок **префиксованы именем файла схемы**, поэтому поиск по `^Error` теряет их
все:

```
FOO.CIR Error Can't plot noise with other expressions.
```

**Ошибки в логе относятся к конкретной схеме.** Если склеить все ошибки батча и показать
их каждой схеме без выходного файла, одна сломанная схема «заразит» своим диагнозом всех
соседей по пачке.

**Расширения численного вывода — не только те, что в мануале.** Справка утверждает, что
Micro-Cap добавляет «(.TNO, .ANO, or .DNO)». Это неверно:

| Анализ | Ключ | Расширение |
|---|---|---|
| Transient | `/T` | `.TNO` |
| AC | `/A` | `.ANO` |
| DC | `/D` | `.DNO` |
| Harmonic Distortion | `/HD` | **`.HNO`** |
| Intermodulation Distortion | `/ID` | **`.INO`** |
| Stability | `/STABILITY` | **`.SNO`** |

**Запуск дорогой.** Каждый старт заново грузит библиотеку компонентов (11 МБ,
«Loading Component Files»), и это доминирует над временем расчёта: прогон, который солвер
считает 0.17 с, занимает 0.8 с целиком. Один batch-файл принимает много схем — старт
амортизируется.

## Получение чисел

**Схемы из поставки по умолчанию не экспортируют ничего.** Численный вывод — это флаг
`OUTPUT` в `Options=` каждого блока `[WaveForm]`. Он выставлен примерно у 36 схем из 475:
остальные рисовали, чтобы смотреть, а не выгружать.

```ini
[WaveForm]
Analysis=AC
XExp=F
YExp=Mag(v(OUT))
Options=OUTPUT,LINEARY    ; ← без OUTPUT таблицы не будет
```

**`NPts=0` в `[Limits]` экспортирует одну строку.** Таких схем 22. Эффект коварный:
генератор на 555 из поставки при родных настройках выглядит мёртвым.

```
NPts=0    →   1 строка  | v(OUT) 0.482..0.482 В
NPts=200  → 200 строк   | v(OUT) 0.229..9.964 В
```

**`Num Out Low="TMIN"` не резолвится в batch.** Границы диапазона задаются символически в
секции анализа (`[Transient]`, `[AC]`, …). Интерактивно это работает, в batch —
`Low Range Error: Unknown identifier 'TMIN'` и пустая таблица. Нужны конкретные значения
из `[Limits]`.

**DC-свипу нужен источник.** Micro-Cap заводит блок `[Limits]` для каждого типа анализа
независимо от того, настраивал его автор или нет. Схема выглядит DC-способной, а
источника не называет:

```ini
[Limits]
Analysis=DC
I1Range=10,0,.5     ; дефолтная болванка
I1=V1               ; ← без этой строки: Error Source not found
```

**Написания анализов внутри `.CIR`** — сокращённые, без пробелов: `HmDistortion`,
`ImDistortion`, `DynamicAC`, `DynamicDC`. `DynamicAC` и `DynamicDC` **не имеют трасс
вообще** — они подписывают значения прямо на схеме, экспортировать нечего.

## Чтение вывода

**Строка единиц позиционная, а не «одна на колонку».** У безразмерных величин единиц нет,
и строку нельзя разбить и сопоставить по порядку — колонки выровнены по правому краю, и
единицы совпадают с ними по позиции окончания:

```
            F Mag(v(S3)/v(In)) mag(v(S3)/v(S2))
         (Hz)
    70.000000       37.383410m      673.569953m
```

У полностью безразмерной таблицы строка единиц пустая.

**Числа приходят в трёх написаниях:**

```
70.000000      обычное
5.000E+00      научная нотация
37.383410m     SI-суффикс (f p n u m k MEG G T)
994.975MEG
```

**`NA`** означает «значение не определено» — например, фаза на первой точке AC. Требование
«в строке только числа» обрывает на ней таблицу и теряет остальные 200 строк.

**Цифровые колонки несут логические состояния** `X`, `Z`, `R`, `F`. У смешанной
аналого-цифровой схемы они стоят в одной таблице с нормальными аналоговыми колонками:

```
         T    V(In)   V(Out) D(Convert) D(B0)
      0.00     7.00     8.00          1     X
```

Выбросив строку из-за `X`, теряешь `V(In)` и `V(Out)`.

**Осторожно с `F`.** Первая колонка AC-таблицы называется `F` (частота), и это же —
логическое состояние «falling». Имена колонок нужно проверять строже, чем значения ячеек,
иначе парсер перестанет узнавать заголовки AC-таблиц.

**В файле есть и другие таблицы.** Рабочая точка и параметры моделей структурно
идентичны кривым и идут **раньше** них. Отличать нужно по заголовку секции:

```
Interpolated Waveform Values     ← кривые
DC Operating Point Voltages      ← не кривые
Model parameters for devices ... ← не кривые
```

**Ошибки Micro-Cap пишет прямо в файл**, на место таблицы:

```
Interpolated Waveform Values
============================
Low Range Error: Unknown identifier 'TMIN'.
```

## Окно

**Headless-режима нет.** В batch Micro-Cap всё равно открывает окно и рисует графики по
ходу — мануал об этом честно предупреждает.

**`STARTUPINFO.wShowWindow = SW_HIDE` не работает.** Это лишь подсказка для первого
`ShowWindow`, а Micro-Cap показывает окна явно. Замер: окно было видно в 9 замерах из 10.

**Подавление и экспорт картинок несовместимы.** Micro-Cap рисует график *через* окно:

| Режим | Окно на экране | Картинки |
|---|---|---|
| без подавления | 97% времени | нормальные |
| `ShowWindow(SW_HIDE)` | 10% | **чёрный JPEG** |
| увод за экран | 47% | **отсутствуют** |

Чёрный JPEG — валидный файл, проверка формата его пропустит. Ловится только по размеру:
53 КБ против 315 КБ у настоящего графика.

Вывод: гасить окно можно только когда картинка не запрошена.

## Generating a `.CIR` from scratch

Facts that make schematic generation work (the driver ships a bounded
generator — a source and a series chain of two-terminal passives):

**Shape and component definitions are built-in.** A `.CIR` that places parts by
name (`Resistor`, `Capacitor`, `Ground`, ...) without embedding any `[shapedef]`
or `[compdef]` still opens and simulates. So a generator needs only `[Main]`,
`[Comp]`/`[Attr]` placements, `[Wire]` segments, `[Grid Text]` node labels, and
`[Limits]`.

**Pin geometry lives in `Standard.cmp`**, in grid units (×8 for pixels):

```
[compdef]
Name=Resistor
Pin="Plus",6,0,-10,-4     ; Plus at grid (6,0) = 48 px
Pin="Minus",0,0,-14,-4    ; Minus at grid (0,0)
```

Every supported two-terminal part — R, C, L, Battery, Voltage Source — shares
this layout: Minus at (0,0), Plus at (6,0), horizontal at `Rot=0`. Knowing the
real pin positions is the difference between building the intended circuit and
whatever Micro-Cap extracts from misplaced wires (a guessed vertical source
left `V(OUT)=0`).

**A node is named by a `[Grid Text]` label at its wire coordinate**, e.g.
`[Grid Text]
Text="OUT"
Px=160,128`.

**A plot expression needs `Plt`/`AliasID`/`Enable`**, or Micro-Cap reports
"Must select an expression to plot".

**A `Voltage Source` (`Definition=VSpice`) takes a `VALUE` attribute** in
Micro-Cap syntax, e.g. `DC=0 AC=1` for an AC probe or a `PULSE ...` line for
transient — not the SPICE `AC 1` spelling.

Verified by generating an RC low-pass that reproduces `1/sqrt(2)` at the cutoff,
a resistive divider at exactly 0.5, an RL high-pass, and a charging transient.

### Parallel branches and active components

**Parallel branches work with the same passive geometry.** Elements sharing a
node just need their own wire down to their own ground. A series R feeding a
parallel L-C tank resonates at `1/(2*pi*sqrt(LC))` as it should. The generator
supports series chains plus parallel shunt branches (RC/RL/RLC, dividers,
tanks).

**Active components hit a wall — geometry solved, instantiation not.** The pin
geometry reads straight from `Standard.cmp`:

```
Opamp: Plus in(0,0) Minus in(0,6) VEE(4,7) Output(9,3) VCC(4,-1)   ; grid units
NPN:   Collector(3,-3) Base(0,0) Emitter(3,3)
```

The near-ideal LEVEL=1 op-amp needs no external supply (VCC/VEE float in the
shipped OPAMP1). But an op-amp placed in a *minimal* generated `.CIR` — even
OPAMP1's own op-amp block copied verbatim, model in `[Text Area]`, output
labelled at the pin — fails to extract a netlist: `Can't find label 'OUT'`.
The working OPAMP1 carries many more sections ([Schematic], [Object], [Page],
...), one of which the schematic netliser needs to instantiate a macro/
subcircuit component like the op-amp. Passives need none of that. Cracking
active-component generation means reverse-engineering that macro infrastructure
— genuine further work, so op-amps and transistors are not generated. A `.CKT`
netlist with an op-amp subcircuit already simulates, if a drawn schematic is
not required.
