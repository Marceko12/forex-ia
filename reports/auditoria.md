# Auditoría técnica — Forex AI / XM MT5

Fecha: 2026-10-14  
Alcance ejecutado: inspección local, cambios de cálculo y preflight, pruebas automatizadas y backtests en el CSV existente. No se inicializó MetaTrader 5 ni se enviaron órdenes.

## Fase 2 — Correcciones y validación ampliada (2026-10-14)

Esta sección reemplaza las conclusiones anteriores donde haya diferencias. El archivo histórico es muy probablemente **EURUSDmicro**, no EURUSD: `logs/app.log` registra una descarga de 5.000 velas EURUSDmicro y el guardado del CSV a las 10:46:48 del 13 de octubre; la fecha de modificación del CSV corresponde a las 15:46:48 UTC (Lima UTC-5). Una descarga anterior de EURUSD fue sobrescrita. El log no prueba por sí mismo qué cuenta/servidor generó las velas ni contiene un snapshot de `symbol_info`.

El CSV tiene 5.000 filas entre 2026-07-31 16:45 UTC y 2026-10-13 18:30 UTC, timestamps UTC estrictamente ordenados, 0 duplicados, 4.988 intervalos de 15 minutos y 11 pausas semanales de viernes a lunes de 48 h 15 min. OHLCV no tiene valores ausentes/no numéricos, volumen no positivo, precios <= 0 ni inconsistencias OHLC. La apertura dominical de XM no fue confirmada, así que no se afirma que toda pausa de fin de semana sea completa. El hash y el inventario están en [data_validation.json](data_validation.json).

### Correcciones de validación

- Reparamos `.venv\pyvenv.cfg` en el mismo lugar, sin borrar el entorno ni reinstalar paquetes. Se guardó el original en [pyvenv.cfg.before-repair](pyvenv.cfg.before-repair). `.venv\Scripts\python.exe` ahora usa Python 3.12.14 y carga pytest, pandas, numpy, scikit-learn y MetaTrader5.
- `load_csv` y `AIModel.train` ahora rechazan velas inválidas, timestamps desordenados/duplicados y entrenamiento fuera de orden. El backtester limita tamaño usando equity liquidable, spread, comisión y slippage asumidos; contabiliza costes al marcar equity, estima MAE intravela conservador y calcula drawdown con el pico de cierre anterior.
- Se añadió `BacktestConfig.slippage_pips`; la configuración por defecto es una hipótesis de 0,2 pips por ejecución, no un dato de XM.
- Se añadió el generador reproducible [run_validation.py](run_validation.py), que divide cronológicamente 60/20/20, conserva el test sin ajuste de parámetros, compara reglas/híbrida/sin operación y barre tres escenarios fijos de costes. Su salida canónica es [validation_results.json](validation_results.json).

### Entorno y pruebas

Reparación verificada: Python 3.12.14; pytest 9.1.1, pandas 3.0.6, numpy 2.5.4, scikit-learn 1.9.1 y MetaTrader5 5.0.6231 importan desde el entorno reparado. Pruebas finales: **63 passed, 0 failed**; incluyen mocks MT5 y no envían órdenes reales.

Se intentó inicializar el terminal de forma aislada y sin pasar credenciales (sin usar `connect_mt5` ni `login`). MT5 devolvió error IPC **-10005**; la conexión demo y las especificaciones reales no pudieron comprobarse. No se llamó a `order_send`.

### Procedencia de resultados y backtests vigentes

Las cifras previas en `backtest_results.json` **no se reproducen** con el backtester corregido y no deben usarse: el CSV era EURUSDmicro, pero se etiquetó y simuló como EURUSD con el contrato genérico de 100.000 unidades; además faltaban slippage, sizing con costes y drawdown intravela. La prueba de reproducción antigua se conserva en `validation_results.json` como diagnóstico y marca las diferencias.

