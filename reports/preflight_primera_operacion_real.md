# Preflight: primera operación real XM MT5

Fecha: 2026-10-14. No se conectó desde esta auditoría, no se ejecutó `order_send`, no se modificaron posiciones y no se tocó el kill-switch.

## Primer bloqueo encontrado

Se ejecutó:

```powershell
.\.venv\Scripts\python.exe reports\evaluate_agent_strategies.py
```

Resultado: `selected=None; reason=no_candidate_passed_selection`. El JSON generado usa el CSV local de 5.000 barras, cuya procedencia no está autenticada. En los tres segmentos de selección, todas las candidatas con operaciones mostraron expectativa negativa y win rate 0%; la referencia NO TRADE tiene cero operaciones. El resultado local no acredita por sí solo la misma evaluación que se obtendría con el histórico actual de MT5, que no se descargó en esta auditoría.

En el orden del flujo, éste es el primer bloqueo para generar una señal operable: sin candidata aprobada, `AutonomousAgent` deja señal 0 y acción `NO TRADE`. No se deben cambiar umbrales/filtros ni forzar una receta para superar el rechazo.

## Otros bloqueos independientes

1. **Costes:** `StrategyEvaluator.costs_verified` inicia en `False` y el selector exige fuente y costes verificados (`src/strategy_evaluator.py:52-54,163-180`). La ruta de `app.py` solo marca `source_verified=True`; no marca costes verificados. La comisión configurada de USD 7/lote no está verificada y swap no está modelado. El evaluador puede calcular diagnósticos, pero no seleccionar una estrategia operable hasta que los costes tengan evidencia documentada y exista una conexión de esa evidencia al gate. Los diez deals previos con cargos cero no establecen por sí solos la tarifa oficial actual.
2. **Riesgo de volumen mínimo:** bajo los límites live de 0,25%, una equity supuesta de USD 7 da presupuesto de USD 0,0175. Usando los datos de EURUSDmicro comunicados previamente (tick USD 0,01, tick size 0,00001), el valor derivado es USD 0,10/pip/lote; con mínimo 0,10 lote, el SL máximo matemático sería 1,75 pips **si no hubiera otros costes**. El sizing en `app.py:214-219` agrega la comisión configurada de USD 7 por lote: USD 0,70 para 0,10 lotes, ya sobre el presupuesto antes del SL. Con la configuración efectiva actual, `position_size_for_risk` debe devolver cero. No se cambió la comisión a cero. El SL/ATR y equity actuales no se consultaron; si costes verificados difieren, MT5 tendrá que volver a estimar pérdida con `order_calc_profit` y el mínimo debe seguir cabiendo.
3. **Estado conectado:** el usuario indica que `check_mt5.py` confirmó cuenta REAL USD, trading permitido y Algo Trading activo. No se repitió esa comprobación ni se leyeron sus datos sensibles durante esta ejecución. La confirmación no elimina los bloqueos anteriores ni demuestra el spread, stops, margen, cotización o especificaciones actuales de EURUSDmicro.

## Controles que permanecen activos

`autonomous` requiere `--live` junto con `--execute`, la variable `ALLOW_LIVE_TRADING=YES_I_ACCEPT_THE_RISK` y confirmación literal `OPERAR EN REAL`. En ejecución valida modo REAL/USD, permisos, símbolo/specs, lote mínimo/paso, SL/TP, quote/spread, exposición, pérdida calculada, margen y `order_check`. El riesgo live es 0,25% por operación, pérdida diaria 1%, drawdown 5%, máximo 0,5 lote, una posición y tres operaciones diarias. El kill-switch sigue activo.

## Tests ejecutados

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_live_audit\basetemp tests\test_live_gate.py tests\test_risk.py tests\test_autonomous.py
```

Resultado: **64 passed in 4.35s**. Son tests locales/fakes; no prueban conexión XM ni autorizan operaciones. No ejecuté la suite completa ni una orden.

## Corrección mínima pendiente

No hay una corrección de código segura que convierta el resultado actual en operable. Hace falta que una evaluación con histórico autenticado pase sin alterar criterios; verificar comisión y swap en condiciones oficiales de la cuenta/instrumento y deals; conectar esa evidencia al gate de costes; y obtener un SL/quote reales cuyo lote mínimo, con `order_calc_profit`, desviación, costes y margen, esté dentro del 0,25%. Hasta entonces `selected=None` / `NO TRADE` y lote cero son el comportamiento correcto.

Cuando todo lo anterior esté satisfecho, el comando sería:

```powershell
.\.venv\Scripts\python.exe app.py autonomous --symbol EURUSDmicro --timeframe M15 --cycle-minutes 15 --execute --live
```

No ejecutar ese comando ahora: la selección y el gate de costes fallan y el sizing configurado rechaza el volumen mínimo para una cuenta de aproximadamente USD 7.
