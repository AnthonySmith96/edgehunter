# Evaluación causal BTC 5 minutos — 270 días

Estado final: **señal aprendida prometedora, rentabilidad todavía no demostrada**.

## Datos utilizados

- Ventana solicitada: 270 días hasta el corte congelado `1789706100`.
- Mercados resueltos: 73,112.
- Registros de precios: 67,204; 62,793 tenían un precio causal utilizable.
- Observaciones finales: 183,691 en horizontes de 60, 120 y 180 segundos.
- Los mercados sin precio causal, con precio demasiado viejo o sin velas suficientes se excluyeron; no se rellenaron con datos inventados.
- El precio de Bitcoin se alineó al timestamp del precio de Polymarket disponible en ese instante. La primera corrida de 30 días que mezclaba instantes fue descartada.
- Hash del conjunto normalizado evaluado: `d855c8dd55c71805b3b3199ecc9600f2bc0597496a240a13d62596c14d88a6fa`.

## Reglas manuales

- Se evaluaron 81 reglas con 100 SIM_USD, apuestas de 10, costos conservadores y embargo temporal de 7,200 segundos.
- Seis fueron positivas en entrenamiento, nueve en validación y tres en ambos periodos.
- Las tres positivas en ambos periodos conservaron límites inferiores negativos en sus IC95. Ninguna pasó el gate estadístico.
- Estado: `NO_CANDIDATE_PASSED_DEVELOPMENT_GATE`.
- El holdout no se abrió para esta familia.

## Modelo aprendido

- Se evaluaron 96 variantes de regresión logística regularizada. El modelo sólo se ajustó con entrenamiento; validación escogió una variante y el holdout no participó en la selección.
- Catorce variantes pasaron el gate de desarrollo.
- Variante seleccionada: horizonte 60 s, `ridge=100`, edge mínimo 0.06 y ask máximo 0.85.
- Entrenamiento: 64 operaciones, 47 victorias, +154.3360; IC95 [17.8976, 309.5478].
- Validación: 48 operaciones, 38 victorias, +223.7019; IC95 [91.0681, 381.5517].
- Holdout congelado: 3 operaciones en 3 días, 2 victorias, +1.6989; IC95 [-25.5514, 26.1997].
- Estado: `HOLDOUT_INSUFFICIENT`. El resultado no alcanzó el mínimo preregistrado de 20 operaciones y 4 días, y el intervalo cruza cero.

## Lectura práctica

El modelo aprendido merece continuar como candidato paper congelado: ganó en desarrollo y quedó ligeramente positivo en la reserva. Tres operaciones no permiten distinguir una ventaja real de la suerte. No se debe cambiar la regla usando el resultado del holdout; la siguiente evidencia válida tiene que venir de operaciones prospectivas nuevas.

El paper preregistrado original sigue en ejecución por separado. Al cierre de este informe llevaba 7 liquidaciones, 4 victorias, 3 derrotas y +4.64647208356 SIM_USD, todavía con `financial_edge_proven=false`.

Evidencia detallada:

- `btc_history_270d_evaluation.json`: reglas manuales, splits, métricas y hashes.
- `btc_history_270d_learned.json`: selección aprendida, coeficientes congelados y holdout.
- `../data/btc_history_270d/merged/manifest.json`: conteos y hashes de los insumos fusionados.

Es un backtest de señal con costos conservadores. No contiene el libro histórico completo, prioridad de cola ni prueba de ejecución real.