El resultado nuevo es solo una simulación condicional: usa contract size 1.000, lote mínimo 0,1 y step 0,01 como hipótesis pública de una cuenta XM Micro; no están confirmados en esta cuenta/símbolo. La página de ayuda de XM presenta esos valores para cuentas Micro ([XM — especificaciones generales de Micro](https://www.xm.com/help-center/trading-accounts/are-all-instruments-swap-free-for-ultra-low-account-holders)). Los costes también son escenarios hipotéticos, no spreads/fees registrados de MT5; los swaps están excluidos porque no se obtuvo la tarifa de esta cuenta.

En el test de 1.000 velas, USD 10.000 y costes base hipotéticos (1,5 pips de spread, 0 comisión por lote Micro y 0,2 pips de slippage por lado): reglas **-0,19%**, expectativa **-USD 0,41**, PF **0,88**, DD máximo **-0,66%**, 46 operaciones; híbrida **+0,03%**, expectativa **+USD 0,14**, PF **1,04**, DD **-0,45%**, 21 operaciones. Referencia sin operaciones: 0%, 0 operaciones. Al elevar costes (2 pips, USD 0,07 por lote Micro y 0,5 pips de slippage por lado), reglas **-0,60%**, PF 0,67; híbrida **-0,16%**, PF 0,81. En estrés (3 pips, USD 0,14 por lote Micro y 1 pip de slippage por lado), reglas **-1,22%**, PF 0,44; híbrida **-0,44%**, PF 0,57. No hay evidencia de ventaja fuera de muestra.

La AUC del modelo fue 0,496 en la validación interna, 0,534 en validación externa y 0,533 en test. La estrategia híbrida quedó prácticamente plana antes de cualquier swap y su expectativa fue casi cero; no se considera una ventaja demostrada.

### Balance USD 7 y barreras pendientes

Con hipótesis pública de volumen mínimo Micro 0,1 y contrato 1.000, el riesgo estimado del lote mínimo al SL para EURUSDmicro en el periodo test fue USD **0,071 / 0,100 / 0,167** en percentiles 5/50/95 bajo el escenario base, frente a un presupuesto default de USD 0,07; solo el 4,4% de velas cabría en ese presupuesto, y el backtest produjo 3 operaciones, con retorno -0,83% y DD -2,28%. Bajo la configuración live (0,25%, USD 0,0175) no hubo velas elegibles ni operaciones. Con escenarios de costes superiores no hubo operaciones incluso en el riesgo default. Son cálculos hipotéticos, no el resultado de `order_calc_profit` de XM.

EURCADmicro no se puede cuantificar: no hay histórico guardado para ese símbolo, el terminal falló al inicializarse y faltan conversión CAD/USD, contrato, mínimo/paso, spread y tick values reales. Con el código actual, sin datos MT5, el cálculo de cruces falla de forma segura y bloquea operaciones.

**Conclusión de fase 2:** el entorno quedó reparado y la validación estadística fue reproducible, pero las cifras no son evidencia fiable de rentabilidad ni de ejecución en XM: faltan la confirmación demo real, los specs del símbolo y costes/swaps de la cuenta. El modo real debe permanecer bloqueado.

## Hallazgos principales

| Gravedad | Hallazgo | Estado |
|---|---|---|
| Crítica | El fallback de valor de pip suponía USD para cruces como EURCAD, sin convertir CAD a USD. | Corregido: pares sin conversión verificable ahora fallan cerrados; la ruta MT5 obtiene la pérdida desde `order_calc_profit`. |
| Crítica | La orden MT5 no comprobaba tick utilizable, límites de volumen, dirección/distancia de SL/TP, pérdida, margen ni `order_check`. | Corregido parcialmente: se valida volumen, cotización, stops, pérdida monetaria, margen libre y preflight `order_check` antes de `order_send`. La ejecución queda sin comprobar hasta validación demo controlada. |
| Alta | El resolver de símbolos esperado por pruebas no existía, aunque XM usa sufijos y símbolos ambiguos no deben adivinarse. | Corregido: coincidencia exacta o único prefijo; cero o varias coincidencias bloquean. |
| Alta | No existía validación de pérdida calculada directamente por el motor de MT5 para dimensionar la posición. | Corregido para MT5; se redondea el volumen hacia abajo y se rechaza si el lote mínimo excede el presupuesto. |
| Alta | El modo de backtest no entrega evidencia suficiente para afirmar ventaja del modelo; la muestra OOS es corta. | Sigue pendiente una evaluación independiente con procedencia de datos, spreads reales, costes/swap y walk-forward. |
| Alta | El entorno `.venv` está obsoleto: su ejecutable base apunta a un Python 3.12 eliminado. | Pruebas ejecutadas con el runtime Python empaquetado y los paquetes que conserva `.venv`. |

## Cambios aplicados

- `config.py`: el valor de pip ya no inventa conversiones para cruces no denominados en USD.
- `src/risk_manager.py`: nuevo dimensionamiento desde pérdida monetaria por lote devuelta por el broker.
- `src/market_data.py`: resolución segura de símbolo exacto o sufijo único.
- `src/broker_demo.py`: preflight de volumen, precio, SL/TP, pérdida al SL, margen libre y comprobación del broker.
- `app.py`: con MT5 usa cotización ejecutable y la pérdida monetaria estimada por MT5; para cálculos sin conversión falla de forma segura.
- `tests/test_config.py`, `tests/test_risk.py`: casos de conversión desconocida y redondeo de tamaño desde riesgo monetario.

## Pruebas

`pytest -q -p no:cacheprovider`: **60 passed**, 0 fallidas, incluyendo las comprobaciones recientes de precios, volúmenes y preflight MT5 con mocks. No se ejecutó ninguna orden real.

## Backtests existentes

Datos: `data/market_data.csv`, 5.000 velas M15; el tramo reservado como test contiene 1.500 velas (2026-09-22 a 2026-10-13). La procedencia de este CSV no está documentada, por lo que sus resultados son exploratorios y no se atribuyen a XM ni a EURCADmicro.

| Variante | Balance inicial | Equity final | Retorno | Drawdown máx. | Operaciones | Win rate | Profit factor | Expectativa | Sharpe diario |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Reglas, EURUSD M15 | USD 10.000 | 10.717,10 | +7,17% | -7,95% | 68 | 44,12% | 1,15 | USD 10,55 | 2,28 |
| Híbrida reglas + IA, EURUSD M15 | USD 10.000 | 10.902,22 | +9,02% | -3,72% | 20 | 55,00% | 1,77 | USD 45,11 | 4,77 |
| Sin operaciones, EURUSD M15 | USD 10.000 | 10.000,00 | 0,00% | 0,00% | 0 | — | — | — | — |
| Reglas, EURUSD M15 | **USD 7** | 7,00 | 0,00% | 0,00% | **0** | — | — | — | — |

El clasificador híbrido tuvo AUC de validación **0,509** (accuracy 0,503), prácticamente azar. El resultado híbrido de 20 operaciones no demuestra ventaja de la IA y no se debe usar para justificar trading real. Los dos primeros backtests usaron el balance de USD 10.000 configurado en el proyecto, no el capital del usuario.

## Limitaciones y bloqueos para operar

- No se conectó a la terminal ni se leyeron las especificaciones actuales de XM; por tanto se desconocen símbolo publicado, spread, volumen mínimo/paso, stops level, margen, tick values, comisión y cotización de EURCADmicro/EURUSDmicro.
- El archivo de datos no trae procedencia ni condiciones de ejecución verificables. Son solo 1.500 velas OOS y 20 operaciones híbridas.
- El backtester actual aplica spread fijo y comisión fija, no modela swap ni slippage variable, no prueba sensibilidad de costes ni hace walk-forward; sus métricas no son una validación económica realista.
- Con USD 7 y los límites configurados, la simulación EURUSD abrió cero operaciones porque el lote mínimo excede el riesgo permitido. No se debe elevar el riesgo para forzar operaciones.
- El preflight usa `order_calc_profit`, `order_calc_margin` y `order_check` en código; el sizing suma la comisión round-turn configurada (USD 7/lote), que todavía no se ha verificado contra XM. Falta validar la ruta con la cuenta demo de XM, solo después de verificar cuenta/símbolo/mercado. No se probó ejecución de órdenes, rechazos reales, fills parciales ni reconciliación con el historial.
- No se habilitó el modo real. El proyecto no está técnicamente listo para operación real.

## Pendientes antes de evaluar demo o real

1. Restablecer el entorno Python reproducible.
2. Confirmar con lectura de MT5 que terminal y cuenta sean los esperados, cuenta USD, mercado disponible y símbolos exactos; capturar especificaciones y costes sin enviar órdenes.
3. Completar pruebas mock de margen, rechazo `order_check`, ejecución parcial, pérdida de conexión, duplicados y reconciliación.
4. Mejorar backtest con spread/slippage/swap, gaps y restricciones del símbolo; añadir benchmark simple, sensibilidad de costes, métricas por periodo y walk-forward.
5. Repetir evaluación con datos históricos identificados e independientes para EURCADmicro y EURUSDmicro, entrenamiento/validación/test cronológicos sin reutilizar el test para elegir parámetros.
6. Resolver el impedimento de lote mínimo frente a los USD 7 sin aumentar silenciosamente los límites de riesgo.

**Conclusión:** las pruebas de software pasan, pero no hay prueba de que la estrategia tenga ventaja ni de que el broker acepte una orden válida bajo sus condiciones actuales. No operar en real.
