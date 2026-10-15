# Auditoría de especificaciones y costes: XM EURUSDmicro

Fecha: 2026-10-14. No se enviaron solicitudes de trading, no se modificaron órdenes/posiciones ni se habilitó trading real.

## Resultado MT5 de esta sesión

Se ejecutó `reports/inspect_mt5_readonly.py`, que llama `mt5.initialize(path=..., timeout=15000)` sin `login()`, sin credenciales de cuenta y sin `order_send()`. La lectura no logró establecer IPC con el terminal: `connected=false`, `reason=initialize_failed`, `error_code=-10003`; no se obtuvieron `symbol_info`, ticks ni historial. La inspección anterior guardada había informado `-10005`; el intento de esta sesión terminó con `-10003`. Por ello **ningún campo específico de la cuenta/símbolo puede declararse verificado por la API MT5**. El JSON no incluye IDs de cuenta, contraseña, tickets ni balance: [`mt5_account_inspection.json`](mt5_account_inspection.json).

El inspector ahora solicita al conectar estos datos exactos: contrato, volumen mínimo/paso/máximo, tick size y valores tick profit/loss/general, `point`, `digits`, spread de `symbol_info` y de cotización, moneda de beneficio/margen, modo de trading, antigüedad de cotización y agregados anonimizados de comisión/swap/PnL de deals cerrados del año previo. `symbol_info` no publica un tarifario de comisión; se consulta `history_deals_get` para la comisión realmente contabilizada en operaciones cerradas.

## Datos confirmados y pendientes

| Dato | Estado |
|---|---|
| `trade_contract_size` | API no disponible. XM publica para cuenta Micro que 1 lote = 1.000 unidades; es referencia general, no lectura del símbolo de la cuenta conectada. |
| `volume_min`, `volume_step`, `volume_max` | API no disponible. La página pública de XM para Micro indica 0,1; 0,01; 100 lotes, respectivamente. Debe cotejarse con el símbolo exacto de MT5. |
| `trade_tick_size`, `trade_tick_value`, `trade_tick_value_profit/loss` | No verificados por MT5. |
| `point`, `digits`, `spread`, `currency_profit`, `trade_mode` | No verificados por MT5. El spread público genérico tampoco sustituye el tick/spread del servidor. |
| Comisión efectiva | No verificada: no hubo historial MT5 accesible. No se debe tratar USD 7/lote como coste probado. |
| Swap y slippage | No verificados en la cuenta. El slippage futuro no puede conocerse por `symbol_info`; el histórico de deals permite medir ejecuciones realizadas frente a sus cotizaciones/órdenes si esos datos existen. |
| PnL histórico | No comparado contra operaciones cerradas MT5: la API no devolvió historial ni especificaciones de la cuenta. |

