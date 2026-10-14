# forex-ai

Proyecto de trading de divisas (forex) con **Python + pandas + scikit-learn**. Funciona en
**demo** por defecto y tiene un **modo real** protegido por doble confirmación. Incluye indicadores, estrategia por reglas, modelo de
IA, gestión de riesgo, backtesting realista y logs de operaciones.

> ⚠️ Nada aquí es asesoría financiera ni promete ganancias. El forex tiene riesgo alto
> y la mayoría de estrategias pierden dinero en real. El CSV actual parece proceder de una
> descarga MT5 de EURUSDmicro según los logs locales, pero no guarda identidad de cuenta ni
> especificaciones del símbolo autenticables. Los datos sintéticos se generan con `--source synthetic`;
> no uses resultados sintéticos o sin costes verificados como evidencia de rentabilidad.

## Inicio rápido en Windows (recomendado)

Doble clic en **`iniciar.bat`**. Instala/verifica Python 3.12, crea el entorno, instala las librerías,
te pide tus datos de MT5 (la contraseña no se muestra), detecta la ruta de MetaTrader 5, prueba la
conexión, corre los tests, descarga datos, entrena el modelo, hace un backtest y te muestra un menú
(simular / demo / real). Se puede repetir las veces que quieras. Abre MT5 y déjalo conectado antes.

## Instalación manual

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # y completa credenciales de una cuenta DEMO de MT5 (opcional)
```

## Uso

```bash
python app.py download --source synthetic   # o --source mt5 (Windows + MetaTrader 5 abierto)
python app.py train                         # entrena y guarda models/forex_model.joblib
python app.py backtest --mode rules         # solo reglas técnicas
python app.py backtest --mode ai            # solo modelo de IA
python app.py backtest --mode hybrid        # reglas + confirmación de la IA
python app.py demo --broker paper           # simula trading sobre las últimas velas
python app.py demo --broker mt5             # cuenta DEMO de MT5 (rechaza cuentas reales)
python app.py demo --broker mt5 --live      # cuenta REAL (ver sección siguiente)
python app.py autonomous --symbol EURUSDmicro --cycle-minutes 15  # análisis periódico, sin órdenes
python app.py autonomous --symbol EURUSDmicro --cycle-minutes 10  # intervalo alternativo
python -m pytest -q -p no:cacheprovider    # tests
```

Opciones útiles: `--symbol GBPUSD --timeframe M15 --mode hybrid --log-level DEBUG`.

El comando `autonomous` usa M15 por defecto, lee velas cerradas y evalúa reglas, IA e híbrida
con entrenamiento/validación/test cronológicos. Solo selecciona una estrategia si supera
los filtros en cada subperiodo de validación; el test queda reservado para diagnóstico.
Si ninguna califica, registra `NO TRADE`. Las decisiones van a `logs/autonomous_decisions.jsonl`
y la vela procesada y límites de riesgo a `logs/autonomous_state.json`. No ejecuta órdenes
por defecto. `--execute` habilita órdenes de la sesión MT5 conectada; una cuenta REAL sigue
requiriendo `--live`, el gate de entorno y la confirmación escrita existentes.

## Modo real (dinero real)

Está bloqueado salvo que cumplas **todo** esto:

1. `ALLOW_LIVE_TRADING=YES_I_ACCEPT_THE_RISK` en tu `.env`.
2. Ejecutar con `--live` y escribir a mano `OPERAR EN REAL` cuando se pida.
3. Cuenta en **USD** (el cálculo de lotes asume USD), con "Algo Trading" activo en MT5.

Con `--live` se aplican límites más estrictos (`config.live_risk`): 0.25 % de riesgo por operación,
1 % de pérdida diaria, 5 % de drawdown máximo (el bot se detiene), 3 operaciones/día y 0.5 lotes máx.
Toda orden lleva Stop Loss obligatorio y los cierres del servidor se registran en `logs/trades.csv`
con `source=live`. Si el bot se detiene, las posiciones abiertas siguen en MT5 con su SL/TP: revísalas.

**Recomendación:** no pases a real sin haber probado con datos reales de MT5 y varias semanas en demo.
La conexión a MT5 (órdenes y lectura de historial) está escrita según la API oficial pero **no pudo
probarse contra un terminal real** en este entorno; valídala primero en demo.

## Cómo funciona

| Módulo | Qué hace |
|---|---|
| `config.py` | Parámetros + credenciales desde `.env` (nunca en el código). |
| `src/market_data.py` | Velas desde MT5, CSV o datos sintéticos. Solo velas **cerradas**. |
| `src/indicators.py` | EMA, RSI, MACD, ATR, Bollinger. |
| `src/strategy.py` | Reglas (tendencia + momentum) y combinación con la IA. |
| `src/ai_model.py` | RandomForest: predice si el precio sube > 0.5 ATR en 6 velas. Validación temporal con purga. |
| `src/risk_manager.py` | 1 % de riesgo por operación, límite diario 3 %, drawdown máx. 10 % (kill-switch), 1 posición, 5 ops/día. |
| `src/backtester.py` | Entrada en apertura siguiente, costes supuestos, slippage configurable, SL antes que TP si se tocan a la vez y drawdown intravela aproximado; swaps no incluidos. |
| `src/broker_demo.py` | `PaperBroker` (simulador) y `MT5DemoBroker` (rechaza cuentas no demo). |
| `src/strategy_evaluator.py` | Comparación temporal de reglas/IA/híbrida y abstención si validación no aporta evidencia estable. |
| `src/autonomous_agent.py` | Ciclo periódico, filtro de datos/spread, gestión de vela duplicada, stops, estado persistente y log de decisiones. |
| `src/logger.py` | `logs/app.log` (rotativo) y `logs/trades.csv` (una fila por operación). |

El backtest entrena el modelo con el 70 % inicial y evalúa en el 30 % final (**fuera de muestra**).

## Antes de pensar en dinero real

- Prueba con datos reales de MT5 y varios pares/periodos; desconfía de resultados demasiado buenos.
- Un AUC cercano a 0.50 en datos reales es lo normal: significa que el modelo no aporta ventaja.
- Haz forward-testing en demo semanas antes de sacar conclusiones.
- Limitaciones conocidas: el valor del pip en cruces (EURGBP, EURJPY…) es aproximado (en cuentas
  USD con pares XXX/USD o USD/XXX es exacto); en modo `paper` la orden se ejecuta al cierre de la
  vela de la señal; el spread real varía y puede ser mayor al simulado en noticias.
