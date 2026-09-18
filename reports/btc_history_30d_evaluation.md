# Backtest causal BTC 5 minutos — 30 días

Estado: **NO_CANDIDATE_PASSED_DEVELOPMENT_GATE**.

- Ventana congelada: 30 días hasta `1789706100`.
- Mercados resueltos: 8,639.
- Observaciones utilizables: 25,857 de tres horizontes; los precios viejos o ausentes se excluyeron.
- Candidatos evaluados: 81.
- Capital por bloque: 100 SIM_USD; apuesta fija máxima: 10 SIM_USD.
- Separación temporal: entrenamiento 60%, validación 20%, holdout 20%, con embargo de 7,200 segundos.
- Resultado: ningún candidato tuvo evidencia positiva suficiente en entrenamiento y validación con costos conservadores.
- El holdout final no se abrió y permanece disponible para un modelo aprendido posterior.

La primera corrida produjo una ganancia artificial porque el precio de Bitcoin y el precio agregado del mercado no estaban alineados al mismo instante. Esa corrida fue descartada. El conjunto actual usa el precio de Bitcoin disponible en el instante causal del punto de Polymarket y conserva la antigüedad de ambos precios.

Esto es un backtest de señal. No contiene el libro histórico completo, prioridad de cola ni una garantía de ejecución.

Evidencia detallada: `btc_history_30d_evaluation.json`.
