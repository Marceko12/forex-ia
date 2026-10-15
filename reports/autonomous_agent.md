# Ciclo autónomo de análisis y decisión

## Comportamiento

- app.py autonomous consulta velas cerradas del timeframe M15 por defecto y repite cada 15 minutos. --cycle-minutes 10 ofrece el intervalo alternativo; ciclos sin vela nueva quedan deduplicados por símbolo/timeframe y timestamp.
- Cada ciclo valida índice temporal, OHLCV, orden, duplicados, consistencia de velas, antigüedad, conexión/cotización, permisos y spread máximo configurado (2 pips por defecto; límite operativo configurable en MarketConfig).
- El evaluador usa 50/20/15/15% cronológico para entrenamiento/selección/validación/test. La selección compara siete recetas predefinidas en tres bloques; una receta pasa a validación independiente y al test final solo si supera selección. El test no selecciona. Además, la fuente debe estar verificada para habilitar una receta; de otro modo devuelve NO TRADE. Exige al menos cinco operaciones, expectativa y retorno netos positivos, PF >= 1,05 y DD <= 5% en cada bloque. La receta aprobada también determina el SL/TP usados por la ejecución.
- El CLI autónomo admite únicamente M15, M30, H1 y H4; el ciclo de consulta continúa configurable a 10 o 15 minutos, independientemente del timeframe.
- El broker expone todas las posiciones del símbolo, incluidas manuales; cualquier posición u orden pendiente bloquea una entrada. Solo cierra posiciones con el magic propio; nunca administra posiciones manuales. Si no se puede consultar la exposición, falla cerrado.
- Hay tres comandos explícitos: `analyze` (MT5 solo lectura), `paper` (replay CSV), y `demo` (`--broker mt5` exige cuenta DEMO confirmada). `autonomous --execute` se limita también a DEMO; la compuerta live existente se conserva aparte.
- `download-range` consulta el símbolo exacto con `copy_rates_range`, UTC y fragmentación; guarda columnas originales, alias `volume` para evaluación y un manifest. El manifest informa huecos y chunks vacíos, pero `complete=false`: MT5 no certifica cobertura histórica absoluta.
- El estado conserva última vela, pico de equity, pérdida diaria, contador de operaciones y kill-switch. Decisiones/señales/métricas se anexan a logs/autonomous_decisions.jsonl; cierres MT5 se sincronizan con logs/trades.csv netos de profit, comisión y swap informados por MT5.
- El broker comprueba frescura de cotización, modo del símbolo, permisos, volumen/paso, stops, spread, pérdida a SL con desviación permitida y comisión configurada, margen libre y order_check. Un fill parcial no se reintenta automáticamente.

## Ejecución segura

Solo análisis de mercado en tiempo real (conecta MT5 en modo de solo lectura):

~~~powershell
.\.venv\Scripts\python.exe app.py analyze --symbol EURUSDmicro --timeframe M15 --cycle-minutes 15
~~~

Alternativa de 10 minutos:

~~~powershell
.\.venv\Scripts\python.exe app.py analyze --symbol EURUSDmicro --timeframe M15 --cycle-minutes 10
~~~

Descarga histórica autenticada por MT5 (ejemplo; usa fechas con zona UTC y símbolo exacto disponible):

~~~powershell
.\.venv\Scripts\python.exe app.py download-range --symbol EURUSDmicro --timeframe M15 --from 2025-01-01T00:00:00Z --to 2026-01-01T00:00:00Z --output data/history/EURUSDmicro_M15_2025.csv
~~~

Replay paper sin conexión MT5: `python app.py paper --timeframe M15 --replay-bars 500`. Demo controlada: `python app.py demo --broker mt5`; al ser `MT5DemoBroker`, se detiene si MT5 no confirma trade_mode DEMO. Ningún comando de validación de esta tarea conecta MT5 o envía órdenes.

## Evidencia reproducible

`reports/evaluate_agent_strategies.py` usa los 5.000 registros locales, fuente no autenticada; el informe debe quedar `selected=null` por el gate de fuente incluso si una receta pasara filtros. Los costes/specs EURUSDmicro siguen hipotéticos. No hay evidencia de ventaja. El JSON contiene las fechas/filas de train/selection/validation/test y diagnósticos candidatos reproducibles.

## Límites y pendientes

- El diagnóstico MT5 previo falló con IPC -10005; no se consultaron especificaciones actuales ni se probaron conexiones ni órdenes XM.
- La comisión de BacktestConfig y el límite fijo inicial de spread son parámetros de configuración, no tarifas observadas. Swaps no aparecen en el backtest.
- No hay feed validado ni historial autenticado para EURCAD/EURCADmicro. Los tests de cuatro nombres de símbolo prueban la aritmética del redondeo con fixtures, no los specs reales del broker.
- La descarga por rango no se ejecutó durante esta tarea porque requiere conectar con el terminal MT5; su comportamiento se valida con mocks. Hay que revisar chunks vacíos, huecos, `TERMINAL_MAXBARS` y extremos solicitados antes de considerar el CSV suficiente.
- Esta implementación aún no debe considerarse lista para cuenta real; se requiere forward-test en demo y validación de ejecución, reconexión, fills parciales y estados de posición reales.


