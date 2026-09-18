# Experimento 2: calibración regularizada

Estado: **EXPLORATORY_POSITIVE**. Sólo simulación; edge financiero no demostrado; live bloqueado.

Modelo congelado antes de descargar septiembre: `residual_prior10_z0_category1_edge0.0025`.
Desarrollo: 497 observaciones ya inspeccionadas del experimento1; selección walk-forward pasó: False.
Holdout fijo: decisiones del 1 al 16 de septiembre2026; 217 observaciones de 469 eventos consultados.

| Escenario | Operaciones | PnL ficticio neto | Capital final (1000 inicial) | IC95 descriptivo por días observados |
|---|---:|---:|---:|---|
| optimistic | 33 | 5.0025 | 1005.0025 | [-27.488951193200005, 33.820203693799996] |
| base | 33 | 1.7987 | 1001.7987 | [-30.431118162600004, 30.320030154324996] |
| conservative | 33 | -6.4038 | 993.5962 | [-38.21883750975, 21.3054731605] |

Se preserva el fracaso del experimento1. Sus datos ya fueron observados y son desarrollo para el2; las fechas/eventos nuevos no se reutilizaron para elegir modelo. Los escenarios cambian costos y restricciones de ejecución, manteniendo la regla de señal base.

Se informan ambos experimentos y las25 especificaciones consideradas (7+18). Un saldo positivo es un resultado histórico exploratorio, no evidencia de rentabilidad real. Los intervalos por días son descriptivos y pueden subestimar dependencia entre eventos/días.

No existen books, fills ni fees históricos verificados; timestamps de disponibilidad, categorías vigentes y demora de redención son aproximaciones. No se ha completado una prueba prospectiva.

Reproducir: `.venv\Scripts\python.exe scripts/research_calibrated.py --offline`. Datos/hash/modelo: `../data/research_v2/`. Operaciones: `research_calibrated_trades.csv`.
