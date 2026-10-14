# Auditoría técnica — Forex AI / XM MT5

Fecha: 2026-10-14  
Alcance ejecutado: inspección local, cambios de cálculo y preflight, pruebas automatizadas y backtests en el CSV existente. No se inicializó MetaTrader 5 ni se enviaron órdenes.

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
