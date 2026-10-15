# Diagnóstico local de conexión MT5 y pruebas de riesgo

Fecha: 2026-10-14. Inspección de solo lectura. No se intentó `login()`, no se enviaron órdenes, no se modificaron posiciones y no se alteró el kill-switch ni la selección de estrategia.

## Conexión MT5

El último resultado guardado por `reports/inspect_mt5_readonly.py` es `connected=false`, `reason=initialize_failed`, `error_code=-10003`, `login_attempted=false` y `orders_sent=0` (`reports/mt5_account_inspection.json`). No se imprimieron ni usaron credenciales. En el diagnóstico local, el paquete `MetaTrader5` versión `5.0.6231` estaba instalado y la ruta configurada apuntaba a un archivo existente llamado `terminal64.exe`; no se encontró proceso local `terminal64`/`terminal` en ejecución.

Esto confirma un fallo en la inicialización/IPC del terminal, previo a cualquier autenticación de cuenta. La ausencia de proceso en ejecución es consistente con el fallo, pero no prueba la causa raíz única: podrían existir otros problemas del terminal, permisos o IPC. No se puede informar conexión, cuenta, divisa, permisos, especificaciones ni comisión desde MT5 hasta que una consulta válida responda. El error exacto registrado es `-10003`; no se atribuye a credenciales.

## Revisión local del dimensionamiento

Se revisaron `config.py`, `src/risk_manager.py`, `src/backtester.py` y `tests/test_risk.py`/`tests/test_strategy.py`.

- `RiskManager.position_size()` calcula el presupuesto como `equity * risk_per_trade`, estima pérdida por lote con el SL y suma el coste por lote que le entregue el llamador. `position_size_for_risk()` rechaza entradas no válidas, retorna cero si el volumen bruto no alcanza el mínimo y redondea hacia abajo desde `min_lot` por múltiplos de `lot_step`, respetando `max_lot` y `max_lots`.
- Las pruebas nuevas usan una fixture **sintética/ilustrativa**, no valores obtenidos de XM: tick size 0,00001, tick value 0,10 USD y lote mínimo/paso 0,1. A presupuesto de USD 10, SL de 5/10/20 pips da 2,0/1,0/0,5 lotes y pérdida teórica no mayor que el presupuesto. Con presupuesto USD 0,25, SL 50 pips y volumen mínimo 0,1, se obtiene volumen 0 porque la pérdida teórica mínima sería USD 5.
- Estos cálculos comprueban el algoritmo local; no sustituyen `order_calc_profit`, `order_calc_margin`, `order_check` ni `symbol_info` de la cuenta MT5.
- En `config.py`, `commission_per_lot=7.0` es un supuesto configurado, no tarifa XM verificada. El backtester local usa spread fijo configurado cuando no existe spread histórico válido y no modela swap; esos resultados no son prueba de costes reales.
- El evaluador guardado mantiene `selected=null` y acción `NO TRADE`. El CSV se declara sin fuente autenticada, y los supuestos de costes/especificaciones se identifican como no confirmados.

## Pruebas locales ejecutadas en esta continuación

Comando exacto:

```powershell
$env:TEMP=(Resolve-Path reports\pytest_latest\basetemp).Path; $env:TMP=$env:TEMP; .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_latest\basetemp tests\test_risk.py tests\test_strategy.py -k "position_size or mt5_tick_spec or tick_spec_minimum or asymmetric_tick_values or variable_historical_spread"
```

Resultado: **10 passed, 32 deselected in 13.81s**. Las pruebas pasaron localmente; no requieren terminal MT5 y tampoco verifican datos de XM. No se ejecutó en esta continuación la suite completa.

## Datos aún NO VERIFICADOS

Las especificaciones reales de `EURUSDmicro` (contrato, volumen mínimo/paso, tick size/values, point/digits, spread, monedas y modo de trading), símbolos realmente disponibles, moneda/estado/permisos de cuenta, comisión XM efectiva, swaps, slippage y conciliación del PnL del backtester contra deals cerrados son **NO VERIFICADOS**. El intento fallido no devolvió `symbol_info`, cotizaciones ni historial. No deben presentarse las fixtures ni los resultados del CSV local como evidencia fiable de ejecución o rentabilidad.

Para continuar, abrir manualmente el terminal MT5 deseado y comprobar que esté conectado; después repetir el inspector de solo lectura. No hace falta facilitar ni guardar credenciales en este informe.
