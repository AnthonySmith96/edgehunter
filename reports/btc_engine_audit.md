# Auditoría del motor BTC — 18 de septiembre de 2026

La señal aprendida conserva una ventaja descriptiva en desarrollo, pero todavía no prueba rentabilidad ejecutable. La prioridad es trasladar correctamente sus datos de entrada al experimento prospectivo y descontar el dinero pendiente de liquidación antes de simular otra compra.

Esta auditoría no ajustó coeficientes ni buscó parámetros nuevos. Usó el modelo y los umbrales congelados de `btc_history_270d_learned.json` únicamente sobre entrenamiento y validación. Excluyó las observaciones con epoch >= 1789195800 antes de construirlas para el análisis. La validación ya había seleccionado el modelo: las cifras siguientes son diagnósticos, no una nueva prueba independiente.

## Hallazgos prioritarios

1. **El horizonte nominal no describe la antigüedad de las variables.** `btc_history_download.py:605` fija el precio BTC en la apertura del minuto del timestamp más antiguo de ambos precios de mercado; `:615` calcula el horizonte efectivo hasta vencimiento. Para el candidato nominal de 60 segundos, 44,696 de 44,795 filas de entrenamiento y 14,980 de 14,982 filas de validación tienen horizonte efectivo de **180 segundos**. Las 48 operaciones de validación usan 180 segundos. `learned_intraday.py:46` utiliza correctamente ese horizonte efectivo. Alimentarlo prospectivamente con ticker actual y 60 segundos restantes cambiaría el significado de sus entradas. La réplica debe reconstruir las mismas entradas causales; cualquier cambio de representación se debe registrar como candidato nuevo.

2. **Los precios de ambos resultados pueden corresponder a instantes distintos.** El constructor admite timestamps separados; la normalización `up/(up+down)` en `learned_intraday.py:44` los trata como pareja. En las 64 operaciones de entrenamiento la separación mediana fue 43.5 segundos y la suma mediana de precios 0.795. Eso puede producir aparente margen a partir de precios que no estaban disponibles simultáneamente como órdenes ejecutables. En validación la separación mediana fue sólo 2 segundos y la suma mediana 1.0. Es incorrecto atribuir toda la ganancia a esa anomalía: el grupo de validación con separación <=5 segundos produjo 46 operaciones y +226.74; el grupo con suma >=0.95 produjo 30 y +211.32. Estos grupos se describen para diagnosticar, no para seleccionar otra estrategia.

3. **El paper original no comprobaba frescura ni capital disponible.** En la versión auditada de `scripts/run_btc_paper.py:100`, `book()` verificaba sólo asset_id. El timestamp se guardaba en el fill (`:321`) pero no se validaba. `coinbase_inputs()` (`:107`) guardaba ticker_time sin comprobar antigüedad ni continuidad de velas. El tamaño (`:297-310`) estaba limitado a 10 por operación y a profundidad visible, sin descontar pérdidas ni capital comprometido en fills aún abiertos. El libro posterior a la latencia debe validarse y el saldo simulado debe reconstruirse del registro de eventos. Estas observaciones corresponden a la versión anterior a las mejoras concurrentes de esta sesión.

4. **El precio posterior a la latencia puede consumir el margen que justificó la compra.** La versión original de `btc_lag.choose_candidate()` estimaba entrada ask+1 tick, mientras `run_btc_paper.py:276` permitía ask+2 ticks. El fill sólo verificaba ese límite (`:290`), sin recalcular margen neto con precio final. Debe respetarse el umbral preregistrado usando precio, comisión y probabilidad final autorizada.

5. **El importador de observaciones tiene un control causal incompleto.** `intraday_dataset.py:91-94` rechaza timestamps posteriores al vencimiento, pero admite timestamps posteriores a la decisión `epoch + 300 - horizon_seconds`. El descargador sí aplica un filtro de disponibilidad causal. El importador debería imponer también ese límite y la coherencia del horizonte efectivo para que un archivo externo no omita esa protección.

6. **La contabilidad histórica supone liquidación inmediata entre operaciones.** `learned_intraday.py:148` y `robust_intraday.py:272` agregan el payout al capital después de cada operación sin una hora de disponibilidad del pago. Los mercados no se solapan como contratos de cinco minutos, pero su liquidación oficial puede demorar. `capital_path_executable=true` describe solamente suficiencia de saldo bajo esa hipótesis; no acredita disponibilidad de efectivo durante demoras. El paper nuevo debe reservar fondos hasta la liquidación oficial.

## Diagnósticos del modelo congelado

| Medida | Entrenamiento | Validación |
|---|---:|---:|
| Filas del horizonte seleccionado | 44,795 | 14,982 |
| Operaciones aprendidas | 64 | 48 |
| PnL aprendido, costos originales | +154.34 | +223.70 |
| Operaciones usando sólo probabilidad normalizada de mercado, mismos umbrales | 40 | 16 |
| PnL usando sólo esa probabilidad de mercado | +98.56 | -0.96 |
| Brier aprendido, menor es mejor | 0.1845155 | 0.1726359 |
| Brier de probabilidad normalizada de mercado | 0.1845572 | 0.1726714 |
| Probabilidad media prevista del lado comprado | 74.11% | 67.75% |
| Frecuencia ganadora observada del lado comprado | 73.44% | 79.17% |

El cambio global en Brier es muy pequeño. El beneficio propuesto se concentra en pocas señales; la media global de precisión no es una prueba suficiente a favor ni en contra. La comparación con mercado mantiene los mismos costos y umbrales congelados y no vuelve a entrenar. El baseline y el candidato conservan la limitación compartida de precios históricos muestreados, sin libro ejecutable.

En validación las 48 operaciones estuvieron distribuidas en julio (2, -2.14), agosto (22, +106.05) y septiembre antes del holdout (24, +119.79). Esa frecuencia explica parte de la lentitud: sólo 0.32% de las filas del horizonte seleccionado se convirtieron en operaciones. Aumentar frecuencia cambiando umbrales no demuestra una ventaja más rápido; también cambia la estrategia.

## Mejoras acotadas recomendadas

- Congelar la pareja de modelo y transformación de entradas; ejecutar un challenger prospectivo con diario y saldo propios.
- Conservar precio histórico de señal separado de ask ejecutable y registrar ambos timestamps, horizonte efectivo, antigüedad y costo final.
- Abstenerse ante entradas viejas, futuros timestamps, huecos en velas, libro inválido o fondos insuficientes.
- Recalcular margen neto en el precio posterior a la latencia antes del fill.
- Mantener el holdout ya abierto como evidencia histórica; las nuevas decisiones necesitan una nueva ventana prospectiva.

Evidencia numérica completa: [btc_engine_audit_diagnostic.json](btc_engine_audit_diagnostic.json). Ningún resultado aquí cambia `financial_edge_proven`.
