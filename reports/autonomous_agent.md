# Ciclo autónomo de análisis y decisión

## Comportamiento

- app.py autonomous consulta velas cerradas del timeframe M15 por defecto y repite cada 15 minutos. --cycle-minutes 10 ofrece el intervalo alternativo; ciclos sin vela nueva quedan deduplicados por símbolo/timeframe y timestamp.
- Cada ciclo valida índice temporal, OHLCV, orden, duplicados, consistencia de velas, antigüedad, conexión/cotización, permisos y spread máximo configurado (2 pips por defecto; límite operativo configurable en MarketConfig).
- El evaluador entrena un modelo solo con el primer 60% cronológico, compara reglas/AI/híbrida en el siguiente 20% por tres subperiodos y reserva el último 20% como diagnóstico. Exige al menos cinco operaciones, expectativa y retorno netos positivos, PF >= 1,05 y DD <= 5% en cada subperiodo. Si ninguna pasa, la decisión es NO TRADE.
- El bot revalida posiciones del magic propio, bloquea nuevas entradas si falta/cambió el SL/TP o el precio salió de sus niveles, y cierra por tiempo máximo solo cuando --execute está habilitado. Sin ejecución, las recomendaciones quedan en modo análisis.
- El estado conserva última vela, pico de equity, pérdida diaria, contador de operaciones y kill-switch. Decisiones/señales/métricas se anexan a logs/autonomous_decisions.jsonl; cierres MT5 se sincronizan con logs/trades.csv netos de profit, comisión y swap informados por MT5.
- El broker comprueba frescura de cotización, modo del símbolo, permisos, volumen/paso, stops, spread, pérdida a SL con desviación permitida y comisión configurada, margen libre y order_check. Un fill parcial no se reintenta automáticamente.

## Ejecución segura

Solo análisis (predeterminado):

~~~powershell
.\.venv\Scripts\python.exe app.py autonomous --symbol EURUSDmicro --cycle-minutes 15
~~~

Alternativa de 10 minutos:

~~~powershell
.\.venv\Scripts\python.exe app.py autonomous --symbol EURUSDmicro --cycle-minutes 10
~~~

No usar --execute mientras no se haya validado la cuenta/símbolo/costes en MT5 demo. La cuenta REAL continúa bajo el gate --live, variable de entorno y confirmación escrita existentes. Ningún test o backtest envía órdenes reales.

## Evidencia reproducible

reports/evaluate_agent_strategies.py generó agent_strategy_evaluation.json desde los 5.000 registros locales; el CSV no tiene procedencia autenticada y el contrato/costes EURUSDmicro siguen hipotéticos. El selector obtuvo selected=null, no_candidate_passed_all_validation_periods; por tanto el ciclo actual seleccionaría NO TRADE. En los tres subperiodos de validación, los retornos fueron: reglas −2,99/−6,24/−5,39%, IA −5,20/−6,16/−6,57% e híbrida −1,86/−4,30/−2,16%; PF 0 en todos esos bloques. No hay evidencia de ventaja.

## Límites y pendientes

- El diagnóstico MT5 previo falló con IPC -10005; no se consultaron especificaciones actuales ni se probaron conexiones ni órdenes XM.
- La comisión de BacktestConfig y el límite fijo inicial de spread son parámetros de configuración, no tarifas observadas. Swaps no aparecen en el backtest.
- No hay feed validado ni historial autenticado para EURCAD/EURCADmicro. Los tests de cuatro nombres de símbolo prueban la aritmética del redondeo con fixtures, no los specs reales del broker.
- Esta implementación aún no debe considerarse lista para cuenta real; se requiere forward-test en demo y validación de ejecución, reconexión, fills parciales y estados de posición reales.


