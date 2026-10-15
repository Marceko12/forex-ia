# Auditoría de costes EURUSDmicro

Fecha: 2026-10-14. No se enviaron órdenes ni se modificaron posiciones. El kill-switch no se tocó.

## Datos MT5 comunicados y alcance

El usuario aporta estas especificaciones de EURUSDmicro: contrato 1.000, volumen mínimo 0,10, paso 0,01, tick size 0,00001, tick value USD 0,01 y moneda de beneficio USD. De ahí se deriva **USD 0,10 por pip y lote** (`0,01 × 0,0001 / 0,00001`). También comunica diez movimientos históricos con comisión/swap/fee iguales a cero y suma de `profit` de +USD 0,09.

Tomo esas cifras como datos aportados para este análisis, pero no pude verificarlas contra la API en este entorno: el archivo de inspección local continúa con `initialize_failed/-10003`, sin símbolos ni deals. Tampoco se suministraron las diez filas con `position_id`, `entry`, volumen, precio, comisión, swap, fee y profit. Por eso no es posible agrupar aquí esas operaciones ni comparar el PnL calculado posición por posición. Los diez movimientos con cargos cero prueban, como máximo, los valores realizados de esa muestra; **no establecen la tarifa oficial aplicable** ni justifican cambiar la configuración a cero.

## Dónde se aplican costes

- `config.py:191-193`: defaults de backtest: spread 1,0 pip; comisión USD 7 por lote round-turn (marcada ahora como supuesto no confirmado); slippage 0,2 pip por ejecución. No hay parámetro ni deducción de swap.
- `src/backtester.py:80-85,128,180-183`: selecciona spread por vela en puntos MT5 si hay `point` válido; si no, usa spread fijo. Define `half_cost = (spread/2 + slippage) × pip_size`, aplica ese ajuste al fill de entrada y al de salida, y calcula `costs = gross_pnl - pnl`. Para velas de precio medio esto equivale a **un spread completo** ida y vuelta y dos slippages por operación. No hay otra deducción de spread separada.
- `src/backtester.py:105,122,173,181`: descuenta `commission_per_lot × lots` una vez en el PnL de liquidación/cierre y una vez en la estimación de sizing. La primera es el cargo simulado de la operación completa; la segunda solo reserva presupuesto antes de abrir. El parámetro se denomina round-turn y no se divide por lado ni por movimiento. No aparece un segundo cargo de comisión en el neto registrado.
- `app.py:214-218,229-230,254` y `src/autonomous_agent.py:320,335`: reusan USD 7/lote para reservar riesgo/log de supuesto en ejecución. Esto tampoco confirma que sea el tarifario XM. `src/broker_demo.py` recibe una comisión configurable como simulación; no es fuente de una comisión MT5 observada.
- No se modela swap en el backtester. Los costes efectivamente reportados por MT5 deberían conciliarse como `profit + commission + swap + fee` por posición; no se debe volver a sustraer spread de `profit` de MT5, pues ese profit ya resulta de precios de deal ejecutados.

**Cautela sobre la base de precios:** el ajuste half-spread por lado presupone OHLC de precio medio. El inspector existente guarda rates OHLC y un spread, pero no etiqueta la base Bid/Ask/Last. Si los OHLC son Bid, sumar medio spread en ambos lados no reproduce de forma fiel fills Bid/Ask ni la activación de SL/TP; no afirmo doble deducción confirmada sin saber el modo del gráfico/feed. El backtest requiere aclarar esta semántica antes de considerar sus costes representativos. La prueba nueva declara explícitamente velas mid-price y verifica aritmética, no resuelve el origen del CSV.

## Operación sintética completa

Se añadió `test_complete_synthetic_round_trip_applies_one_full_spread_not_twice` en `tests/test_strategy.py`. Con especificaciones ilustrativas equivalentes a las aportadas (tick value $0,01, tick size 0,00001), 5 lotes, take-profit bruto de 2 pips, spread de 1 pip round-turn, sin slippage y comisión 0 solo para aislar la prueba: PnL bruto **+$1,00**, coste de spread **$0,50**, PnL neto **+$0,50**. Así se prueba que dos mitades de spread suman un spread completo, no dos spreads. El parámetro cero vive únicamente en la fixture y no cambia `config.py`.

## Agrupación de deals

Se amplió `reports/inspect_mt5_readonly.py` para que, cuando una futura lectura MT5 de solo lectura tenga éxito, agrupe deals cerrados por posición sin publicar IDs; reporte conteo, volúmenes, precios promedio ponderados, `profit`, comisión, swap, fee, neto MT5 y delta frente a un PnL de precio reconstruido con el contract size. El cálculo de precio es diagnóstico y puede no conciliar operaciones con cierres parciales, reversals, conversiones o comisiones fuera de los deals; se conservan volúmenes de entrada/salida para detectarlo. Esta sesión no produjo grupos reales ni comparación porque no hay filas disponibles y el terminal local no conectó.

## Comisión desconocida: solo diagnóstico

`StrategyEvaluator` exige ahora fuente **y** costes verificados antes de devolver una estrategia seleccionada. Por defecto `costs_verified=False`; una receta que pase sus métricas, pero no tenga costes verificados, devuelve `selected=None` con razón `unverified_transaction_costs`. `reports/evaluate_agent_strategies.py` registra comisión como `UNVERIFIED`, swap como `NOT_MODELLED_UNVERIFIED` y modo `diagnostic_only`. El escenario sigue usando el USD 7 configurado para que las métricas puedan inspeccionarse; no lo reemplaza por cero y no habilita selección operable. En la ejecución local, `selected=null`, `NO TRADE`; motivo concreto `no_candidate_passed_selection`.

## Pruebas ejecutadas

Comando de pruebas dirigidas:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_cost_audit\basetemp tests\test_strategy.py tests\test_autonomous.py -k "complete_synthetic_round_trip or deterministic_buy or deterministic_sell or backtest_uses_mt5_spread or strategy_evaluator_selects_from_bounded_recipes_without_test_tuning"
```

Resultado: **5 passed, 41 deselected**. Una primera invocación de pruebas usó dos opciones `-k` y terminó con todos los tests deseleccionados; se corrigió el filtro y la ejecución válida es la indicada arriba.

Evaluación diagnóstica:

```powershell
.\.venv\Scripts\python.exe reports\evaluate_agent_strategies.py
```

Resultado: `selected=None; reason=no_candidate_passed_selection`; JSON incluye costes no verificados y `evaluation_mode=diagnostic_only`. No se ejecutó la suite completa.

## Conclusión

Defecto de seguridad/semántica corregido: antes el evaluador podía seleccionar si fuente/especificaciones pasaban aunque la comisión y swap no estuvieran verificados. Ahora es diagnóstico hasta verificar costes. La comisión de USD 7 round-turn sigue siendo supuesto y afecta sizing/backtest; los 10 movimientos con comisión cero no la autentican ni permiten reemplazarla por cero. El spread se descuenta una vez como round-trip en el modelo mid-price; la base real de precios de las velas queda pendiente. Swap queda omitido. La agrupación/comparación por posición espera las filas MT5 individuales y una conexión válida. **`selected=None` / `NO TRADE` se mantiene.**
