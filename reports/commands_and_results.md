# Comandos y resultados de la fase de validación

Ejecutados desde `C:\Users\USUARIO\Downloads\forex-ai` en PowerShell.

## Entorno y datos

| Comando | Resultado |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q` (antes de reparar) | **Falló**: el launcher buscó `C:\Users\USUARIO\AppData\Local\Programs\Python\Python312\python.exe`, que ya no existe. |
| `& 'C:\Users\USUARIO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -q` | **Falló**: el Python empaquetado no trae pytest. Los paquetes del proyecto estaban conservados en `.venv\Lib\site-packages`. |
| `@' ... repair existing .venv\pyvenv.cfg ... '@ | & 'C:\Users\USUARIO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -` | **Pasó**: respaldó `reports\pyvenv.cfg.before-repair` y actualizó el home a Python 3.12.14 sin borrar/reinstalar paquetes. Se dejó una versión repetible en `reports\repair_venv.py`. |
| `.\.venv\Scripts\python.exe --version` | **Pasó**: `Python 3.12.14`. |
| `.\.venv\Scripts\python.exe -c "import sys,pytest,pandas,numpy,sklearn,MetaTrader5; print(sys.executable,pytest.__version__,pandas.__version__,numpy.__version__,sklearn.__version__,MetaTrader5.__version__)"` | **Pasó**: imports disponibles desde `.venv`. Versiones: pytest 9.1.1, pandas 3.0.6, numpy 2.5.4, scikit-learn 1.9.1, MetaTrader5 5.0.6231. |
| `.\.venv\Scripts\python.exe reports\repair_venv.py` | **Pasó**: idempotente, informa que el entorno ya está reparado. |
| `.\.venv\Scripts\python.exe reports\validate_data.py` | **Pasó**: 5.000 filas, UTC, cero duplicados, 4.988 deltas de 15 min y 11 pausas semanales viernes-lunes; OHLCV validado. Actualiza `reports\data_validation.json`. |

## Pruebas, backtest e integración

| Comando | Resultado |
|---|---|
| `.\.venv\Scripts\python.exe reports\run_validation.py` | **Pasó**: generó `reports\validation_results.json`; cortes cronológicos 3.000/1.000/1.000, reglas/híbrida y tres escenarios de costes. No se usó el test para ajustar parámetros. |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | **Pasó**: 63 passed, 0 failed. Incluye tests MT5 con mocks; no envían órdenes reales. |
| `.\.venv\Scripts\python.exe reports\validate_mt5_readonly.py` | **No pudo completar integración**; termina con código 2 y MT5 IPC `-10005` en `initialize`. No hizo login con las credenciales configuradas, no obtuvo specs de símbolos y no envió órdenes. Guarda `reports\mt5_readonly_validation.json`. |

## Intentos iniciales corregidos

- El primer intento de `reports\run_validation.py` falló al importar `config` porque el directorio raíz no estaba en `sys.path`; se corrigió el script y luego terminó con código 0.
- La primera ejecución de `reports\validate_data.py` falló en el parseo del timestamp del log; se corrigió el parser y la siguiente ejecución terminó con código 0.
- Una invocación de prueba con la ruta mal escrita `.\.venv\Scripts\python.exe ...` no fue reconocida por PowerShell; se corrigió y las pruebas enfocadas pasaron. La suite final completa pasó después.
- Antes de la auditoría anterior, la recolección de pytest había fallado porque faltaba `resolve_mt5_symbol`; la implementación y los tests ya están incluidos y pasan en la suite final.

No se ejecutó `order_send` contra MT5. El único test que llama `order_send` usa un objeto falso dentro de pytest.
