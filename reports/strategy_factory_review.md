# Revisión de estrategia autónoma y backtest

Fecha: 2026-10-14

## Cambios

- `src/strategy_evaluator.py` ahora construye siete recetas limitadas y explícitas: cuatro de reglas, una de IA y dos híbridas, variando filtros RSI, umbrales de probabilidad y niveles de SL/TP.
- Usa 50/20/15/15% cronológico: entrenamiento, selección, validación independiente y test final. La selección rankea candidatas solo en el tramo de selección; la validación es un gate único para la receta elegida. El test solo se calcula si pasan ambas etapas y nunca modifica la elección.
- El evaluador exige `source_verified=True` para devolver una receta operable. El reporte del CSV local no puede fijar esta marca porque la procedencia no está autenticada, así que queda en NO TRADE.
- Una candidata debe obtener al menos cinco operaciones, expectativa y retorno positivos, profit factor >= 1,05 y drawdown <= 5% en cada uno de los tres segmentos. Si ninguna cumple, se abstiene (`NO TRADE`).
- `src/autonomous_agent.py` toma la configuración de la receta elegida también para calcular el riesgo y definir SL/TP. Así no evalúa una configuración y ejecuta otra.
- `app.py` y el ciclo bloquean timeframes menores que M15. Las opciones quedan M15, M30, H1 y H4; el intervalo de ciclo de 10/15 minutos no cambia el timeframe del mercado.
- No hay aprendizaje online de parámetros ni generación ilimitada. Es una selección periódica de recetas previamente acotadas, para limitar el sobreajuste.

## Ejecución reproducible

Desde la raíz del proyecto:

```powershell
.\.venv\Scripts\python.exe reports\evaluate_agent_strategies.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Para un CSV recién descargado con manifest lateral, se puede indicar `--data`, `--symbol` y `--output`:

```powershell
.\.venv\Scripts\python.exe reports\evaluate_agent_strategies.py --data data\history\EURUSDmicro_M15_2025.csv --symbol EURUSDmicro --output reports\xm_eurusdmicro_m15.json
```

El primer comando no conecta a MT5 ni envía órdenes. Los detalles completos por receta, bloque, costes y tramo de prueba están en `reports/agent_strategy_evaluation.json`.

## Resultado de esta ejecución

- Datos: 5.000 velas M15, 2026-07-31 16:45 UTC a 2026-10-13 18:30 UTC; SHA-256 `512d65e10248a09d7a9b592aa90201c31226cef99ed49367cb6b2e9fb1f13cc1`. Split: train 2.500, selection 1.000, validation 750, test 750.
- La procedencia se infiere de logs locales como EURUSDmicro, pero no está autenticada de forma independiente; símbolo/cuenta/specs del broker no están confirmados.
- Costes sensibilizados: base 1,5 pips spread, 0 comisión, 0,2 pips slippage por lado; superior 2/0,07/0,5; estrés 3/0,14/1. Son hipótesis, no tarifas observadas de XM; swap no estimado.
- En selection, reglas base: −0,31%, expectativa −USD 0,77, PF 0,70, DD −0,46%, 41 operaciones; híbrida: −0,16%, expectativa −USD 0,83, PF 0,68, DD −0,32%, 19 operaciones.
- En validation, reglas base +0,36%, expectativa USD 0,92, PF 1,38, DD −0,37%, 39 operaciones; híbrida +0,27%, expectativa USD 1,19, PF 1,44, DD −0,14%, 23 operaciones.
- En el test final, reglas base −0,58%, expectativa −USD 1,86, PF 0,55, DD −0,67%, 31 operaciones; híbrida −0,07%, expectativa −USD 0,46, PF 0,87, DD −0,23%, 14 operaciones. Bajo costes de estrés, híbrida −0,38%, PF 0,45.
- AUC del modelo: 0,558 en validation y 0,508 en test. El test no demuestra ventaja. El selector de siete recetas dio `selected=null`, `no_candidate_passed_selection`, y el CSV sin manifest no supera el gate de fuente; acción `NO TRADE`.
- Los resultados no son evidencia fiable de una ventaja económica: los precios no tienen procedencia autenticada, símbolo y especificaciones micro son supuestos, las tarifas reales y el swap son desconocidos, y la muestra es corta. No habilitar trading real por estos resultados.
- Suite completa: **101 passed, 0 failed** (112,76 s), incluyendo mocks y sintéticos; no valida el terminal XM ni ejecuta órdenes.

## Feedback de auditoría

La arquitectura separa señal, evaluación, sizing, broker y ciclo autónomo; el backtester ejecuta la señal de la vela cerrada en la apertura siguiente y modela spread, comisión y slippage configurados. La selección conservadora permite permanecer fuera del mercado. Persisten estas barreras:

1. Confirmar origen/instrumento de los datos con una descarga identificable y archivada; recolectar suficiente histórico de EURUSDmicro/EURCADmicro y timeframe >= M15.
2. Leer de MT5 los contratos, lotes, tick values, spreads, stops, margen, comisiones y swaps reales. Los tests de volumen del proyecto usan fixtures, no specs MT5.
3. Validar en demo con feed de ticks/costes observados, forward test y reconciliación de operaciones. No se hizo conexión MT5 en esta ejecución.
4. Reevaluar la estrategia en periodos más largos e instrumentos independientes; el actual resultado obliga a NO TRADE y no justifica una cuenta real.
