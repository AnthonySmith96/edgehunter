# Fuentes y proveedores

Verificación de interfaces: 17 de septiembre de 2026. Las llamadas de esta entrega leen datos públicos o usan servicios exclusivamente locales. El detalle de las consultas ejecutadas está en `reports/sources.json`; una prueba con respuesta simulada no acredita acceso a un proveedor.

| Fuente | Implementación y evidencia | Límite |
|---|---|---|
| Polymarket Gamma / CLOB | Catálogo, contratos, libro, fees, hora, geoblock e históricos consultados realmente por HTTPS GET | Sin cuenta, SDK firmado, canal de usuario, órdenes ni redención |
| Coinbase | Cotización pública BTC-USD consultada | Spot de ese producto; no cobertura completa ni ejecución |
| Federal Reserve RSS | Comunicados consultados, deduplicados y normalizados | Radar informativo; no presume efecto financiero cuantificado |
| SEC / NOAA | Adaptadores de lectura y validación de identidad/estación | No hay captura operativa validada para una tesis concreta |
| Alpha Vantage | `GLOBAL_QUOTE` con esquema comprobado en pruebas | Falta clave y entitlement; no presume datos en tiempo real |
| JEV / TypeSafe | Inferencia externa verificada, respuesta estructurada, versión devuelta registrada, cache, presupuesto durable y circuit breaker | La sesión prospectiva perdió 35.6451 SIM_USD; su Brier fue peor que el mercado y no demuestra edge |
| WeatherNext / BigQuery | Consulta parametrizada, dry-run y límite de bytes, ubicación y horizonte | Sin cuenta/allowlist; no se descargaron miembros reales ni se calibró una señal meteorológica |
| Brevo | Outbox, tratamiento de reintentos/UNKNOWN y adaptador HTTP probado con mock | No se envió correo externo ni se validaron webhooks de entrega |
| PocketBase 0.40.4 | Binario oficial con SHA-256, migraciones, hooks, roles y pruebas HTTP locales reales | Administrador loopback, sin MFA/remoto certificado |
| Social, opciones y derivados | Registro de ausencia de insumos | Sin proveedor licenciado ni métricas inventadas |

`PublicHTTP` admite GET hacia hosts explícitos, valida destinos públicos, limita tamaño/concurrencia, aplica backoff y no sigue redirects. Las noticias son datos sin autoridad para ejecutar herramientas, modificar políticas o elegir destinos de pago. La inteligencia no recibe credenciales de trading. Jev y WeatherNext tienen reservas de costo; sin presupuesto explícito no llaman al servicio.

Las observaciones conservan timestamps disponibles, primera observación local, URL y hashes. Un timestamp desconocido se mantiene desconocido. Los históricos no tienen disponibilidad observada en tiempo real: su demora es inferida y está marcada así. La deriva del reloj del equipo no se oculta cambiando los datos.

El daemon actual usa sondeo REST de mercados binarios con límites de recursos. `BookState` invalida actualizaciones/gaps y requiere reconstrucción REST, pero **no se implementó la suscripción WebSocket persistente**. El radar Coinbase/RSS se ejecuta mediante `sources probe`; no es aún un servicio continuo multiactivo. El archivo Parquet tiene cuota local aproximada de 512 MB y no borra evidencia para hacer espacio.

Documentación primaria consultada:

- [Polymarket: mercados](https://docs.polymarket.com/api-reference/markets/list-markets), [libro](https://docs.polymarket.com/api-reference/market-data/get-order-book), [fees](https://docs.polymarket.com/trading/fees), [datos en tiempo real](https://docs.polymarket.com/market-data/realtime-data), [geoblock](https://docs.polymarket.com/api-reference/geoblock).
- [Coinbase ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker), [Alpha Vantage](https://www.alphavantage.co/documentation/).
- [Typesafe API](https://docs.typesafe.ai/api), [WeatherNext acceso](https://developers.google.com/weathernext/guides/access-forecast), [WeatherNext BigQuery](https://developers.google.com/weathernext/guides/bigquery), [BigQuery query](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query).
- [PocketBase migraciones](https://pocketbase.io/docs/js-migrations/), [autenticación](https://pocketbase.io/docs/authentication/), [producción](https://pocketbase.io/docs/going-to-production/).

El acceso público no implica permiso irrestricto de redistribución, elegibilidad personal o gratuidad de proveedores con cuenta. Esta entrega conserva la investigación local y no publica datasets ni autoriza contratación.
