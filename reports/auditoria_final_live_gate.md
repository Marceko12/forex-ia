# Auditoría final del gate de ejecución real

Fecha: 2026-10-14. Solo revisión local/pruebas; no se conectó a MT5 ni se envió/modificó ninguna orden o posición.

## Gates de `autonomous`

- `app.py:360-380,449-455`: `autonomous` acepta M15/M30/H1/H4. `--live` exige también `--execute`; `--live` solo falla antes de conectar.
- `app.py:160-180`, `config.py:223`: requiere `ALLOW_LIVE_TRADING=YES_I_ACCEPT_THE_RISK` y escribir exactamente **`OPERAR EN REAL`** (sensible a mayúsculas). Es una variable de entorno más confirmación interactiva. Con `--live`, usa `live_risk`: 0,25% por trade, 1% pérdida diaria, 5% drawdown máximo, una posición, tres trades diarios, máximo 0,5 lotes (`config.py:205-216`).
- `app.py:373-380`, `src/broker_demo.py:175-240`: conecta, comprueba que la cuenta realmente sea REAL y que la divisa sea USD; comprueba terminal conectado, permiso de cuenta/terminal, símbolo exacto y obtiene especificaciones vivas del símbolo. No cambia la cuenta conectada.
- `src/autonomous_agent.py:191-238,266-278,310-335`: si `selected=None`, la señal es cero y acción NO TRADE. Antes de ejecución exige datos/cotización válidos, vela cerrada no repetida, posición/stops revisados, riesgo y margen calculables y límites de `RiskManager`.
- `app.py:187-233`, `src/broker_demo.py:314-431`: rechaza lote 0/fuera de min/max/paso; spread excesivo; quote vieja/inválida; modo de símbolo distinto de FULL; SL/TP inválido o demasiado cerca; falta de permisos/exposición no consultable; pérdida al SL no estimable o superior al presupuesto (incluye comisión configurada y deviation); margen no verificable/insuficiente; y `order_check` que no retcode 0. Solo entonces llega a `order_send`.
- `src/risk_manager.py:74-105`: redondea hacia abajo por pasos y retorna cero si el lote mínimo excede el presupuesto; kill-switch y límites permanecen activos.

## Bloqueos actuales comprobables

1. **Conexión:** `reports/mt5_account_inspection.json` registra `connected=false`, `initialize_failed`, `error_code=-10003`; no se verificaron cuenta, cotización, permisos ni especificaciones en la sesión guardada. La ruta de terminal existía en el diagnóstico previo, pero no había proceso de terminal en ejecución. No se atribuye a credenciales.
2. **Estrategia:** `reports/agent_strategy_evaluation.json` tiene `selected=null`, `action_if_current_signal=NO TRADE`, `reason=no_candidate_passed_selection`. No se debe pasar ninguna entrada con estrategia forzada.
3. **Costes:** el evaluador mantiene `costs_verified=False`; la tarifa de comisión no está acreditada y el swap no se modela. Por el gate de `src/strategy_evaluator.py:52-54,163-180`, una selección operable también requiere fuente y costes verificados. En `app.py:376` solo se establece `source_verified=True`; no hay ruta de producción que marque costes verificados. Esta condición mantiene selección cerrada hasta implementar evidencia verificable de tarifa/swap.
4. **Volumen mínimo con cuenta aproximada de USD 7:** usando *solo para estimación* los datos aportados (tick value USD 0,01, tick size 0,00001, pip 0,0001; mínimo 0,10 lotes), valor aproximado = USD 0,10/pip/lote y USD 0,01/pip al lote mínimo. Límite `--live` = USD 7 × 0,25% = **USD 0,0175**. Sin comisión ni costes adicionales, el SL tendría que estar como máximo a **1,75 pips** para que el lote mínimo cupiera. La configuración actual agrega USD 7/lote round-turn: USD 0,70 al mínimo, ya mayor que USD 0,0175 aun antes del SL; `try_open_trade` por tanto calcularía lote cero/rechazaría, y el preflight también lo bloquearía. La comisión sigue siendo desconocida, no se reemplazó por cero. Sin equity, ATR/SL, quote y specs consultados ahora, no se puede afirmar viabilidad real; el criterio es **rechazar hasta que el cálculo con `order_calc_profit` y costes válidos quepa**.
5. **Cuenta/permisos/cotización de ahora:** modo REAL, USD, trade_allowed, terminal Algo Trading, spread, stops_level, margen libre y símbolo no se verificaron por la desconexión.

## Tests relevantes ejecutados

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_live_audit\basetemp tests\test_live_gate.py tests\test_risk.py tests\test_autonomous.py
```

**64 passed in 10.75s.** Cubre confirmación/env, gates de broker, no envío ante rechazo, riesgo mínimo, kill-switch y ciclo/evaluador. Son pruebas locales con broker falso; no demuestran conexión ni ejecución en XM. No se ejecutó la suite completa. Ningún test envía una orden real.

## Comando previsto, solo tras despejar los bloqueos

En PowerShell, ejecutar únicamente cuando la conexión, símbolo exacto, costes (incluido swap), estrategia seleccionada y cálculo del lote mínimo hayan pasado sus gates:

```powershell
.\.venv\Scripts\python.exe app.py autonomous --symbol EURUSDmicro --timeframe M15 --cycle-minutes 15 --execute --live
```

El proceso pedirá escribir `OPERAR EN REAL` y requiere `ALLOW_LIVE_TRADING=YES_I_ACCEPT_THE_RISK` ya autorizado en entorno. **No es un comando seguro para ejecutar en el estado actual:** fallará por conexión antes de operar si persiste `-10003`; aunque conecte, `selected=null`/costes no verificados/lote mínimo fuera de presupuesto mantienen NO TRADE o rechazan la orden.
