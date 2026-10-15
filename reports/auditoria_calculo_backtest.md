# Auditoría del cálculo del backtester

Fecha de ejecución: 2026-10-14. Auditoría estática y pruebas sintéticas locales; no se conectó a MT5 ni se enviaron órdenes.

## Hallazgos

### Defecto confirmado y corregido

En `src/backtester.py`, rama de salida intravela dentro de `Backtester.run`, las condiciones de toque intravela del SL se evaluaban antes de comprobar si la apertura de esa vela ya había saltado por encima del TP (BUY) o por debajo del TP (SELL). Si la vela abría con un gap ganador y luego su rango OHLC incluía también el SL, la simulación devolvía SL. Eso viola el orden temporal: la apertura ocurre primero y el cierre del TP por gap debe ocurrir a la apertura antes de evaluar high/low.

Se cambió el orden para ambos lados: primero gap de SL, después gap de TP, luego toque intravela SL y finalmente toque intravela TP. Si la apertura queda dentro de los niveles y una sola vela toca ambos, continúa ganando el SL por criterio conservador. Los cuatro escenarios deterministas BUY/SELL y el gap para ambos lados están cubiertos en `tests/test_strategy.py`.

### Precio, PnL y costes

- Para sizing conservador, `pip_value_per_lot` sin selector usa el mayor tick value; el PnL usa `trade_tick_value_profit` o `trade_tick_value_loss` según el signo de la operación. Para cruces sin tick values ni conversión de divisa verificados, el cálculo fallback solo acepta USD quote/base.
- Swap no se simula. El spread/slippage/comisión de los JSON son parámetros asumidos, no tarifas XM medidas. No hay doble deducción de comisión en el PnL implementado; sí se incluye una vez en el coste estimado utilizado para dimensionar y una vez en el PnL realizado, que son dos usos distintos.
- Señal de la vela `t-1`; entrada en apertura de `t` ([`src/backtester.py:98-126`](../src/backtester.py)). No se utiliza el cierre futuro para decidir la entrada.
- BUY entra en `open + half_cost`; SELL entra en `open - half_cost`. El coste por ejecución es `(spread/2 + slippage) * pip_size` ([`src/backtester.py:74-75`](../src/backtester.py), [`src/backtester.py:116-121`](../src/backtester.py)).
- El SL es `open - side * ATR_distance`; TP es `open + side * RR * ATR_distance`. La salida compara OHLC de la vela actual con esos niveles y al cierre usa precio de gap o nivel ([`src/backtester.py:128-155`](../src/backtester.py)).
- El PnL convierte movimiento de precio a pips, multiplica por valor de pip y lotes; la comisión total configurada se resta una vez por lote al cerrar. El campo `costs` es `gross_pnl - pnl`, por lo que incorpora spread, slippage de entrada y salida y comisión ([`src/backtester.py:84-94`](../src/backtester.py), [`src/backtester.py:164-181`](../src/backtester.py)).
- El dimensionamiento estima pérdida por distancia del SL más spread, dos ejecuciones de slippage y comisión, y limita los lotes por `RiskManager` ([`src/backtester.py:106-126`](../src/backtester.py); [`src/risk_manager.py:74-103`](../src/risk_manager.py)). La entrada adversa y salida adversa producen una distancia de pérdida de SL igual a la distancia configurada más el coste de ida y vuelta. Los precios SL/TP se anclan al open de referencia; esto es conservador respecto a la posición BUY/SELL con spread, pero debe distinguirse de cómo un broker calcula sus niveles respecto del precio ejecutado.
- El PnL usa el tick value de ganancia o pérdida según el signo; el sizing conserva el máximo de ambos valores ([`config.py:76-101`](../config.py)). Para cruces sin tick values ni conversión de divisa verificados, el fallback falla en lugar de asumir USD.
- Swap no se simula. El spread/slippage/comisión de los JSON son parámetros asumidos, no tarifas XM medidas. No hay doble deducción de comisión en el PnL implementado; sí se incluye una vez en el coste estimado utilizado para dimensionar y una vez en el PnL realizado, que son dos usos distintos.
- El PnL usa el tick value de ganancia o pérdida según el signo; el sizing conserva el máximo de ambos valores. Ambos dependen de las especificaciones MT5 obtenidas para esa cuenta y fecha.
- Swap no se simula. El spread/slippage/comisión de los JSON son parámetros asumidos, no tarifas XM medidas. No hay doble deducción de comisión en el PnL implementado; sí se incluye una vez en el coste estimado utilizado para dimensionar y una vez en el PnL realizado, que son dos usos distintos.

### Por qué el informe presentaba 0% de ganadoras

