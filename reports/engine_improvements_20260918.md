# Mejoras del motor — 18 de septiembre de 2026

Se aceleró la preparación histórica, se incorporó reanudación verificable y se abrió un experimento prospectivo del modelo congelado con 100 SIM_USD. No se volvió a entrenar el candidato ni se reabrió el holdout para elegir mejoras.

## Velocidad y recuperación

Se eliminó un recuento cuadrático de filas purgadas. La partición completa de 183,691 filas tardó 0.1252 s. En la misma muestra de 18,000 filas, sólo el recuento antiguo tardó 1.8304 s frente a 0.0102 s para toda la partición nueva: mejora de al menos 180 veces en esa operación, no en todo el entrenamiento. Las particiones y filas purgadas coincidieron. [Medición](split_benchmark_20260918.json).

`learn_btc_history.py` y `evaluate_btc_history.py` admiten `--checkpoint-dir`, escriben progreso y recuperan candidatos completados. Los checkpoints verifican datos, configuración, costos, código y entorno. Si comenzó la apertura del holdout y no quedó resultado final completo, se rechaza repetirla en ese directorio. Los CLI impiden sobrescribir reportes congelados. La protección pertenece al experimento/directorio: no es un registro global contra crear otro directorio y repetir una selección.

## Calidad de señal y capital

La [auditoría](btc_engine_audit.md) encontró que el horizonte nominal de 60 s usa principalmente características con horizonte efectivo de 180 s. También identificó cotizaciones históricas asincrónicas y ausencia de comprobaciones de frescura/saldo en el paper anterior. El importador ahora rechaza datos posteriores a la decisión y horizontes incoherentes; el hash del conjunto normalizado original se conserva.

El [stress de capital](btc_capital_stress.json) probó 18 escenarios sobre la validación **ya utilizada**. Reserva capital hasta la liquidación y compara apuestas fijas de 10/5 y Kelly fraccional acotado. Con 100 iniciales y apuesta fija de 10, los costos originales producen +223.70; con cinco centavos adicionales de deslizamiento, +52.39 en 20 operaciones. Son diagnósticos sobre precios históricos aproximados, no nueva evidencia independiente ni expectativa de retorno.

El modelo y el holdout originales siguen intactos: +1.6989, dos victorias de tres, IC95 [-25.55, 26.20], estado `HOLDOUT_INSUFFICIENT`. El conjunto solicitado como 270 días tiene cobertura desigual; no equivale a 270 días completos de libros ejecutables.

## Nuevo paper aprendido

Arranque: `INICIAR_BTC_APRENDIDO.bat`; parada: `DETENER_BTC_APRENDIDO.bat`. El iniciador evita duplicados y el journal se recupera tras interrupciones; todavía no hay arranque automático al encender Windows.

El manifiesto [btc_challenger_v5.json](../config/btc_challenger_v5.json) congela modelo, política y código antes de observar. Consulta libros frescos, sincroniza ambas caras y usa apertura de minuto con 120 cierres previos contiguos. El saldo empieza en 100 y reserva hasta 10 por apuesta. Recalcula probabilidad y margen después de 450 ms de latencia y descuenta comisión/deslizamiento. Liquida con resultados oficiales, sin anticipar cobros para financiar otra apuesta.

Los datos de señal son midpoints; los fills usan asks y profundidad limitada. Por eso se clasifica como experimento prospectivo nuevo, no reproducción exacta de los precios históricos. Las abstenciones también conservan probabilidades y resultados para medir calibración.

Cuatro pruebas operativas iniciales se preservaron sin predicciones ni fills: v1 descubrió caché atrasada en las velas de Coinbase; v2 mostró que un límite `end` exactamente al inicio del minuto lo excluye; v3 confirmó que un límite futuro fijo también conserva en caché la respuesta incompleta; v4 confirmó que esperar cinco segundos era insuficiente. Una [medición de publicación](coinbase_candle_publication_probe.json) observó la vela ausente a los 6.4 s y presente a los 11.6 s del minuto. Registros: [v1](btc_challenger_v1_stopped.json), [v2](btc_challenger_v2_stopped.json), [v3](btc_challenger_v3_stopped.json), [v4](btc_challenger_v4_stopped.json). No hubo selección entre resultados ganadores.

v5 consulta veinte segundos después de abrir el minuto, a 160 s del vencimiento, con `end` igual al instante exacto de solicitud. Mantiene el horizonte efectivo de características en 180 s, usa sólo la apertura actual y cierres estrictamente anteriores, y rechaza datos faltantes con detalle de los timestamps ausentes. El margen de publicación no garantiza disponibilidad futura: la condición de datos completos sigue siendo obligatoria.

Estado actual: [btc_challenger_live.json](btc_challenger_live.json). Diario separado: `data/btc_challenger_v5/events.jsonl`. El paper anterior permanece independiente; al corte de 13:40 UTC lleva 8 liquidaciones, cuatro victorias y **−5.35352756764 SIM_USD**. El saldo contable nominal es 994.64647243236, pero esa versión anterior no impone reservas de capital. Ninguna cuenta demuestra rentabilidad repetible.

La versión v5 registró su [primera predicción prospectiva válida](btc_challenger_first_prediction.json) a las 13:57 UTC: datos completos y frescos, abstención `NO_COSTED_EDGE`, cero fills y 100 SIM_USD disponibles. Esto verifica la captura corregida; no acredita una ganancia.

## JEV y verificación

Se preparó el adaptador de probabilidades con versión fija, límite de gasto persistente, plazo de respuesta y salidas estrictas. La llave fue aceptada por la consulta de modelos; la inferencia espera presupuesto positivo. El detalle y los límites están en [JEV_EXPERIMENT.md](../docs/JEV_EXPERIMENT.md).

Las pruebas cubren recuperación sin duplicados, saldo reservado, huecos/frescura, rechazo cuando desaparece el margen, checkpoints, etiquetas futuras, salida inválida de JEV, presupuesto y carga segura de `.env`. El total final y los comandos están en [ACCEPTANCE_REPORT.md](../ACCEPTANCE_REPORT.md).

[Verificación de esta ampliación](engine_verification_20260918.json): 250 pruebas en la suite, 8 repetidas tras el ajuste de captura, mypy en 51 archivos, Ruff y huellas del modelo/manifiesto comprobadas.
