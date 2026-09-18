# JEV: integración y evaluación prospectiva

Revisión del 18 de septiembre de 2026. JEV ya recibe snapshots públicos por la [API TypeSafe](https://docs.typesafe.ai/api), devuelve probabilidades UP/DOWN y dirige entradas de dinero ficticio. La ejecución calcula costos, límites y tamaño en código. El registro local `data/btc_jev/events.jsonl` conserva las inferencias y liquidaciones; no es únicamente una prueba de acceso ni un mock.

## Resultado comprobado

El último corte de [btc_jev_live.json](../reports/btc_jev_live.json), 22:04 UTC, contiene 20 operaciones cerradas: 12 ganadas, 8 perdidas, PnL de −35.6451 SIM_USD y saldo de 64.3549 desde 100. La revisión encontró el proceso detenido aunque ese reporte antiguo decía `OBSERVING_AND_READY`; el archivo histórico se conserva intacto.

Las 33 decisiones se contrastaron con resoluciones públicas oficiales, incluyendo abstenciones: JEV y la dirección implícita del mercado sumaron 26 aciertos cada uno, con dos desacuerdos entre sus pronósticos. Brier: JEV 0.169533; mercado 0.130502. Log-loss: JEV 0.513929; mercado 0.412678. Menor es mejor en ambas medidas. Son datos de una sesión, insuficientes para acreditar rentabilidad repetible. El resultado histórico de 10/10 no describe la rentabilidad prospectiva. Desglose y límites en [la revisión](../reports/jev_review_20260918.md).

## Configuración y operación

`.env` está excluido de Git. El cargador no interpola ni ejecuta contenido; las variables del proceso prevalecen sobre el archivo y los argumentos CLI sobre ambos.

| Variable | Función |
|---|---|
| `TYPESAFE_API_KEY` | Llave privada de TypeSafe |
| `TYPESAFE_MODEL` | Modelo solicitado; el ejemplo usa `jev-latest` |
| `TYPESAFE_BUDGET_USD` | Límite acumulado para API; la plantilla usa cero y bloquea inferencias hasta que el usuario autorice un monto positivo |
| `TYPESAFE_MAX_CALL_USD` | Reserva por intento, sin equivaler a un precio facturado confirmado |
| `TYPESAFE_TIMEOUT_SECONDS` | Tiempo máximo de respuesta |

La reserva persiste en `data/btc_jev/usage.db`; reiniciar no restablece lo consumido. Los tokens devueltos no son una factura. El saldo `SIM_USD` es ficticio; el uso de la API puede tener costo real.

`INICIAR_BTC_JEV.bat` inicia el runner y `DETENER_BTC_JEV.bat` solicita su parada. Estos cambios no reiniciaron el proceso ni enviaron nuevas inferencias pagadas. El reporte conserva todas las operaciones anteriores; una nueva revisión de ejecución se identifica en los eventos nuevos, sin restablecer el capital ni borrar pérdidas.

## Correcciones y medición futura

- Kelly fraccionario deja de elevar una apuesta pequeña al mínimo: si el tamaño calculado no alcanza el mínimo, se abstiene. Los límites se validan y también se aplican al modo fijo.
- El runner respeta un presupuesto API de cero y carga `.env` antes de elegir los valores del CLI.
- Se comprueban el reloj después de obtener datos, la frescura del ticker, la apertura real disponible y al menos 30 cierres contiguos para volatilidad. No se inventa apertura ni volatilidad cuando faltan datos.
- Se vuelve a consultar el libro después de la inferencia. Una subida por encima del límite, profundidad insuficiente o vencimiento impide el fill simulado.
- Se registran desenlaces oficiales de todas las predicciones, incluidas las abstenciones. Los reportes nuevos comparan Brier y log-loss con el mercado sobre exactamente la misma cohorte; las respuestas fallidas no cuentan como pronósticos de 50%.
- La escritura del reporte evita JSON parciales y la parada normal publica `STOPPED`. Un cierre forzado del sistema todavía requiere comprobar fecha y proceso.

JEV sigue siendo el predictor. No se ajustaron umbrales para hacer ganar retrospectivamente esta sesión. La siguiente evaluación debe acumular datos futuros con configuración estable, medir calibración y PnL juntos, y contrastar cualquier recalibración en un periodo posterior al usado para ajustarla.

## Límites pendientes

Coinbase es un proxy del precio de referencia de resolución. La sensibilidad de comisión `0.10` y el deslizamiento son supuestos de simulación; no acreditan costos ejecutados. La [documentación de Polymarket](https://docs.polymarket.com/trading/fees) indica consultar parámetros por mercado. No se cambiaron retrospectivamente costos ni resultados.

`jev-latest` puede cambiar de versión. Cuando la API devuelve una versión explícita, las decisiones nuevas la conservan; si devuelve sólo el alias, no permite conocer los pesos exactos. TypeSafe recomienda [mantener la aritmética en código](https://docs.typesafe.ai/model-jaggedness/jev-1.13). Las consultas no demuestran por sí mismas calibración en BTC.

Las sondas previas `benchmark_jev.py` y `score_jev_shadow.py` siguen siendo un experimento separado, con presupuesto y datos propios. El comparador histórico de 10 observaciones no es un holdout representativo del runner actual ni usa necesariamente su misma ejecución.