La FAQ pública de XM describe la escala general de las cuentas Micro (1 lote = 1.000 unidades; mínimo 0,1; máximo 100; paso 0,01) y señala que 0,1 Micro equivale a 100 unidades. Esto hace plausibles los supuestos de volumen del informe previo, pero no autentica `EURUSDmicro`, el servidor ni la cuenta personal. [XM: información sobre cuentas Micro y volumen](https://www.xm.com/help-center/trading-accounts/are-all-instruments-swap-free-for-ultra-low-account-holders)

XM indica que las comisiones, si aplican, se divulgan en “Spreads and Conditions”, pueden ser porcentuales o fijas; swaps/financiación se cargan o abonan según el instrumento y la cuenta. La misma política diferencia precios BID/ASK y describe ejecución al primer precio disponible bajo determinadas condiciones. Por tanto, la comisión no se deduce de `symbol_info` y debe verificarse para la entidad, tipo de cuenta e instrumento correctos. [XM: política de ejecución, costes y precios BID/ASK](https://www.xm.com/assets/pdf/docs/XM_Order-Execution-Policy.pdf) [XM: rollover y cálculo de swap](https://www.xm.com/help-center/trading-conditions/faq-why-are-rollover-rates-tripled)

## Defectos confirmados y correcciones

1. **Tick value incorrecto para PnL favorable.** El backtester usaba el máximo entre `trade_tick_value_profit` y `trade_tick_value_loss` para todas las operaciones. Eso es apropiado para dimensionamiento conservador, pero puede inflar el PnL de una operación ganadora cuando los valores difieren. `SymbolSpec.pip_value_per_lot` ahora acepta el signo del PnL; `Backtester` usa valor profit/loss correspondiente y mantiene el máximo para sizing. La prueba usa distintos tick values y cubre PnL ganador y pérdida.
2. **Spread histórico ignorado.** El recolector MT5 conservaba el campo `spread` en puntos, pero `load_csv` lo descartaba y el backtester aplicaba siempre `BacktestConfig.spread_pips`. Ahora `load_csv` y `fetch_mt5` lo preservan. El backtester lo convierte de puntos MT5 a pips con `SymbolSpec.point` y aplica la mitad del spread de la barra de entrada y la mitad de la barra de salida, más slippage por ejecución. Si no tiene puntos válidos, queda explícito `spread_model=configured_fixed_pips`; esto sigue siendo una hipótesis. El evaluador solo considera specs verificados si el manifiesto incluye `point`, ambos tick values, contrato/volúmenes, digits y moneda de beneficio.
3. **Comisión.** No hay doble resta dentro del PnL del backtest: comisión configurada se descuenta una vez al cerrar; también se incluye una vez en la estimación usada para calcular volumen. Sin tarifa real, esa estimación no representa una orden MT5. El valor por defecto del proyecto es USD 7/lote (`config.py`), y `app.py:try_open_trade` también lo suma a la pérdida de MT5 para sizing. Ese importe no ha sido verificado para esta cuenta, por lo que no debe considerarse una medición XM.
4. **Swap.** No se simula en el backtester. Para posiciones que cruzan rollover es un coste omitido hasta disponer de rates históricos o de swap del instrumento/cuenta. No se inventó una tarifa.

El PnL del backtester basado en ticks tampoco se puede certificar contra `order_calc_profit` para datos históricos con valores actuales: el API actual no conectó, y una conversión de tick value puede depender de moneda/tipo de cuenta. La comparación rigurosa requiere descargar el historial de deals de esa cuenta, cotejar volumen, precios de apertura/cierre, comisión y swap por posición, y calcular el neto como suma de profit + commission + swap. El inspector guardará agregados por símbolo, no identificadores.

## Verificación de comisión

1. En MT5, abrir Historial de cuenta, mostrar deals de EURUSDmicro, exportar el período y sumar `Commission` por entradas y salidas de posiciones cerradas; dividir el total absoluto por el volumen inicial emparejado para obtener comisión efectiva por lote round-turn. Revisar el campo `Swap` por separado.
2. Contrastar con la sección “Spreads and Conditions” correspondiente a la entidad/regulación, tipo exacto de cuenta (Micro/Ultra Low/etc.) y producto EURUSD del portal XM. Si la comisión está integrada en spread o se factura como deal balance separado, conciliar la columna en el estado de cuenta y no volver a deducirla desde el spread.
3. Ejecutar de nuevo `reports/inspect_mt5_readonly.py` con MT5 abierto y conectado manualmente a la cuenta deseada. Nunca usar `login()` desde el auditor.

## Pruebas de riesgo por volumen

Se añadieron fixtures MT5 sintéticas con tick size/values y mínimo/paso ilustrativos; no son cotizaciones de la cuenta. Para presupuesto de USD 10, la prueba verifica SL de 5/10/20 pips con volúmenes 2,0/1,0/0,5 lotes y que pérdida al stop no excede el presupuesto. Otra comprueba el rechazo: un mínimo de 0,1 lotes con stop de 50 pips arriesgaría USD 5 frente a presupuesto USD 0,25, por lo que `position_size` devuelve 0. También hay pruebas de PnL usando distintos tick values y de spread histórico variable.

La selección sigue en `selected=None` / `NO TRADE`. Ningún supuesto o contrafactual se usa para superar filtros. El comando de evaluación se volvió a ejecutar después de los cambios y devolvió `selected=None; reason=no_candidate_passed_selection`; las velas locales no tienen columna spread, así que sus informes marcan `spread_model=configured_fixed_pips`.

## Tests

- Suite: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_xm_specs\basetemp` — **111 passed**.
- Pruebas dirigidas de volumen, tick values y spread: **6 passed, 46 deselected**.
- `compileall` y `git diff --check`: pasaron.
- No se realizó ni se solicitó una comparación contra una cuenta real con PnL de MT5 porque el terminal no conectó.
