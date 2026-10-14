# Revisión de estrategia autónoma y backtest

Fecha: 2026-10-14

## Cambios

- `src/strategy_evaluator.py` ahora construye siete recetas limitadas y explícitas: cuatro de reglas, una de IA y dos híbridas, variando filtros RSI, umbrales de probabilidad y niveles de SL/TP.
- Entrena el modelo exclusivamente con el primer 60% cronológico. La selección utiliza solamente el siguiente 20%, separado en tres segmentos; el 20% final se calcula y se informa como diagnóstico, pero no participa en la elección.
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

El primer comando no conecta a MT5 ni envía órdenes. Los detalles completos por receta, bloque, costes y tramo de prueba están en `reports/agent_strategy_evaluation.json`.

## Resultado de esta ejecución

- Datos: 5.000 velas M15, 2026-07-31 16:45 UTC a 2026-10-13 18:30 UTC; SHA-256 `512d65e10248a09d7a9b592aa90201c31226cef99ed49367cb6b2e9fb1f13cc1`.
- La procedencia se infiere de logs locales como EURUSDmicro, pero no está autenticada de forma independiente; símbolo/cuenta/specs del broker no están confirmados.
- Costes usados: spread 1,0 pip, comisión 7 USD por lote ida y vuelta y slippage 0,2 pip por lado. Son valores configurados/hipotéticos, no tarifas observadas de XM; no se pudo valorar swap.
- Ninguna receta pasó los filtros de validación; la selección resultó `null` y la acción es `NO TRADE`.
- En el tramo final de prueba, solo como diagnóstico: reglas equilibradas −9,93%, expectativa −USD 34,25, PF 0, drawdown −10,05%, 29 operaciones; híbrida equilibrada −7,27%, expectativa −USD 34,61, PF 0, drawdown −7,29%, 21 operaciones; híbrida selectiva −3,36%, expectativa −USD 33,59, PF 0, drawdown −3,44%, 10 operaciones. La referencia de no operar fue 0%, 0 operaciones y DD 0%.
- Los resultados no son evidencia fiable de una ventaja económica: los precios no tienen procedencia autenticada, símbolo y especificaciones micro son supuestos, las tarifas reales y el swap son desconocidos, y la muestra es corta. No habilitar trading real por estos resultados.
- Suite completa después de los cambios: **83 passed, 0 failed**. Incluye pruebas con fakes y datos sintéticos; no valida el terminal XM ni ejecuta órdenes.

## Feedback de auditoría

La arquitectura separa señal, evaluación, sizing, broker y ciclo autónomo; el backtester ejecuta la señal de la vela cerrada en la apertura siguiente y modela spread, comisión y slippage configurados. La selección conservadora permite permanecer fuera del mercado. Persisten estas barreras:

1. Confirmar origen/instrumento de los datos con una descarga identificable y archivada; recolectar suficiente histórico de EURUSDmicro/EURCADmicro y timeframe >= M15.
2. Leer de MT5 los contratos, lotes, tick values, spreads, stops, margen, comisiones y swaps reales. Los tests de volumen del proyecto usan fixtures, no specs MT5.
3. Validar en demo con feed de ticks/costes observados, forward test y reconciliación de operaciones. No se hizo conexión MT5 en esta ejecución.
4. Reevaluar la estrategia en periodos más largos e instrumentos independientes; el actual resultado obliga a NO TRADE y no justifica una cuenta real.
