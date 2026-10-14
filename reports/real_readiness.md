# Estado de preparación para cuenta REAL de XM MT5

Fecha de comprobación: 2026-10-14. No se envió ninguna orden y no se hizo login desde Python.

## Hallazgos de esta revisión

- La inicialización de solo lectura del terminal con la ruta ya guardada en `.env` falló con IPC `-10005`. Por eso no fue posible verificar modo de cuenta, divisa, permisos, balance/equity, margen libre, símbolos exactos, especificaciones, spread ni cotizaciones.
- `reports/inspect_mt5_readonly.py` inspecciona la sesión abierta sin credenciales, sin `login()`, sin imprimir login/servidor y sin `order_send()`. Cubre `EURUSDmicro`, `EURUSD`, `EURCADmicro` y `EURCAD`; el intento de hoy se guarda en `reports/mt5_account_inspection.json`.
- El gate de REAL ya está configurado en `.env`; no se modificó. Esto no constituye autorización para iniciar el bot. No se leyó ni expuso ningún valor de credencial.
- Se corrigió `connect_mt5`: ahora compara la sesión abierta con la configuración y falla cerrado ante discrepancia; no invoca `login()` ni cambia de cuenta. `check_mt5.py` y los logs ya no imprimen identificadores de cuenta/servidor.
- `MT5Broker` ya consulta pérdida y margen con `order_calc_profit`/`order_calc_margin`, valida volumen, dirección y distancia SL/TP, y llama `order_check` antes de `order_send`. `--live` exige además la frase escrita y aplica los límites live actuales (0,25% por operación, 1% pérdida diaria, 5% drawdown, máximo 0,5 lotes).
- La estrategia no muestra ventaja demostrada. Con el CSV de procedencia local no autenticada, en el segmento de test las reglas dieron −0,19% neto hipotético (46 trades, PF 0,88, expectativa −USD 0,41, DD −0,66%); la híbrida +0,03% (21 trades, PF 1,04, expectativa +USD 0,14, DD −0,45%). La validación fue negativa para ambas y los escenarios de costes mayores también fueron negativos. Estos resultados no justifican REAL.

## Bloqueos técnicos antes de operar

1. Resolver IPC ejecutando MT5 y Python con el mismo usuario y nivel de permisos; verificar manualmente en MT5 que se conserva la cuenta que el usuario eligió. El diagnóstico no cambiará de cuenta.
2. Volver a ejecutar el diagnóstico y confirmar modo REAL, USD, conexión/permisos, símbolos exactos, tick fresco, volumen mínimo/paso, stops/freeze level, spread y margen.
3. El sizing actual no pone un techo explícito al spread de mercado; el riesgo modela comisión configurada y el fill puede diferir entre sizing y envío. El broker comprueba que la pérdida calculada sea negativa, pero no contrasta la pérdida recalculada en el último tick con el presupuesto permitido. No se debe considerar protegido frente a deslizamiento adverso hasta cerrar esa brecha.
4. El bucle gestiona posiciones con el magic number del bot; aún falta una verificación periódica que detenga nuevas entradas y alerte si una posición propia pierde/cambia su SL/TP, y una reconciliación robusta tras fills parciales/desconexión. No se ha probado ejecución XM.
5. La comisión de XM, swaps, deslizamiento observado, conversiones de divisa y condiciones particulares de la cuenta siguen sin verificar. La cifra de riesgo del backtest no equivale al riesgo live de la cuenta.
6. Para un saldo de USD 7 y el perfil live (0,25%), el presupuesto es USD 0,0175 por operación. Los datos previos hipotéticos no encontraron oportunidades con volumen mínimo bajo ese límite; solo las especificaciones y `order_calc_profit` de la cuenta conectada pueden dar la respuesta real para EURUSDmicro/EURCADmicro. No se ampliarán los límites para forzar entradas.

## Validación reproducida

- `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`: **63 passed** después de cambiar la conexión a fail-closed.
- `.\.venv\Scripts\python.exe reports/run_validation.py`: **exit 0**; se regeneró `reports/validation_results.json`, split cronológico 3000/1000/1000, costes base/mayores/estrés. No se ajustó con el tramo de test.
- `.\.venv\Scripts\python.exe reports/inspect_mt5_readonly.py`: **exit 2**, IPC `-10005`, conexión imposible; ningún login intentado ni orden enviada.

## Comando preparado, no ejecutar hasta nueva autorización explícita

Tras confirmar que el terminal está conectado a la cuenta REAL correcta, verificar especificaciones/riesgo para el símbolo elegido y aceptar expresamente el resumen pretrade, el comando previsto con la configuración predeterminada sería:

```powershell
Set-Location C:\Users\USUARIO\Downloads\forex-ai
.\.venv\Scripts\python.exe app.py demo --broker mt5 --live --mode hybrid --symbol EURUSD --timeframe H1
```

El proceso todavía requerirá escribir `OPERAR EN REAL`. El símbolo del comando es el default actual del proyecto, **no está confirmado en la cuenta XM**, y el comando no debe ejecutarse hasta resolver los bloqueos anteriores. No se ha cambiado la cuenta, la contraseña, el gate ni los límites de riesgo.