Se reprodujo `reports/evaluate_agent_strategies.py` sobre `data/market_data.csv`. El informe usa especificación provisional EURUSDmicro con contrato 1.000, volumen máximo de símbolo 100 y aplica comisión de USD 7 por cada lote de volumen; el riesgo limita a 5 lotes en los trades examinados. Con estas entradas el coste de comisión consume las ganancias brutas. En `rules_balanced`, tres subperiodos de selección dan:

| Subperiodo | Trades | Ganancias brutas | Ganancias netas con comisión asumida USD 7/lote | PnL neto | Costes |
|---|---:|---:|---:|---:|---:|
| 1 | 17 | 7 | 0 | -607.25 | 606.90 |
| 2 | 14 | 7 | 0 | -480.15 | 499.80 |
| 3 | 12 | 3 | 0 | -443.99 | 428.40 |

Como diagnóstico de sensibilidad únicamente, al poner la comisión a cero (sin afirmar que sea la tarifa real), las ganadoras netas son 7, 6 y 3; PnL -12.25, +9.85 y -23.99. Esto demuestra que el 0% neto es sensible a la hipótesis de comisión, pero no prueba que la hipótesis USD 7 sea falsa. La tarifa XM para este símbolo/cuenta, el significado de “lote” en esa comisión y los swaps siguen sin verificarse; por ello ni los resultados actuales ni el contrafactual son evidencia de rentabilidad. Se mantuvo la configuración original y el evaluador continúa en `selected=None`, `NO TRADE`.

Comparación del JSON anterior con su regeneración tras corregir el caso de gap: mismos trades, mismos PnL y el mismo 0% de ganadoras netas en cada bloque. La corrección no alteró retrospectivamente estas velas; el fixture de gap nuevo sí comprueba el defecto corregido.

| Bloque | Antes PnL / ganadoras netas | Después PnL / ganadoras netas | Costes después |
|---|---:|---:|---:|
| 1 | -607.25 / 0 de 17 | -607.25 / 0 de 17 | 606.90 |
| 2 | -480.15 / 0 de 14 | -480.15 / 0 de 14 | 499.80 |
| 3 | -443.99 / 0 de 12 | -443.99 / 0 de 12 | 428.40 |

Un cálculo directo de las mismas señales con la especificación EURUSD estándar del proyecto (contrato 100.000) sí obtiene ganancias netas en los subperiodos, pero esa especificación no corresponde al símbolo micro solicitado; no es una comparación válida y no debe usarse como resultado.

## Métricas añadidas

El backtester añade por evaluación `buy_signals`, `sell_signals`, `opened_trades`, `closed_winning_trades`, `closed_losing_trades` y `closed_breakeven_trades`. Las operaciones abiertas al final se liquidan con salida EOD, de modo que en esta implementación el número de abiertas coincide con operaciones cerradas en el dataframe de resultado.

## Pruebas

- BUY con TP alcanzado sin tocar SL: cierre TP, PnL neto positivo y costes positivos.
- BUY con SL alcanzado: cierre SL, PnL neto negativo y costes positivos.
- SELL con TP alcanzado sin tocar SL: cierre TP, PnL neto positivo y costes positivos.
- SELL con SL alcanzado: cierre SL, PnL neto negativo y costes positivos.
- Vela que toca SL y TP con apertura interior: SL primero.
- Gap ganador al abrir y toque del SL en el rango posterior: TP por apertura para BUY y SELL.
- Contadores de señales y cierres separados.

Comando final de suite: `$env:TEMP=(Resolve-Path reports\pytest_forex_audit).Path; $env:TMP=$env:TEMP; .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_forex_audit\basetemp` — **105 passed**. La prueba aislada del gap también pasó: `.\.venv\Scripts\python.exe -m pytest -q tests/test_strategy.py -k 'gap_through'` — **1 passed, 22 deselected**. `compileall` y `git diff --check` pasaron.

Durante el desarrollo, el primer intento de `tests/test_strategy.py` tuvo un fallo de entorno porque `TemporaryDirectory` intentó escribir bajo `%TEMP%` fuera del workspace, y un fixture de gap inicialmente representaba de forma inválida una vela SELL. Se corrigió el fixture; con TEMP/TMP dentro del proyecto la suite completa pasó, incluyendo la prueba de serialización. No queda fallo de prueba conocido.

## Límites

La fuente histórica local no cuenta con procedencia autenticada en este análisis. Los OHLC no permiten conocer el orden intravela cuando el precio abre entre SL/TP y toca ambos; asumir SL primero es una aproximación conservadora, no un tick replay. No hay histórico de bid/ask, comisiones, swaps, conversión de divisas ni ejecución XM para validar costes. La corrección de gap arregla un defecto de simulación; no mejora ni certifica la estrategia.
