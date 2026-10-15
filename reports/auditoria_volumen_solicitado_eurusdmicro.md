# Volumen explícito EURUSDmicro: implementación y validación

Fecha: 2026-10-14. No se enviaron órdenes reales ni se modificaron posiciones.

## Cambio implementado

`Settings.requested_volume_by_symbol` solicita `EURUSDmicro: 0.10`. El flujo de `app.try_open_trade` usa ese volumen exacto (no vuelve a dimensionarlo ni lo eleva), valida min/max/paso y `max_lots`, calcula pérdida monetaria con `loss_per_lot` de MT5 cuando está disponible más la comisión configurada y lo rechaza si supera el presupuesto. Para MT5, también consulta margen requerido y margen libre antes de llamar al broker. El broker conserva sus comprobaciones finales de volumen, SL/TP, spread, exposición, pérdida con desviación, margen y `order_check`.

El preflight de `AutonomousAgent` usa el mismo volumen explícito y lo informa en `risk_estimate`; un exceso de pérdida o margen produce `NO TRADE` con pérdida estimada USD y motivo. `selected=None` sigue siendo `NO TRADE`.

## Consulta MT5 y viabilidad

Se intentó `reports/inspect_mt5_readonly.py`, que solo inicializa MT5 y consulta datos, sin login ni trading. La llamada no devolvió respuesta y se interrumpió. `reports/mt5_account_inspection.json` conserva el último resultado completado anterior: `initialize_failed/-10003`; esta auditoría no confirmó en vivo `volume_min`, `volume_step` ni `volume_max`.

Según las especificaciones previamente comunicadas (min 0,10, step 0,01), 0,10 es aritméticamente válido; la validación implementada lo vuelve a comprobar contra `symbol_info` en cada conexión y bloquea si no coincide. En este entorno no puedo afirmar que sea válido con las especificaciones actuales de XM.

Para una equity de USD 7, el riesgo live de 0,25% permite USD 0,0175. Con tick value USD 0,01, tick size 0,00001 y pip 0,0001, 0,10 lotes supone aproximadamente USD 0,01/pip; sin costes, un SL máximo teórico de 1,75 pips cabría. La configuración vigente agrega USD 7/lote de comisión asumida, es decir USD 0,70 a 0,10 lotes antes del SL. Esa hipótesis no está verificada, pero el código conserva el coste y por tanto rechaza este caso; no la cambia a cero. No hay SL, quote ni margen actuales para calcular un riesgo real definitivo. **Viabilidad actual: no viable con los parámetros/configuración actualmente aplicados; specs y costes de XM no revalidados en esta sesión.**

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=reports\pytest_requested_volume\basetemp tests\test_requested_volume.py tests\test_live_gate.py tests\test_risk.py tests\test_autonomous.py
```

Resultado: **69 passed in 3.75s**. Las pruebas nuevas comprueban que se solicita exactamente 0,10, que el riesgo excesivo bloquea con pérdida y presupuesto en USD, que el margen insuficiente bloquea con pérdida y margen requerido/libre, y que el preflight autónomo informa 0,10 y rechaza cuando excede riesgo. Son pruebas locales con broker falso; no demuestran la viabilidad de la cuenta XM.

También pasaron `compileall` de los archivos modificados y `git diff --check` (Git emitió únicamente avisos de normalización LF/CRLF).
