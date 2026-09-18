# EdgeHunter — Runbook maestro de construcción y operación

Versión: 1.0 · Fecha: 17 de septiembre de 2026 · Propietario: EdgeHunter contributors

## 0. Instrucción al harness: construye el producto

Este documento es una especificación ejecutable para un harness con modelo frontier y herramientas de desarrollo. No entregues otra propuesta ni solamente un scaffold: implementa, ejecuta pruebas, corrige, documenta y deja el sistema funcionando en el entorno autorizado. Trabaja hasta completar todo lo técnicamente realizable. No confundas «software implementado» con «estrategia rentable», «integración simulada» con «integración verificada», ni «deployment preparado» con «deployment realizado».

El usuario quiere intervenir únicamente en lo irreductiblemente humano: identidad/KYC, CAPTCHA/2FA, aceptación personal de contratos, autorizaciones económicas, firmas sensibles, depósitos/retiros y accesos que no puedas obtener legítimamente. Automatiza el resto con las herramientas y permisos disponibles. Si falta una credencial, implementa y prueba el adaptador sin ella, deja la integración bloqueada de forma explícita y continúa con los otros módulos. Consolida las intervenciones pendientes en una sola lista breve con enlaces, motivo y acción exacta.

### Mandato de ejecución

1. Lee este documento completo y los `AGENTS.md` aplicables. Inspecciona el repositorio y preserva cambios existentes.
2. Crea `IMPLEMENTATION_STATUS.md` con cada requisito, estado, evidencia y bloqueo. Mantén esa matriz durante el trabajo y úsala para retomar después de compactaciones.
3. Investiga APIs/SDK vigentes en documentación oficial; fija versiones compatibles y registra fecha, fuente, costos, permisos y límites. No instales paquetes por parecido de nombre.
4. Implementa el producto completo por incrementos verticales, comenzando por un recorrido real desde ingestión hasta decisión, paper, bitácora y notificación.
5. Ejecuta tú instalación, migraciones, pruebas, perfiles de rendimiento, arranque local y diagnósticos permitidos. No mandes al usuario comandos que puedes ejecutar tú.
6. Prepara y realiza el despliegue si el usuario proporcionó el destino, acceso y autorización. Si no existen, entrega el despliegue automatizado y marca el servidor como NO DESPLEGADO.
7. No pares por elecciones técnicas reversibles. Elige un valor seguro, documenta y continúa. Sí detente ante permisos denegados, costos no autorizados o acciones financieras no aprobadas.
8. Nunca solicites secretos por chat, los imprimas, los incluyas en Git o explores archivos/cuentas ajenos al proyecto para obtenerlos. Usa credenciales configuradas para este trabajo o un mecanismo seguro de ingreso.
9. Puedes preparar flujos de alta y completarlos cuando exista autorización explícita y el proveedor lo permita; no suplantes identidad, no evadas verificaciones y no aceptes declaraciones personales por tu cuenta. Detente exactamente en el paso humano y reanuda después.
10. No ejecutes operaciones reales para «probar» sin una aprobación específica que indique wallet, monto, modo, estrategias, vigencia y límites. El permiso de construir no es permiso de invertir.
11. No prometas que las pruebas de meses pueden completarse durante una sesión. Deja la observación funcionando y el avance protegido por evidencia acumulada.
12. Tu entrega final será breve: qué funciona, pruebas realizadas, estado local/VPS, acceso administrativo, bloqueos externos y acciones humanas imprescindibles. El detalle vive en los archivos.

### Definición del producto

EdgeHunter es software de investigación y ejecución cuantitativa para capital propio. Busca oportunidades medibles, determina si son operables y administra riesgo dentro de un mandato humano. También detecta catalizadores y anomalías en otros mercados y avisa cuando requieren acción manual. La aspiración es encontrar ventajas escalables y acumular capital; no existe promesa de ganancias ni de riesgo de ruina cero. Perder dinero, no encontrar edge o descubrir que no escala son resultados posibles y válidos.

Debe correr primero en la máquina del usuario y después 24/7 en un VPS, sin depender del harness ni de una suscripción de chat. Interfaz: administración nativa de PocketBase y correo. Sin Angular, dashboard propio, Kubernetes, agentes LLM permanentes ni conectores financieros innecesarios.

«Autónomo» significa operación diaria sin supervisión continua dentro de límites ya aprobados; no elimina renovación de accesos, cambios del proveedor, incidentes o decisiones humanas excepcionales.

## 1. Decisiones cerradas y alcance

| Área | Decisión |
|---|---|
| Lenguaje | Python async; versión compatible fijada con lockfile |
| Proceso | Un daemon modular; tareas supervisadas y workers de cálculo acotados |
| Trading inicial | Polymarket Predictions, spot/totalmente colateralizado; no perps, apalancamiento ni deuda |
| Radar | Acciones, cripto y mercados de predicción; nuevas categorías mediante fuentes registradas |
| Otros venues | Alerta/manual inicialmente; ninguna orden sin adaptador certificado y mandato separado |
| Administración | PocketBase dedicado, admin nativo, acceso remoto desde celular y laptop |
| Persistencia crítica | SQLite local dedicado al journal, ledger, reservas y outbox; distinto al SQLite de PocketBase |
| Proyección operativa | PocketBase asíncrono; configuración, solicitudes, estado y bitácora legible |
| Histórico pesado | Parquet + DuckDB; no millones de snapshots en PocketBase |
| Inteligencia | Matemáticas → especialistas → Jev → LLM económico → frontier excepcional |
| Correo | Brevo; transporte reemplazable; destinatario confirmado en configuración |
| Producción | systemd, Nginx/TLS, usuario dedicado, backups y monitor externo |
| Retiros | Manuales. Treasury recomienda; el daemon no implementa sweeper ni transferencias arbitrarias |
| Promociones | Democión automática; activación de capital real y ampliaciones requieren aprobación humana |

### Correcciones que tienen prioridad sobre el chat previo

- Sacar PocketBase del camino de ejecución NO significa operar sólo en RAM: el intento de orden y su reserva se escriben durablemente antes de enviar al venue.
- PocketBase puede caer sin romper la reconciliación, pero no indefinidamente sin afectar autorizaciones: al vencer el lease de control, se bloquea riesgo nuevo.
- Un score de Jev no es probabilidad de rentabilidad. La entropía binaria de un evento sólo depende de su `p`; no añade información independiente a ese mismo `p`. La dispersión meteorológica o el desacuerdo entre modelos sí pueden aportar otra señal, previa validación.
- «80% del modelo se vuelve 62%» no será una sustitución basada en pocas observaciones: exige calibración temporal fuera de muestra, intervalos y muestras suficientes.
- Un stop-loss no garantiza pérdida máxima si desaparece la liquidez. Los límites se aplican a pérdidas posibles, no solamente a stops esperados.
- Guardar ganancias como `locked` en una base no las protege de una wallet comprometida. Sólo su retiro a una wallet sin llaves accesibles al sistema las separa efectivamente.
- Prohibir retiros en código no restringe criptográficamente una private key propietaria. Elegir credenciales delegadas cuando estén disponibles; de lo contrario informar el riesgo y bloquear live hasta aprobación explícita de esa arquitectura.
- Construir todos los módulos no implica habilitarlos todos. Un módulo sin fuente, licencia, calibración o permisos válidos queda bloqueado; nunca produce oportunidades falsas para parecer completo.
- Los ejemplos GameStop/GoPro/NFT son escenarios de investigación, no evidencia de una estrategia. No incorporar como hechos sus fechas, retornos o narrativas del chat sin reconstrucción documental.

## 2. Entorno, descubrimiento y límites de autorización

El contexto aportado menciona un VPS Ubuntu con Nginx, PocketBase, WireGuard y varios proyectos existentes. Es referencia, no inventario vigente ni permiso de modificarlos. No reutilices puertos, unidades o credenciales sin comprobarlos. No escribas en los `pb_data` de otros productos ni actualices sus binarios.

El preflight debe detectar SO, arquitectura, Python, RAM, disco, puertos, reloj, herramientas disponibles, conectividad por proveedor, servicios existentes y estado del repositorio. Para escritorio soporta macOS Apple Silicon y Linux; Windows mediante WSL2, con detección y explicación de requisitos. No exige systemd para desarrollo. El launcher supervisa Python y PocketBase y libera sus procesos al salir.

Valores de instalación propuestos: `/srv/edgehunter`, `/var/lib/edgehunter`, `/etc/edgehunter`, unidades `edgehunter.service` y `edgehunter-pocketbase.service`. Elige puertos loopback libres; nunca asumas que 8090–8093 están disponibles. Dominio administrativo: dato pendiente que debe resolver el harness con el usuario si no está preautorizado. No crees registros DNS o cuentas de terceros fuera del alcance otorgado.

### Preflight financiero obligatorio

Verifica términos de automatización, elegibilidad del usuario, disponibilidad del producto concreto, permisos de datos y restricciones desde el host que realmente enviará órdenes. La comprobación geográfica debe hacerse tanto en local como en el VPS. Un endpoint accesible no acredita legalidad. Si existe restricción, ambigüedad o falta de autorización, continúa en research/shadow y reporta; no cambies IP, uses VPN/proxy o migres de país para evadirla. WireGuard se usa para administración privada, no para saltarse restricciones del venue. [Restricciones oficiales de Polymarket](https://docs.polymarket.com/api-reference/geoblock).

No administrar dinero de terceros. No automatizar manipulación, wash trading, spoofing, campañas sociales o acceso a información privada. El radar sólo observa fuentes obtenidas legítimamente. Preparar exportación contable; no afirmar cumplimiento fiscal automático.

## 3. Arquitectura, propiedad del estado e invariantes

| Componente | Responsabilidad | No puede hacer |
|---|---|---|
| Ingestion | Adquirir, validar y normalizar datos con procedencia y relojes | Convertir texto externo en instrucciones |
| Market state | Snapshot coherente, frescura, metadatos y exposición en memoria | Ser única copia de órdenes/mandatos |
| Strategy plugins | Proponer oportunidades y salidas justificadas | Enviar órdenes, aprobarse o cambiar límites |
| Intelligence | Extraer hechos/clasificaciones y producir estimaciones etiquetadas | Acceder a secretos, shell o herramientas financieras |
| Calibration | Versionar probabilidades, incertidumbre y validación | Publicar calibradores sin controles |
| Risk/portfolio | Reservar capital, controlar correlación/capacidad y aprobar planes técnicos | Superar mandato o aprobación humana |
| Execution | Enviar/cancelar planes ya autorizados, reconciliar | Generar capital o transferir a direcciones externas |
| Journal/ledger | Intentos, reservas, fills, mandatos, cashflows y outbox durables | Ser editable desde la UI administrativa |
| PocketBase | Solicitudes humanas, configuración propuesta y proyecciones | Ser autoridad del PnL o dar permisos fuera del hard envelope |
| Notifications | Correo deduplicado, entregas y escalamiento | Interpretar una respuesta de correo como aprobación |
| Research | Backtests, calibración candidata, capacidad y reportes | Modificar producción o promoverse a live |

Flujo de orden: datos válidos → oportunidad → plan y riesgo → transacción local que reserva capital y registra intención → envío → confirmación/reconciliación → ledger → proyección asíncrona y métricas. Un solo escritor decide sobre el mismo presupuesto; dos estrategias concurrentes no pueden gastar el mismo saldo.

### Invariantes no negociables

1. Ninguna orden real sin mandato vigente, estrategia habilitada, control fresco, datos válidos, saldo reconciliado y reserva durable.
2. Nunca se asume que una orden falló sólo porque expiró una petición HTTP.
3. Una aprobación no autoriza otro activo, mayor tamaño, precio peor, estrategia/version distinta ni fecha posterior.
4. Una aprobación aceptada no resetea contadores ni autoriza reutilizar capital consumido fuera de su alcance.
5. El LLM, el feed y la cuenta de servicio de PocketBase no pueden emitir aprobación humana.
6. Un reinicio o rollback no reactiva un kill switch ni borra pérdidas, reservas o nonces consumidos.
7. Si no se puede reconstruir la exposición, no se abre riesgo nuevo.
8. Salidas y cancelaciones pasan sus propios controles; una operación etiquetada `reduce_only` debe reducir realmente el riesgo bajo escenarios admitidos.
9. Sólo una instancia ejecutora puede operar una wallet. Una migración local→VPS nunca deja dos escritoras activas.
10. Sin fuente verificable, precio ejecutable o datos de resolución suficientes, el resultado puede ser ABSTAIN.

## 4. Estructura obligatoria del repositorio

| Ruta | Contenido |
|---|---|
| `pyproject.toml`, lockfile | Dependencias fijadas, grupos runtime/dev/weather y CLI |
| `src/edgehunter/domain/` | Tipos, unidades, estados, validadores e invariantes |
| `src/edgehunter/ingestion/` | Registry de fuentes, rate limits, normalización, relojes |
| `src/edgehunter/venues/polymarket/` | API/WS, firmas, metadatos, restricciones y reconciliación |
| `src/edgehunter/strategies/` | Todos los plugins de la sección 9 |
| `src/edgehunter/intelligence/` | Jev, LLM, especialistas, presupuestos y provenance |
| `src/edgehunter/research/` | Replay, calibración, walk-forward, hipótesis y capacidad |
| `src/edgehunter/risk/` | Hard envelope, exposición, sizing, circuit breakers |
| `src/edgehunter/execution/` | Planes, reservas, máquina de órdenes, paper/live |
| `src/edgehunter/storage/` | Journal SQLite, migraciones, ledger, Parquet y PB outbox |
| `src/edgehunter/control/` | Instrucciones/aprobaciones, leases y configuración versionada |
| `src/edgehunter/treasury/` | Asignación de estrategias y recomendaciones de tesorería |
| `src/edgehunter/notifications/` | Brevo, plantillas, deduplicación, delivery y alertas |
| `src/edgehunter/ops/` | Salud, CLI, backups, incidentes y mantenimiento |
| `pocketbase/pb_migrations/`, `pocketbase/pb_hooks/` | Esquema reproducible, reglas y flujo de aprobación |
| `config/` | Perfiles seguros, schemas, política y fuentes sin secretos |
| `tests/unit/`, `integration/`, `contract/`, `chaos/`, `e2e/` | Pruebas deterministas y externas separadas |
| `tests/fixtures/` | Datos sintéticos identificados y capturas lícitas redactadas |
| `deploy/` | Nginx, systemd, firewall incremental, instalación/rollback |
| `scripts/` | Bootstrap, doctor, demo, backup, restore, smoke y migración |
| `docs/` | Operación, seguridad, proveedores, estrategia, limitaciones |
| `IMPLEMENTATION_STATUS.md` | Matriz requisito→implementación→test→evidencia |
| `HUMAN_ACTIONS.md` | Sólo impedimentos humanos/exteriores pendientes |
| `ACCEPTANCE_REPORT.md` | Evidencia de entrega y gates de habilitación |

Usa typing estricto, schemas versionados, validación en fronteras, UTC en almacenamiento y `America/Mexico_City` para presentación configurable. Dinero/precios/tamaños se representan con enteros de unidades mínimas o Decimal; floats sólo en estadística con conversiones explícitas. Conserva moneda, token, chain y precisión: MXN, USD de reporte y collateral del venue no son intercambiables.

## 5. Modos y políticas de arranque

| Modo | Red de mercado | Fondos | Resultado |
|---|---|---|---|
| `REPLAY` | No, fixtures/histórico congelado | Simulados | Reproducibilidad y pruebas causales |
| `SHADOW` | Lectura real | Ninguno | Señales y decisiones hipotéticas; ninguna orden |
| `PAPER` | Lectura real | Ledger simulado | Órdenes/fills simulados con modelo explícito |
| `MICRO_LIVE` | Real | Mandato pequeño aprobado | Validación de ejecución y costos reales |
| `LIVE` | Real | Mandato específico aprobado | Estrategias habilitadas con límites |

El modo global y el modo permitido por estrategia se intersectan: elevar el primero no promueve a todas. Distinguir ciclo de estrategia `IMPLEMENTED / DATA_BLOCKED / RESEARCH / SHADOW / PAPER / MICRO_LIVE / LIVE / QUARANTINED / DISABLED`.

Primer arranque: REPLAY demo o SHADOW si existe conectividad pública; `trading_enabled=false`. Credenciales de lectura no habilitan firmas. Un binario invocado en shadow no obtiene el signer ni puede cancelar órdenes reales. Tests nunca cargan credenciales live salvo una suite expresamente autorizada.

Cada arranque live comienza en `RECONCILING`. Sólo pasa a `READY` al comprobar journal íntegro, mandato, reloj, identidad de cuenta, wallet/chain, órdenes, fills recientes, saldos, posiciones y frescura. Si estaba detenido por riesgo, sigue detenido. No basta con `systemctl is-active` para declarar listo.

## 6. Datos, fuentes y relojes

Cada evento incluye: `event_id`, `schema_version`, `source_id`, `source_record_id`, `source_url`, `content_hash`, `event_time`, `source_publish_time`, `first_seen_at`, `ingested_at`, `processed_at`, `available_at`, calidad temporal y versión del proveedor. Tiempos desconocidos se guardan como desconocidos; no se inventa precisión. Para latencias usa reloj monotónico. Un backtest sólo puede usar lo conocido en su `available_at` histórico, incluidas revisiones y latencia de publicación.

Cada fuente registra: acceso/licencia, cobertura, frecuencia, retraso conocido, términos de retención, autenticación requerida, precio, cuota, freshness TTL, fallback, estado `HEALTHY / DEGRADED / STALE / BLOCKED` y último error. `STALE` bloquea las decisiones que dependen de esa fuente; no congela investigación independiente.

### Fuentes a implementar

- Polymarket: catálogo/eventos, contratos y cambios, orderbook/WS, trades, órdenes/fills de la cuenta, posiciones, resolución y parámetros de trading. REST para bootstrap y reparación, WS para cambios; paginación y checkpoints.
- Noticias: RSS/Atom y endpoints primarios configurables, comunicados de relaciones con inversionistas y filings SEC donde aplique. Deduplicar sindicación para no contar veinte copias como veinte evidencias. Respetar identificación del cliente, cuotas y términos de cada proveedor.
- Cotizaciones de acciones/cripto: al menos un adaptador real por clase con permisos disponibles. Cobertura, horario de negociación y delay explícitos. Si no hay feed legítimo de precio/volumen, puede emitirse noticia, pero no un «edge vigente» calculado con precios inventados.
- Weather: WeatherNext con extracción acotada y observaciones oficiales compatibles con el mercado. Históricos de pronósticos emitidos, no sólo clima observado después.
- Social/derivados/short interest/wallet intelligence: adaptadores concretos cuando exista acceso; diferenciar métricas actuales de informes demorados. No suponer Reddit/X/opciones gratis o en tiempo real.

Implementa `source probe`, reporte de cobertura y fixtures. No completes métricas faltantes con cero: usa `null + reason`, reduce confianza o abstente. El radar no vigila literalmente todo internet: informa el universo cubierto y sus huecos.

### Libro de órdenes y recursos

Aplicar snapshots/deltas según garantías reales del protocolo; detectar secuencias perdidas cuando existan, cambios de tick, cierre de mercado y reconexión. Al desconectar invalida el estado; vuelve a sincronizar antes de operar. Un ping del socket no demuestra frescura de un libro: comprobar transport, suscripción y resync periódico según mercado. Escanear catálogo amplio a baja frecuencia y suscribir profundidad sólo para universo activo/priorizado; no descargar todo continuamente.

Colas acotadas y backpressure. Se pueden compactar ticks reemplazables; nunca descartar silenciosamente fills, órdenes, aprobación o alertas críticas. Toda pérdida de datos de investigación se marca en el histórico. Una tarea pesada de weather/backtest no bloquea heartbeats ni ejecución.

## 7. Contratos de dominio y plugins

Implementa al menos estos modelos tipados, serializables y versionados:

| Modelo | Campos esenciales |
|---|---|
| `MarketContract` | venue, event/market/token IDs, outcomes, payoff, reglas/texto/hash, fuente de resolución, zona/fecha/cierre, ticks, mínimos, fees, collateral, flags y versión |
| `Evidence` | evento/fuente, hash, extracto, URL, relojes, verificación, contradicciones |
| `Forecast` | target preciso, horizonte, `p_raw`, `p_calibrated`, intervalo, modelo/calibrador, provenance, estado de validación |
| `Opportunity` | id, estrategia/version, target, hipótesis, evidencia, estimaciones, vigencia, capacidad, cluster, `AUTO/MANUAL/UNAVAILABLE`, motivo de abstención |
| `ExecutionPlan` | hash canónico, wallet/venue, legs, lados, size, límites de precio/costo, max_loss, expiración, exits, mandatos y reglas hash |
| `RiskDecision` | ALLOW/REJECT/REDUCE, razones codificadas, reservas, exposición antes/después, política/version |
| `OrderIntent` | intent_id único, plan/approval_id, payload/hash estable, timestamps, state, provider_order_id, nonce/salt si aplica |
| `Fill` | venue/account/trade_id, leg/index cuando aplique, qty, precio, fees, estado de settlement, revisiones |
| `ApprovalRequest` | tipo, payload/hash, límites, expiración, revisión, actor/decisión, estado de ejecución y audit IDs |
| `StrategyEvaluation` | período, dataset, hipótesis, split, costos, intervalos, capacidad, leakage checks, gates |

Interfaz orientativa: `on_event(event, state) -> Sequence[Opportunity]`, `evaluate_position(position, state) -> ExitProposal | None`, `health() -> StrategyHealth`. Los plugins usan contexto de sólo lectura y no reciben clientes autenticados de trading. El core convierte propuestas en planes ejecutables y verifica todo otra vez.

El registry declara inputs, TTL, universo, costos, supuestos, capacidad soportada, estado de calibración y modos admitidos. Falta de dependencia produce `DATA_BLOCKED`, no un import que tumbe el daemon completo. Un fallo de riesgo/ledger, en cambio, sí bloquea operaciones nuevas globalmente.

## 8. Inteligencia y calibración

Jev será un adaptador real con timeout, schema estricto, modelo fijado, cache por entrada/versión, límite de concurrencia, circuit breaker y contabilidad de uso. La documentación actual ofrece `typesafe-sdk`, respuestas tipadas y primitivas Choice/Score/Noul; confirmar la versión antes de instalar. No asumir precio «casi gratis» ni latencia fija. [Quickstart oficial de TypeSafe](https://docs.typesafe.ai/introduction/quickstart).

Separar estrictamente `relevance_score`, `source_confidence`, `regime_score`, `p_event` y `expected_return`: son objetos distintos. Un 0.9 de relevancia no equivale a 90% de ganar. Jev/LLM pueden ayudar a interpretar contratos y noticias, pero los cálculos monetarios y validaciones numéricas se hacen en código; el propio proveedor advierte limitaciones de precisión numérica. [Limitaciones de Jev](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

Los proveedores LLM se conectan mediante una interfaz intercambiable con salida estructurada. El frontier se usa sólo si el valor esperado de información y el presupuesto justifican escalar; una oportunidad de centavos no dispara investigación costosa. No es obligatorio llamar a todos los niveles. No enviar PII, secretos o balances detallados a un proveedor si no hacen falta.

### Seguridad de contenido y decisiones

Texto externo es dato no confiable, nunca una instrucción de sistema. No ejecutar código, abrir tools arbitrarias ni seguir URLs sugeridas por el texto sin validación. Extractores con límites de tamaño/tiempo, bloqueo de SSRF (loopback, redes privadas, metadata endpoints, redirecciones y DNS revalidado), sanitización y allowlists pertinentes. Renderizar correos escapando HTML externo.

El modelo no cambia config, aprueba solicitudes, firma órdenes ni decide destinatarios. JSON inválido, campos inesperados, timeout o evidencia insuficiente → abstención/degradación. Cachear no puede prolongar una señal vencida.

### Calibración y aprendizaje

Guardar predicción antes del resultado, target exacto, horizonte, método y versión. Medir Brier, log-loss, curvas de confiabilidad, cobertura de intervalos y skill frente a base rate/mercado según tarea. Calibradores por dominio/horizonte cuando la muestra lo permita; regularización y pooling documentados cuando no. No multiplicar probabilidades de señales correlacionadas ni promediar fuentes como si fueran independientes.

Walk-forward temporal, purga/embargo para labels solapados y holdout no reutilizado. Registrar todas las hipótesis probadas y corregir selección múltiple. Bootstrap por clusters/eventos/días, no por miles de ticks dependientes. Datos insuficientes → `UNPROVEN` y sin promoción automática. La mejora del Brier por sí sola no demuestra PnL positivo.

Research puede entrenar calibradores candidatos y proponer parámetros, nunca generar/desplegar código en caliente ni relajar riesgo. Un candidato corre champion/challenger en shadow; versión nueva necesita gates y aprobación para producción. Aprendizaje continuo no significa auto-modificación sin control.

## 9. Estrategias que deben quedar implementadas

Cada plugin entrega lógica real, fuentes, pruebas, modelo de costos, salida y motivos de no operar. No basta un archivo que devuelva `[]` permanentemente. Las dependencias externas bloqueadas se reportan aparte de la calidad del código.

### 9.1 Arbitraje estructural y multi-leg

Buscar payoffs cuya cobertura pueda demostrarse mediante contratos, no parecido textual. Para un conjunto binario completo y realmente complementario, comparar costo ejecutable total y costos de conversión/settlement contra payout garantizado según contrato. Para múltiples outcomes verificar exhaustividad, exclusión y tratamiento de invalidación/«otro». Una tabla de payoff por escenario debe demostrar la cobertura.

Clasificar `STRUCTURAL_ATOMIC`, `STRUCTURAL_NONATOMIC` o `RELATIVE_VALUE`. Sólo el primero puede eliminar riesgo direccional de legs bajo sus supuestos; persisten riesgos de venue, contrato, collateral y operación. Batch y FOK por leg no implican atomicidad del conjunto. Antes de cada operación establecer secuencia, tiempo máximo descubierto, reserva de cobertura y pérdida máxima de unwind. Si no existe camino aceptable, abstenerse.

### 9.2 Market making

Implementar cotización limitada por inventario, post-only cuando el venue lo soporte, edge tras adverse selection, TTL de quotes, cancelación/repricing sin churn excesivo y protección ante datos viejos. Estimar markout en varios horizontes y probabilidad de fill. Rebates sólo se reconocen al confirmarse; no hacen rentable por decreto un spread negativo. No habilitar live usando únicamente paper de toque de precio.

### 9.3 Wallet intelligence

Reconstruir cashflows, posiciones abiertas/cerradas, transfers, mints/redemptions y trading cuando el proveedor permita distinguirlos. Dirección no equivale a persona ni a una estrategia; no atribuir identidad. PnL de una wallet puede omitir coberturas externas.

Seleccionar candidatos con datos hasta `t`, seguir desde `t+1`; registrar candidatos descartados y wallets desaparecidas. Modelar retraso de detección, profundidad al copiar, entrada/salida diferente, concentración y correlación. Nunca copiar sólo porque el histórico se ve bueno. Evidencia insuficiente → señal auxiliar o alerta, no compra automática.

### 9.4 Weather

Traducir exactamente el target del contrato: estación, coordenadas, temperatura máxima/mínima/promedio, unidad, redondeo, período local, fuente y hora oficial de publicación. Considerar interpolación, elevación, zona horaria y error de representatividad. Si el contrato liquida con una estación concreta, una celda global no es observación equivalente.

Consultar sólo variables/regiones/horizontes necesarios. WeatherNext documenta ensembles de 64 miembros y sesgos/limitaciones; registrar la cantidad realmente recibida, miembros faltantes, dependencia entre miembros y versión del run. `k/n` es un estimador crudo, no probabilidad calibrada. No tratar los miembros como 64 ensayos independientes para inventar intervalos estrechos. [Limitaciones de WeatherNext](https://developers.google.com/weathernext/guides/benefits-limitations).

Guardar inicialización y disponibilidad real: publicación horaria no significa inmediatez. La documentación actual muestra retrasos alrededor de siete horas según superficie/run; validar el calendario y detectar entregas tardías. No codificar una latencia universal fija. Para muy corto plazo, combinar observaciones/avisos oficiales cuya disponibilidad sea compatible con el contrato. [Calendario de publicación](https://developers.google.com/weathernext/guides/dissemination).

Implementar reducción regional, presupuesto máximo de bytes/query/egress, caché y backfill acotado. Verificar permisos y facturación antes de consultar recursos costosos. Si no hay acceso, el adaptador queda implementado con fixtures y `ACCESS_BLOCKED`, sin fingir operación live. [Acceso oficial](https://developers.google.com/weathernext/guides/access-forecast).

### 9.5 News latency y cross-market

Resolver entidades por IDs, no sólo ticker/nombre; identificar evento nuevo, ampliación, rumor, desmentido o noticia reciclada. Una publicación oficial reduce incertidumbre factual, no garantiza buena inversión. Guardar catalizador, condiciones pendientes, dilución/deuda/financiación cuando correspondan y causas alternativas.

Medir reacción desde la publicación y desde el primer dato disponible. No existe una observación directa de «porcentaje ya descontado»: `priced_in_assessment` será una estimación con método e incertidumbre, no un hecho. Sin timestamps o cotización oportuna, limitarse a alerta informativa.

Cross-market: construir relaciones lógicas o económicas explicadas, comprobar reglas de resolución, períodos y escenarios. No llamar arbitraje a una correlación histórica. Un desajuste puede ser racional por fuentes de settlement, riesgo o liquidez diferentes.

### 9.6 Forecasting

Baselines explícitos por dominio, modelo estadístico/especialista y, sólo donde aporte, estimación de modelos generales. Debe poder perder frente a la probabilidad de mercado y quedar desactivado. No contar el precio de mercado como feature independiente y luego presentar su eco como ventaja informativa.

### 9.7 Opportunity Radar

Detectar anomalías conjuntas de catalizador, actividad, volumen, liquidez, atención, precio y posicionamiento; ventanas adaptadas al activo y horario. Ajustar baselines por estacionalidad, splits/corporate actions, listado reciente y cobertura del feed. Distinguir anomalía estadística de oportunidad operable.

Priorizar tres horizontes: minutos/horas, días/semanas y estructural. Correo/manual no sirve para prometer capturar oportunidades de milisegundos. Una alerta indica su ventana y puede informar «llegamos tarde» o «no hay acceso de ejecución».

Cada caso incluye: qué cambió, evidencia primaria/contradictoria, timestamps, precio actual con delay, movimiento observado, hipótesis causal, escenarios, invalidación, liquidez/salida, posibilidad de pérdida total, costos, acceso, capacidad y datos faltantes. Upside 5x/10x es escenario, no predicción; un score 92/100 no es 92% de éxito. Si no puede estimarse un edge fiable, `expected_edge=null` y se presenta una hipótesis de investigación.

Detectar señales de manipulación, wash volume, concentración, honeypots, restricciones de venta o mercados no verificables; no conectar wallets ni comprar contratos nuevos automáticamente. «Nueva categoría» abre un expediente manual, no instala un adaptador ni concede permisos.

## 10. Edge, riesgo, capacidad y capital

### Precios y expectativas

Para comprar `q` shares y mantener a resolución, usar `EV = q × E[payout_por_share] − costo_ejecutable(q) − costos_adicionales`. El costo ejecutable usa el ask y la profundidad correspondiente, no midpoint. En estrategias de salida anticipada se necesita distribución del precio de salida, costos y disponibilidad de liquidez; `p_event` no basta.

Registrar unidades: edge por share, puntos de probabilidad y ROI sobre costo son cosas distintas. El spread ya incluido al recorrer el libro no se resta por segunda vez. Añadir latencia/adverse selection como estimación separada. Fees, mínimos, precisiones y tick provienen de metadatos vigentes; verificar cambios antes de enviar. [Fees oficiales](https://docs.polymarket.com/trading/fees) y [metadatos de mercados](https://docs.polymarket.com/api-reference/markets/list-markets).

Decisión sobre edge conservador/intervalo, con reserva por incertidumbre, costos desconocidos y calidad de resolución. Si se desconoce un costo material, no usar cero por defecto.

### Tres capas de autorización

1. **Hard envelope del host**: límites absolutos y capacidades en configuración protegida, no editable por el daemon ni por PocketBase. Incluye modo máximo, wallet/chain, capital máximo, venues, no apalancamiento, no transferencias, pérdida máxima y costos.
2. **Mandato humano vigente**: qué estrategias/versiones pueden operar, cuánto, durante qué período, con qué límites más restrictivos y salidas autorizadas.
3. **Risk engine por plan**: comprueba contexto actual, correlación, capacidad, reservas, elegibilidad y precio. Puede denegar una operación humana aprobada que ya no es válida.

La configuración efectiva siempre es la intersección más restrictiva. PocketBase puede pedir cambios dentro del envelope; no ampliarlo. Ampliarlo requiere mantenimiento autorizado del host y nuevo mandato. Un depósito no aumenta automáticamente el presupuesto autorizado. Ningún campo `force=true` salta controles.

### Perfil inicial de propuesta, no autorización

Todos estos números son defaults operativos conservadores para discutir/validar, no parámetros rentables demostrados. Se muestran en la primera solicitud; live sigue apagado hasta aprobarla.

| Parámetro | Propuesta inicial |
|---|---|
| Capital real de prueba | Hasta $1,500 MXN de referencia; convertir a collateral con FX fechado y aprobar la cantidad exacta |
| Pérdida potencial por idea | Máximo 1% del presupuesto autorizado y un tope absoluto |
| Pérdida potencial por cluster causal | Máximo 3% |
| Pérdida potencial agregada | Máximo 10%, contando posiciones y órdenes pendientes |
| Pérdida diaria | 2% del capital autorizado al inicio del día; se enclava el halt |
| Drawdown | 5% desde high-water mark ajustado por cashflows; halt y revisión |
| Kelly | Desactivado hasta calibración; después como máximo 0.25 de Kelly estimado y siempre subordinado a caps |
| Leverage, naked shorts, préstamos | Prohibidos |
| Capital externo | Sólo sugerencia; sin acceso a la reserva |
| Oportunidad excepcional | Nunca salta límites ni habilita all-in |

Si el mínimo del venue excede un límite, no operar: informar incompatibilidad, no redondear hacia arriba. Un bankroll pequeño puede impedir por completo ciertas estrategias. Comisiones de bridge/gas/datos pueden hacer antieconómica la prueba; demostrarlo antes de pedir dinero.

Los porcentajes de pérdida diaria/drawdown son umbrales de intervención, no garantías de pérdida máxima realizada: gaps, settlement y falta de liquidez pueden superarlos. Definir día contable en la zona configurada, guardar su inicio/fin UTC y su capital de referencia; cambiar la hora, depositar o reiniciar no resetea un breach. Separar drawdown de trading y gastos operativos y vigilar ambos con límites propios.

### Exposure graph y reservas

Clusters por evento, causalidad, activo, región, fuente/modelo, fecha de resolución y collateral/venue. Estresar pérdidas conjuntas incluyendo fallo del venue/collateral; no usar únicamente correlación histórica normal. Para reducir exposición por coberturas debe existir una tabla de payoff verificable y considerar legs no ejecutadas y cierre parcial. Sin prueba, sumar conservadoramente pérdidas potenciales.

Reservar capital para órdenes enviadas, pendientes y de estado desconocido; liberar sólo con confirmación. Ventas parciales, fees en shares/tokens y acciones manuales del dueño actualizan ledger. Una operación externa inesperada pausa nuevas aperturas hasta reconciliar y clasificarla.

### Capacidad y rendimiento

Estimar curva de size→precio/impacto/fill/edge neto y salida, por estrategia y mercado. Profundidad visible no garantiza liquidez futura. Evaluar capacidad con latencia y escenarios; no extrapolar rendimientos de $50 a $50,000.

Medir PnL neto, drawdown, turnover, capital bloqueado, costo de oportunidad, retorno por capital-tiempo y capacidad. No anualizar unos días como promesa. Reportar concentración y estrés; estimaciones de riesgo de ruina son dependientes del modelo y jamás garantizan `≈0`.

### Treasury y portfolio allocators

Distribuir sólo el capital ya autorizado entre estrategias aprobadas, respetando capacidad/correlación. Cambios de pesos dentro de rangos aprobados pueden automatizarse; activar estrategia nueva o ampliar rango requiere solicitud. La evidencia se corrige por selección y costos.

Treasury propone `HOLD / REINVEST / WITHDRAW / ADD_EXTERNAL_CAPITAL`, con saldo libre liquidado, reservas, impuestos/comisiones estimados cuando se conozcan, justificación y caducidad. No sugerir inyecciones sólo para recuperar pérdidas. Separar capital aportado, PnL, disponible, collateral bloqueado y ganancias retiradas. `deployable_reserve` es un dato informativo, no un saldo accesible.

Ganancias designadas para retiro se excluyen del presupuesto; siguen físicamente expuestas hasta que el usuario retire. No marcar un retiro como hecho porque se aprobó: esperar transacción/conciliación. El sistema no necesita el secreto de la wallet de ahorro.

## 11. Ejecución, journal y recuperación

### Integración Polymarket

Seleccionar SDK oficial compatible tras contrastar documentación, release y paquete. Al preparar este runbook la documentación apunta a `polymarket-client` y cliente async; no asumir que un ejemplo antiguo de `py-clob-client` sigue siendo la ruta adecuada. Fijar versión, hacer contract tests y documentar incompatibilidades. [Python SDK oficial](https://docs.polymarket.com/getting-started/python).

Resolver tipos de wallet, signer, funding account, permisos, chain, dominios de firma y collateral a partir de documentación/metadata verificada. La documentación actual identifica pUSD como collateral; no asumir USDC directo ni copiar direcciones de contratos del chat. Registrar asset ID, dirección validada, decimals y cadena. Ante inconsistencias entre docs, respuestas o SDK, bloquear live hasta aclararlas. [Collateral oficial](https://docs.polymarket.com/concepts/pusd).

Límites de precio para todas las entradas; no órdenes de mercado sin cota. Respetar order types y expiraciones soportadas. Nunca tratar una respuesta aceptada como fill o settlement confirmado. Usar canal de usuario y REST de reconciliación; posiciones analíticas pueden ir retrasadas y no ser fuente suficiente de riesgo intradía.

### Máquina de órdenes

Estados internos: `PREPARED → SUBMITTING → ACKNOWLEDGED → OPEN/PARTIALLY_FILLED/FILLED`; ramas `REJECTED`, `CANCEL_PENDING`, `CANCELLED`, `EXPIRED`, `UNKNOWN`. `CANCELLED` puede conservar fills previos. Fill y settlement tienen estados separados; un match no siempre es liquidación final.

Guardar en transacción local plan, mandato, reserva, intent ID, payload/hash y datos de firma persistibles antes del envío. Material secreto no se guarda en el journal. Cuando el protocolo lo permita, persistir identificador/hash determinista de orden y reutilizar el mismo intento; no regenerar salt/nonce al reintentar ciegamente.

No asumir soporte universal de `client_order_id` o `Idempotency-Key`. Si existe, probar su semántica. Si no, usar identidad propia, payload estable, reconciliación por identificadores del venue y serialización. No se promete exactly-once en una red incierta: la política es no duplicar exposición y bloquear ante incertidumbre.

Timeout tras envío → `UNKNOWN`, reserva retenida, consulta/reconciliación. Si no puede determinarse el resultado, no repetir la orden; abrir incidente. Eventos duplicados y fuera de orden deben ser idempotentes mediante claves únicas de negocio y revisiones. WebSocket reconectado no implica que hayan llegado todos los fills.

### Persistencia y crash consistency

SQLite local en WAL, transacciones y política de sync durable documentada. Procesamiento financiero con un escritor y consultas sin bloquearlo. Cada evento de negocio y outbox se confirman juntos. El almacenamiento está en disco local, no filesystem de red; probar corte del proceso en cada punto crítico. Si el disco está lleno o el journal no puede confirmar, detener riesgo nuevo y usar mecanismos de cancelación seguros disponibles.

El sync a PocketBase usa IDs únicos, retries con backoff, cursor durable y dead-letter para errores persistentes. No eliminar un evento hasta confirmación de proyección. La UI muestra `as_of` y lag; un usuario no debe confundir datos atrasados con saldo actual.

### Reconciliación

Al inicio y periódicamente: identidad de wallet, balances, allowances relevantes, órdenes abiertas, fills desde checkpoint con overlap, posiciones, settlement/redemptions y depósitos/retiros. Comparar ledger con venue/onchain según autoridad y retraso. Diferencias por lag se etiquetan; diferencias inexplicadas bloquean nuevas entradas. Registrar ajustes contables explícitos, no sobrescribir históricos para hacerlos coincidir.

Redemption/merge/split no son retiro a otra wallet, pero sí operaciones onchain con costos/permisos. Implementar detección y workflow: automatizables sólo con permisos compatibles y mandato expreso; si no, correo de acción humana. No entregar PnL realizado que ignore fondos pendientes de reclamar.

### Cancelación y parada

Usar cancel-on-disconnect/heartbeat del venue cuando esté soportado y probado; registrar condiciones y alcance sobre la wallet. La documentación actual describe heartbeats de órdenes. No asumir que el monitor externo los sustituye. [Gestión de órdenes](https://docs.polymarket.com/trading/manage-orders).

`PAUSE_NEW_RISK`: no entradas; mantiene reconciliación y salidas previamente permitidas. `CANCEL_OPEN`: cancela órdenes abiertas de EdgeHunter, comprueba confirmación y fills concurrentes. `EMERGENCY_HALT`: enclavado; no apertura ni reanudación automática, cancelación segura y alerta. `FLATTEN`: acción aparte con límite de precio/costo, no liquidar a cualquier precio. Un halt no elimina posiciones existentes.

Si se pierde totalmente red/VPS, no es posible garantizar cancelación desde ese host. Mitigar con expiraciones del venue, heartbeat, límites y wallet dedicada; documentar riesgo residual. Nunca afirmar «kill switch instantáneo» cuando depende de red.

## 12. PocketBase: esquema, permisos y control

Instancia dedicada y migraciones idempotentes, seed seguro y datos demo separados. Los IDs de negocio deben ser campos propios con índices únicos; no depender de que PocketBase acepte cualquier UUID como ID interno. Importes exactos como cadenas decimales validadas o unidades enteras que no excedan precisión segura; nunca redondear dinero por comodidad de UI.

| Colección | Campos mínimos / propósito |
|---|---|
| `service_accounts` (auth) | identidad del daemon, rol fijo, revocación; sin acceso superuser |
| `config_requests` | patch propuesto, base_revision, hash, motivo, decisión humana y resultado |
| `effective_config` | revisión aplicada, límites efectivos, hard-envelope hash, as_of; sólo proyección |
| `commands` | PAUSE/CANCEL/HALT/RESUME, scope, nonce, caducidad, decisión y resultado |
| `approval_requests` | tipo, resumen, payload inmutable/hash, revisión, monto/moneda, plazo, actor y estados |
| `approval_audit` | cambios humanos capturados por servidor, before/after, timestamp y request ID |
| `mandates` | autorización vigente, wallet, estrategias/versiones, caps, períodos y consumo; proyección |
| `markets` | IDs, contrato/hash, resolución, estado y as_of |
| `sources` | cobertura, calidad, frescura, delay, permisos y estado |
| `opportunities` | estrategia, evidencia, score/forecast tipado, capacidad, vigencia y modo |
| `decisions` | evaluación de riesgo, rechazo/aprobación técnica, política, plan hash |
| `orders`, `fills`, `positions` | proyección del journal/venue, estados y freshness |
| `strategies`, `strategy_stats` | versión, lifecycle, gates, desempeño y drift |
| `model_calls` | proveedor/modelo, tarea, hashes, latencia, uso/costo y resultado redactado |
| `wallet_signals` | observación pública, método, delay y evaluación prospectiva |
| `daily_pnl`, `cashflows` | PnL/fees/costos, depósitos/retiros separados, MXN/FX y as_of |
| `treasury_recommendations` | propuesta, supuestos, límites, caducidad y acción requerida |
| `alerts` | severity, dedup key, entrega, acknowledged, resolved |
| `health`, `incidents` | heartbeat real, degradaciones, reconciliación y acciones |

Todas las colecciones privadas por defecto; reglas explícitas mínimas. El daemon no crea usuarios humanos ni altera roles, esquemas, hooks o credenciales. Sólo puede crear propuestas y escribir proyecciones/resultados permitidos. No puede escribir `human_decision`, `decided_by`, `decided_at` ni auditoría de aprobación.

El admin nativo opera con superusers, que omiten collection API rules. Por eso no basta una regla de colección para proteger campos contra ese admin: implementar validación de transición en hooks server-side y verificación independiente en el daemon. Para la operación ordinaria, sólo una petición autenticada del superuser humano designado puede aprobar; los metadatos de actor se obtienen del servidor, nunca del body. [Autenticación y privilegios de PocketBase](https://pocketbase.io/docs/authentication/).

No prometer inmutabilidad absoluta frente al propietario superuser o root: pueden modificar el sistema. La barrera relevante es impedir autoaprobación del daemon/modelo y mantener el hard envelope fuera de PocketBase. Si se compromete el control plane, el daño debe quedar acotado por el envelope y permisos de wallet.

### Flujo exacto de aprobación por campos

Separar `human_decision=PENDING|APPROVED|REJECTED` de `execution_status=WAITING|VALIDATING|ACCEPTED|EXECUTING|APPLIED|EXPIRED|INVALIDATED|FAILED|UNKNOWN`. La UI puede mostrar etiquetas en español. Así el daemon actualiza «en ejecución/en vivo» sin tener permiso de aprobar.

1. El daemon persiste la propuesta canónica en su journal y crea su espejo PB con `PENDING/WAITING`.
2. El correo incluye resumen de riesgo y enlace al registro; nunca ejecuta una acción al abrirlo.
3. El usuario autentica desde celular/laptop y cambia únicamente `human_decision` a `APPROVED` o `REJECTED`.
4. Hook server-side verifica actor y transición, bloquea edición de payload/importe/hash, estampa metadatos y agrega auditoría. Si el usuario quiere otro monto, crea una revisión nueva; no altera la propuesta firmada lógicamente.
5. El daemon recibe realtime como aviso y verifica por lectura autenticada. Polling durable con solapamiento recupera eventos perdidos. El mensaje realtime solo no es autoridad.
6. En transacción local verifica ID, nonce no consumido, hash contra su propuesta original, revisión, TTL, wallet, políticas y evidencia; consume una sola vez la aprobación y crea intención/mandato.
7. Reevalúa datos, precio, liquidez, saldo, exposición y elegibilidad antes de enviar. Si cambió materialmente, `EXPIRED/INVALIDATED` y nueva solicitud; nunca ejecución tardía por optimismo.
8. Proyecta resultado. `APPLIED` significa aplicado y comprobado; no solamente leído. En trading, distinguir autorización aplicada, orden abierta y fill.

Implementar CAS/revisión atómica para la transición y pruebas con doble clic y consumidores concurrentes. El journal tiene índice único para aprobación consumida. `APPROVED` repetido no produce otra operación; restaurar un backup PB tampoco.

### Tipos de aprobación

- `ACTIVATE_MANDATE`: wallet, estrategias/versiones, presupuesto total, caps, vigencia y política de continuidad tras reinicio.
- `ONE_SHOT_TRADE`: activo/lado, cantidad máxima, worst price, costo total, max_loss, fecha y plan hash; no permite aumentar size.
- `CONFIG_CHANGE`: patch tipado, base_revision y límites permitidos. Cambio de modelo/estrategia invalidará autorizaciones dependientes cuando corresponda.
- `PROMOTE_STRATEGY`: evidencia/gates y nueva etapa; no cambia otras estrategias.
- `RESUME_AFTER_HALT`: exige causas resueltas y revisión; no borra contabilidad ni high-water mark.
- `MANUAL_DEPOSIT/WITHDRAWAL`: registra decisión/recomendación; no ejecuta transferencia. Confirmación posterior por reconciliación.

Un mandato permite trading cotidiano sin correos por cada orden; no convertir cada tick en una aprobación humana. Alertas especiales pueden pedir autorización nueva sólo cuando hace falta. TTL orientativo one-shot: hasta 15 minutos, menor si la señal vence antes. Mandato inicial: 30 días, con aviso de vencimiento y opción de ampliar expresamente; nunca renovación silenciosa indefinida.

### Lease de control

El daemon consulta salud autenticada y cambios del control plane a intervalos cortos; valor inicial: 5 s, lease máximo 60 s, configurable según pruebas. Realtime reduce demora, no elimina polling. El lease mide conectividad/control fresco, no exige que el humano esté conectado.

Si PB/canal falla, no consulta la base por cada tick; puede operar bajo configuración cacheada sólo hasta que venza el lease. Vencido: no abrir riesgo, cancelar quotes cuando corresponda y conservar manejo de posiciones/reconciliación con políticas previamente autorizadas. Si sólo falla una proyección no crítica, registrar lag sin fingir pérdida del canal de comandos; diferenciar ambas fallas.

El kill switch se persiste localmente y en PB. En conexión sana se exige un objetivo probado de aplicación ≤5 s; en desconexión, límite del lease más cancelación del venue. El correo de confirmación indicará qué quedó detenido y qué posiciones permanecen.

## 13. Administración segura desde celular y laptop

El acceso no debe depender de estar en casa ni de una IP pública residencial fija. Preparar dos perfiles, desplegar uno y probar su recorrido completo. No construir frontend propio.

### Perfil A — recomendado: WireGuard por dispositivo

PocketBase escucha en loopback y Nginx sirve TLS sobre la red de administración. Un peer distinto para celular y otro para laptop, ambos con acceso desde internet al servidor WireGuard. Split tunnel sólo a recursos administrativos; el tráfico financiero del daemon no pasa por una VPN de evasión. No asignar IPs hasta inspeccionar la red existente ni modificar peers de otros proyectos.

Permisos por peer para el servicio administrativo, no acceso irrestricto a todos los hosts. Firewall IPv4/IPv6 coherente y revocación individual. Preparar perfiles y QR de forma segura, fuera de Git/logs/correos; importarlos en dispositivos puede requerir una única intervención humana. No usar el chat como canal de distribución de llaves.

Pruebas: acceso con datos móviles, acceso desde laptop en otra red, rechazo sin túnel, revocación de un peer sin bloquear al otro, enlace de correo al registro correcto. Emular viewport no prueba conexión real desde celular: si no tienes el dispositivo, deja ese check como pendiente humano, no aprobado ficticiamente.

### Perfil B — acceso web remoto con autenticación reforzada

Si el usuario prefiere no activar VPN, usar TLS, contraseña única robusta de PB, MFA del superuser, rate limiting, sesiones/tokens breves apropiados, backups, actualizaciones controladas y protección de origen. Un gateway por identidad con cookie puede proteger todo el host sin competir por el header de la API; usar sólo si ya existe acceso autorizado o el usuario aprueba configurarlo.

**Basic Auth de Nginx no es automáticamente una segunda capa para toda la API de PocketBase.** Nginx Basic valida `Authorization`, y el cliente PB utiliza ese header para su token. Superponer ambos sin diseño/pruebas puede provocar 401 o romper el admin. Esta es una consecuencia de integrar sus mecanismos de autenticación, no una limitación que se resuelva borrando indiscriminadamente el header. [Basic Auth de Nginx](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html) y [autenticación de PocketBase](https://pocketbase.io/docs/authentication/).

Para el perfil solicitado de Basic Auth, se admite proteger `/_/` y recursos administrativos estáticos con Basic, manteniendo `/api/` autenticada por PB y MFA. Documentar con claridad que Basic NO protege las peticiones API directas. No anunciarlo como dos factores ni como protección completa del origen. Las reglas/hooks, el MFA y los límites siguen siendo indispensables. Si se requiere segunda capa en todo el origen, elegir VPN o gateway probado; no inventar un proxy de tokens casero.

Restringir CORS/orígenes, conservar el token PB en rutas API, denegar administración HTTP sin TLS y evitar tokens en URLs/logs. Para SSE/realtime configurar proxy buffering/timeouts según documentación y probar reconexión. No cachear API/admin en CDN. Si se usa proxy de identidad, bloquear el bypass por IP/origen y validar headers sólo desde proxies confiables.

PocketBase documenta MFA con OTP por correo para superusers. Habilitar y probar recuperación segura; dos contraseñas no equivalen a MFA. El correo se vuelve parte de la seguridad: proteger también la cuenta de correo. Mantener un procedimiento de emergencia por SSH autorizado, no un endpoint público sin autenticación. [Producción y MFA de PocketBase](https://pocketbase.io/docs/going-to-production/).

En ambos perfiles: cuenta humana exclusiva del admin, credencial de daemon separada, PB sin puerto público directo y ningún secreto financiero en campos de PB. El enlace de correo navega a PB; no contiene bearer token, OTP ni autorización de compra. Los escáneres de enlaces de correo jamás deben activar una acción.

## 14. Wallets, permisos y secretos

Usar una wallet/cuenta dedicada a EdgeHunter, sin mezclar ahorro ni trading manual. El mayor riesgo ante compromiso incluye perder todo el saldo accesible, incluso con límites de aplicación; dimensionar el saldo real de la wallet acorde a ello. Una llave sin retiro todavía puede perder dinero operando mal.

La documentación actual de Polymarket describe Session Keys en beta para Deposit Wallets: pueden operar sin la llave del propietario y no retirar; su habilitación exige condiciones del proveedor, actualmente aprobación de Builder API y expiración específica. No asumir disponibilidad en una wallet existente ni permiso automático. Verificar scopes y revocación. Preferir sólo CLOB, no `ALL`, y dejar la llave propietaria fuera del daemon. [Session Keys oficiales](https://docs.polymarket.com/trading/session-keys).

Si no están disponibles, entregar alternativas concretas: (a) mantener shadow/paper mientras se obtiene acceso; (b) signer aislado con validación de payload y permisos mínimos técnicamente posibles; (c) hot wallet de fondos mínimos con llave propietaria, sólo tras aceptación explícita de que «no retiro» sería una política de software, no una garantía criptográfica. Un signer en el mismo host no protege contra root; indicarlo. No implementar una blockchain propia ni contratos de custodia experimentales para resolverlo.

No almacenar seed/private key en PocketBase, Parquet, mensajes, prompts, fixtures, dumps de error, backups sin cifrar o repositorio. Secretos mediante keychain/secret manager o archivos dedicados con permisos estrictos y credenciales de systemd cuando sea compatible. Separar desarrollo y producción; limitar lectura por usuario. La redacción automática de logs se prueba con secretos señuelo.

Registrar capacidades sin valores secretos: account/wallet ID, scopes, expiración y mecanismo de rotación/revocación. Alertar antes de vencer credenciales, session keys, mandato y certificado. Sin permisos para renovar, no renovarlos usurpando la firma humana; enviar acción requerida y degradar seguro al vencer.

El daemon no requiere credenciales de retirada/bridge arbitrario. Sus interfaces no aceptan destinos de pago propuestos por LLM ni direcciones configurables desde una noticia. Setup de allowances/firma de autorización es una acción sensible separada, con contrato/spender/monto y aprobación; no conceder allowances ilimitados por conveniencia.

## 15. Contabilidad y datos históricos

Ledger append-only con asientos balanceados por moneda/activo y claves idempotentes. Separar efectivo disponible/reservado, inventario, realized/unrealized PnL, fees, rebates confirmados, gas, bridge, gastos de API/datos y aportaciones/retiros. Resolver posiciones/cashflows por actividad real; depósitos no son ganancias y retiros no son pérdidas.

Mark-to-liquidation conservador con bid/profundidad para inventario largo; mostrar por separado valoración de referencia si se usa. Si el mercado deja de tener liquidez, no mantener el último precio como certeza: marcar valoración incierta y aplicar estrés para riesgo. Divisas y tokens pueden desviarse; no asumir paridad garantizada.

Reporte de rentabilidad con método explícito (TWR y, cuando corresponda, MWR), high-water mark ajustado por aportaciones y distinción entre resultado de trading y del negocio después de costos. Exponer costo marginal de infraestructura y, por separado, imputación del VPS compartido; no atribuir su factura entera silenciosamente.

Tabla Parquet particionada por fuente/fecha/mercado cuando resulte útil; compactación controlada, checksums, manifests y schema evolution. DuckDB para investigación con límites de CPU/RAM; single writer por dataset y snapshots consistentes. No ejecutar consultas pesadas dentro del loop de órdenes.

Retención configurable y basada en espacio/licencia: decisiones, mandatos, fills y ledger tienen prioridad de conservación; datos crudos voluminosos se comprimen/muestrean según necesidad. No borrar evidencia necesaria para auditar una operación. Al alcanzar cuota, detener ingestión no esencial antes de llenar el disco. Retención fiscal/legal queda pendiente de requisito jurisdiccional, no fijada arbitrariamente aquí.

## 16. Correo como interfaz operativa

Brevo con remitente/dominio autorizado, destinatario confirmado, plantillas texto+HTML y TLS. Reutilizar configuración autorizada cuando exista; no asumir que una dirección de otro contexto ya está habilitada para este proyecto. No enviar mensajes durante construcción a destinatarios inferidos; usar sink de prueba hasta confirmar la configuración.

| Categoría | Cuándo enviar | Contenido |
|---|---|---|
| `CRITICAL` | Pérdida de control, orden UNKNOWN, reconciliación rota, breach, credencial comprometida | Qué ocurrió, dinero/exposición afectados, mitigación realizada, acción y enlace |
| `ACTION_REQUIRED` | Aprobación, oportunidad manual, fondos, retiro, renovación o bloqueo humano | Decisión exacta, monto, riesgo, evidencia, vigencia y formulario PB |
| `DAILY_DIGEST` | Resumen habilitado por usuario o actividad material | PnL neto, exposición, costos, estado, cambios y pendientes |
| `WEEKLY_RESEARCH` | Hallazgos relevantes/gates/drift/capacidad | Lo aprendido, evidencia y propuestas; no spam de métricas sin cambio |

Defaults: críticos y acciones inmediatas, sin correos por cada trade. Un resumen diario en la zona horaria configurada sólo si hay actividad/cambio; si no, confirmación semanal de salud. La hora es configurable, no una automatización externa creada por este documento. Agrupar oportunidades repetidas, cooldown y deduplicación por causa/mercado; un incidente crítico no puede quedar silenciado por el límite de noticias normales.

Entrega: outbox durable, retry/backoff, message ID y estado `QUEUED/SENT/DELIVERED/BOUNCED/FAILED/UNKNOWN` según evidencia real del transporte. Aceptación SMTP no equivale a leído o entregado. Usar eventos de entrega cuando estén disponibles y autenticados; no depender de confirmación de lectura. Los reintentos de email pueden duplicar un mensaje, pero nunca una acción financiera.

Si falla Brevo, el daemon registra y activa canal de monitor externo. Sin canal fiable de alerta o control durante el plazo admitido, parar riesgo nuevo. No sustituir automáticamente por proveedores de pago sin autorización. ACK humano no significa incidente resuelto; ambos estados separados. Escalar críticos no reconocidos con frecuencia acotada, nunca bombardear.

### Plantilla de oportunidad que requiere acción

```text
Asunto: [EdgeHunter][ACCIÓN][vence HH:MM] Catalizador / activo

Qué pasó: hecho nuevo y fuente primaria
Detectado: hora, retraso de fuente y movimiento observado
Acción: aprobar plan acotado / revisar manualmente / no actuar
Precio y liquidez: dato fechado y si tiene retraso
Hipótesis: por qué podría quedar oportunidad; incertidumbre
Capital máximo: cantidad y moneda
Pérdida posible: cantidad, incluyendo escenario de pérdida total
Condiciones: precio máximo, vigencia, criterio de invalidación
Evidencia: enlaces verificados y contradicciones
Acceso: enlace al registro administrativo, sin token

Abrir este correo o enlace no ejecuta ninguna operación.
```

### Monitor externo obligatorio para live 24/7

Proveedor/host fuera del VPS, con notificación que no dependa del mismo proceso y preferiblemente de otro transporte de correo. Registrar heartbeat sólo después de comprobar progreso real de ingestión/control/ejecución requerida, no por tener un thread vivo. Usar pulsos separados de vida y readiness o payload de estado.

Configurar umbral y gracia (propuesta: pulso 60 s, alerta tras 3 ausencias), deduplicación y recuperación. Para producción live demostrar: parada completa del daemon/VPS simulada o real dentro de alcance → monitor detecta → mensaje llega. Si falta cuenta externa, preparar integración y marcar `LIVE_24_7_BLOCKED`; no fingir que un cron dentro del mismo VPS lo sustituye. Conservar vigilancia durante pausas de trading.

## 17. Presupuestos operativos y degradación

Costos monetarios requieren autorización. Default de gasto incremental de APIs/modelos/servicios: cero hasta que exista presupuesto. Se pueden usar cuotas gratuitas confirmadas y credenciales existentes dentro del permiso y límite otorgados. Un saldo de API previamente cargado no autoriza gastarlo sin límites.

Configurar hard caps diarios/mensuales por proveedor y total, reservas para peticiones en vuelo, máximos de tokens, calls/minuto, concurrencia, tiempo, bytes descargados y queries. Antes de una llamada reservar un costo máximo razonable; conciliar con usage real y margen por facturación retrasada. No tratar alertas de billing de nube como hard cap: limitar consultas/bytes desde la aplicación.

Monitorizar requests/errores/latencias p50/p95, lag del event loop, colas, FD, CPU/RAM, disco/inodes, WAL, outbox, cuotas y certificados. Bajo presión: suspender backfills/research, reducir universo/cadencia, conservar reconciliación/control/riesgo; nunca saltarse checks para recuperar velocidad.

Implementar retries con jitter y clasificación: 429/transitorios admiten backoff; 401/403 o permisos no se «solucionan» rotando identidad; requieren diagnóstico/acción. LLM caído → continuar sólo estrategias independientes certificadas. Weather caído → no operar weather. PB lease vencido → no riesgo nuevo global. Ledger inválido → halt global. Error de una estrategia → cuarentena de esa estrategia salvo contaminación compartida.

## 18. Pruebas y aceptación obligatorias

El harness ejecuta toda prueba viable y adjunta evidencia resumida, versiones y timestamps. `SKIPPED` no cuenta como PASS. Fixtures deben indicar origen/sintético y nunca incluir credenciales reales. Tests externos separados de CI sin red.

### Unitarias y de propiedades

- Conversión de monedas/decimals, rounding/tick, fees y mínimos; campos negativos, NaN y tamaños extremos rechazados.
- Cálculo de payoff/EV/edge sin spread duplicado; YES/NO y multioutcome, escenarios inválidos y resolución ambigua.
- Invariantes de ledger balanceado, capital reservado ≤ presupuesto disponible y límites bajo concurrencia.
- Aprobaciones inmutables, TTL, hash/revisión, no-replay, actor real, separación human_decision/execution_status.
- Invalidez de órdenes ante datos viejos, modelo no calibrado, contrato modificado o presupuesto agotado.
- Correlación/escenarios de pérdida, caja bloqueada, retiros, depósitos y high-water mark.
- Clasificación sin confundir score, probabilidad y retorno; calibración entrenada sólo con pasado.
- Secretos señuelo ausentes de logs, excepciones, correo y proyecciones.

### Integración

- Migraciones PocketBase nuevas y actualización desde versión previa sin tocar otros proyectos; ejecutarlas dos veces.
- Hooks/reglas: daemon intenta aprobar/cambiar actor/roles/límites/nonce y falla; humano sólo puede transicionar una solicitud válida por el flujo normal.
- Journal/outbox/PB: caída, reconexión, duplicados, orden inverso y recuperación sin perder ni duplicar efectos.
- Flujo completo dato→oportunidad→riesgo→paper→ledger→correo de sink y resumen.
- Una aprobación de mandato habilita sólo las estrategias/montos/versiones declarados; reinicio preserva consumo.
- Proveedor/SDK contract tests con metadata y respuestas reales read-only cuando haya acceso.
- Admin: login/MFA, API, SSE, renovación, enlace a registro, 401 esperados y no esperados; prueban colisión Basic/token si se usa ese perfil.

### Chaos/fault injection

| Falla inducida | Resultado exigido |
|---|---|
| Muerte antes/después de enviar orden | Recuperación sin duplicación; UNKNOWN conserva reserva |
| HTTP timeout con orden aceptada | Reconcilia orden existente, no reenvía como nueva |
| Dos estrategias consumen mismo saldo | Sólo reservas válidas; nunca overspend |
| Dos procesos/local y VPS | Segunda instancia ejecutora bloqueada; prueba de fencing/cutover |
| WS desconectado o gaps | Snapshot invalidado y reconstruido antes de operar |
| Proceso vivo con task muerta | Readiness falla, supervisión reinicia/pausa y alerta |
| PB inaccesible | Lease vence y bloquea riesgo nuevo; manejo seguro de posiciones |
| Disco lleno/corrupto | Sin nuevas aperturas; alerta y cancelaciones seguras disponibles |
| Datos/comunicado hostil | Sin comandos, secretos ni escalamiento de permisos |
| Brevo falla o rebota | Reintento y escalamiento externo; sin afirmar entrega |
| Reloj desfasado | Bloqueo de operaciones sensibles y diagnóstico |
| Fill parcial y cancelación concurrente | Ledger/exposición reflejan fills reales, no saldo liberado prematuramente |
| Backup restaurado con aprobación vieja | No reaplica; arranque en reconciliación y live deshabilitado |
| Depósito/orden manual inesperados | Reconciliación y revisión; sin aumentar límites automáticamente |
| Cambio de reglas/fees/tick/credencial | Invalida planes afectados y evita firma con información anterior |
| Caída total del VPS | Alerta desde monitor externo; riesgo residual de posiciones documentado |

### Realismo de replay/paper

Reproducir latencia, book depth, ticks/mínimos, fees, rechazos, fills parciales, cancel race y settlement. Para maker modelar cola/adverse selection; si no se dispone de datos de cola, presentar cotas conservadoras y declararlo. «Tocó precio» no significa fill.

Reportes con escenarios optimista/base/conservador y sensibilidad a costos/latencia. No rellenar huecos históricos con información futura. Backtest de noticias/modelos generales puede tener contaminación por conocimiento posterior: etiquetarlo y exigir prueba prospectiva para validar alpha. Tests históricos no pueden demostrar que un LLM «habría sabido» algo antes.

### Gates de promoción

1. **Código listo**: lint/types/tests, invariantes críticas, smoke reproducible, dependencias verificadas, sin TODO en rutas obligatorias.
2. **Datos listos**: contratos/frescura/cobertura comprobados, fuentes autorizadas y disponibilidad histórica razonable.
3. **Research válido**: hipótesis predefinida, evaluación temporal, control de selección, intervalos y costos. Conclusión puede ser NO EDGE.
4. **Shadow/paper estable**: prueba de estabilidad continua de al menos 72 h como gate operativo propuesto; no demuestra rentabilidad. Fijar ventana estadística por dominio y número efectivo de eventos antes de observar resultados.
5. **Micro-live autorizado**: mandato explícito, balance/fees/permisos, acceso remoto, alertas, recuperación y fondos mínimos compatibles. Sólo entonces ejecutar prueba económica pequeña.
6. **Live**: evidencia prospectiva de ejecución/costos y ventaja neta con incertidumbre aceptable, capacidad y aprobación. Si la muestra no sostiene la conclusión, mantener la etapa anterior; no inventar un N universal.

Ninguna duración garantiza edge. Muchas operaciones del mismo evento no son muchas observaciones independientes. Democión/cuarentena automática por drift, breach, error de ejecución o pérdida de validación; la reactivación no es automática tras «un día bueno».

## 19. Bootstrap, CLI y experiencia de cero trabajo manual técnico

Entregar un bootstrap idempotente que instala dependencias del proyecto en entorno aislado, descarga PocketBase de procedencia oficial verificando integridad disponible, crea carpetas/secret templates con permisos seguros, aplica migraciones y ejecuta doctor. Nunca sobrescribe config/secretos existentes ni habilita live. Si un paquete necesita permisos de sistema, identifica el bloqueo antes de cambiar el host.

La CLI implementará realmente estos contratos (puede ajustar sintaxis con documentación consistente):

```text
edgehunter doctor --redact
edgehunter demo
edgehunter run --mode shadow
edgehunter run --mode paper
edgehunter status
edgehunter sources probe
edgehunter strategies evaluate
edgehunter reconcile --read-only
edgehunter approvals list
edgehunter pause --reason <motivo>
edgehunter cancel-open --scope edgehunter
edgehunter backup
edgehunter restore --dry-run <backup>
edgehunter report --period daily
edgehunter deployment preflight
```

Los comandos de cancelación/mantenimiento que muten un venue sólo funcionan con autenticación, alcance y autorización apropiados. No incluir un `--yes` que habilite live o ignore restricciones indiscriminadamente. Live se activa por mandato, no por descubrir una variable secreta.

`demo` arranca PocketBase aislado y recorre un caso completo reproducible sin dinero ni servicios de pago; incluye oportunidades buenas/rechazadas, aprobación simulada claramente etiquetada, fallas y correo en sink. El recorrido real shadow obtiene datos públicos cuando esté permitido y muestra fuentes reales/latencia; no mezclarlo con fixtures.

Arranque local por un solo comando; el harness lo ejecuta, no se limita a entregarlo. Debe terminar con health checks y resumen de URL, modo, fuentes activas y bloqueos. Si hay panel de admin de prueba, credenciales por canal seguro, no en README. Autoapagado ordenado del entorno demo; no mata otros PocketBase o Python por nombre.

## 20. Despliegue y operación 24/7

### Instalación controlada

Preflight de capacidad y convivencia: CPU/RAM/disk libre, IO, puertos, Nginx, firewall y servicios del host. Usar límites para proteger los demás proyectos; no se presupone que «Python es ligero» resuelve cualquier carga. Ajustar universo/retención tras profiling.

Usuario Unix dedicado sin sudo en runtime. Unidades separadas para daemon y PB, no procesos root. Código y hard envelope de sólo lectura para runtime; sólo directorios de estado/log/cache escribibles. Hardening compatible: `NoNewPrivileges`, aislamiento de tmp, permisos restrictivos, filesystem protegido y límites de memoria/CPU/FD. Probar antes de imponer flags que rompan librerías.

systemd con reinicio/backoff y límites de restart, parada ordenada y watchdog ligado a progreso real. Evitar restart storm; tras fallas reiteradas dejar halt y alerta. No confundir `WatchdogSec` con integración funcional: si se usa, implementar notificación del daemon y testear el bloqueo del loop.

Backfills, compactación, backups y research mediante timers/tareas de baja prioridad. No suspender reconciliación para recalibrar. Alertar por reloj, disco, actualización de certificados y expiraciones. No actualizaciones automáticas de código/SDK/modelos en producción que cambien conducta financiera; preparar upgrade canario, contract tests y aprobación.

### Cutover local → VPS

1. Instalar VPS en shadow, verificar permisos del proveedor desde esa ubicación y probar acceso/alertas.
2. Pausar entradas en local; cancelar/verificar órdenes pendientes según plan. Resolver estados UNKNOWN.
3. Detener instancia local y revocar/retirar su capacidad de firma. Un lock de archivo sólo protege el mismo host, no esta migración.
4. Tomar snapshot consistente del journal, ledger, consumos/mandatos y artefactos necesarios; transferir por canal cifrado y verificar checksum.
5. Restaurar con live apagado y reconciliar directamente contra venue. Confirmar mandato válido o solicitar uno nuevo si cambia identidad/entorno relevante.
6. Habilitar una sola ejecutora autorizada, probar readiness y registrar instancia/epoch. Mantener local sin signer o en modo lectura.
7. Probar que arrancar accidentalmente local no puede operar. Si el proveedor no permite fencing efectivo, la revocación/aislamiento del signer anterior es gate obligatorio.

### Backup/restore y rollback

Backups consistentes de SQLite mediante API de backup o mecanismo seguro del motor; no copiar únicamente `.db` mientras WAL sigue activo. Incluir PB con mecanismo compatible, journal/ledger, configuración no secreta, versiones, manifests y calibradores aprobados. Cifrar antes de salir del host y guardar copia fuera del VPS con acceso mínimo; backups que viven sólo en el mismo disco no cubren pérdida del servidor.

Objetivos iniciales para aprobar: RPO ≤15 min para datos de control/config y copia financiera incremental tan frecuente como sea viable; ningún fill reconocido puede depender sólo de una proyección PB. RTO ≤60 min como objetivo de recuperación, a medir en simulacro; no prometerlo sin prueba. El journal local protege contra caída de proceso, no contra destrucción del disco. Registrar qué puede reconstruirse del venue y qué decisiones/contexto se perderían desde la última copia.

Restore siempre en ambiente aislado/live apagado, compara checksums, valida schema, concilia órdenes/fills/saldos y preserva nonces/consumos. No reaplicar outbox financiera como si fueran intenciones nuevas. Ensayar recuperación periódica. Si no existe destino autorizado de backup externo, reportar bloqueo de producción.

Deploy por releases versionadas y cambio atómico con pausa de riesgo. Rollback de binario no revierte operaciones financieras ni restaura a ciegas una base antigua. Migraciones compatibles o procedimiento explícito; diferencias de schema detienen rollback automático. No borrar backups ni releases útiles sin política/permiso.

### Procedimientos de incidente

Entregar pasos concretos para: feed stale; PB caído; correo caído; VPS caído; orden UNKNOWN; exposición discrepante; saldo/fees inesperados; wallet comprometida; mandato/session key vencidos; pérdida del dispositivo; recuperación del MFA; disk full; dependencia rota y pérdida de edge.

Ante compromiso de wallet: detener firmas/órdenes cuando sea posible, revocar sesión desde canal seguro, avisar; la transferencia de fondos restantes la hace el usuario. Ante celular perdido: revocar peer/sesión humana y mantener laptop. Un incidente financiero no se «resuelve» reiniciando el daemon sin reconciliar.

## 21. Secuencia de construcción y evidencia de entrega

Implementa en este orden para que la seguridad exista antes de incorporar dinero:

1. **Descubrimiento y matriz**: entorno, fuentes/versiones, alcance, permisos, riesgos, costos y bloqueos.
2. **Núcleo offline**: tipos, journal/ledger, configuraciones, risk engine, mandatos, simulador y tests de invariantes.
3. **Vertical funcional**: Polymarket lectura → estrategia determinista → paper → PB → correo sink, con bootstrap de un comando.
4. **Control humano**: hooks, permisos, aprobación acotada, lease, admin remoto y anti-replay.
5. **Todos los plugins**: adapters verificables, Jev/LLM/weather, radar, research/calibración, capacidad y allocators. Implementación real incluso si una credencial impide activación.
6. **Executor certificado técnicamente**: firma aislada, órdenes/cancelación, reconciliación, retries seguros y chaos; sin fondos reales por defecto.
7. **Operación**: Brevo real autorizado, monitor externo, presupuestos, backup/restore, systemd/Nginx y cutover.
8. **Validación local completa**: ejecutar tests, smoke y shadow/paper, corregir errores, medir consumo y producir informe.
9. **VPS**: desplegar sólo con destino/acceso autorizados; iniciar shadow y período de observación. Si bloqueado, dejar instalador probado y estado explícito.
10. **Activación financiera**: preparar expediente de gates y mandato. La construcción termina sin invertir si aún falta evidencia, autorización o fondos; la observación puede continuar sola.

Por fase registrar comandos ejecutados, resultado, tests/cobertura crítica, evidencias redactadas y requisitos pendientes. Si el harness se queda sin tiempo/contexto, checkpoint de estado y siguiente acción exacta; no rebautizar pendiente como opcional.

### Definition of Done del software

- [ ] Repositorio completo, sin implementaciones falsas en rutas prometidas.
- [ ] Bootstrap y demo ejecutados en máquina soportada sin cuentas pagadas.
- [ ] Datos reales en shadow o bloqueo de acceso demostrado; estado claramente distinguido de demo.
- [ ] Todos los plugins implementados y probados; cobertura y dependencias externas visibles.
- [ ] Journal/ledger transaccional, outbox, reconciliación y anti-duplicación probados con fallas.
- [ ] Motor de riesgo/mandatos impide autoaprobación y límites ampliados desde PB/modelo.
- [ ] Flujo PB «pendiente → aprobado → aplicado/en ejecución» validado de punta a punta.
- [ ] Acceso remoto seguro probado hasta donde permitan dispositivos/autorización; pendientes explícitos.
- [ ] Correo con sink probado; Brevo real y monitor externo sólo declarados listos si hubo prueba real.
- [ ] Reportes PnL/costos y exportación contable coherentes.
- [ ] Investigación walk-forward, calibración candidata, capacidad y gates implementados.
- [ ] Despliegue automatizado, backups/restore/rollback y cutover probados en entorno seguro.
- [ ] Live apagado sin mandato y posible reanudación únicamente bajo política explícita.
- [ ] `ACCEPTANCE_REPORT.md` distingue implementado, probado, activo, bloqueado y no validado financieramente.

La entrega puede alcanzar «software completo» sin alcanzar «rentabilidad demostrada». Si cualquier componente obligatorio está incompleto, decirlo con precisión y seguir trabajando en lo viable; no atribuirlo genéricamente a «fase 2».

## 22. Acciones humanas: último recurso, no lista de tareas delegadas

El harness debe primero ejecutar lo permitido y preparar el resto. `HUMAN_ACTIONS.md` contiene únicamente bloqueos reales, no todos los pasos posibles de esta tabla:

| Bloqueo | Preparación a cargo del harness | Intervención mínima del usuario |
|---|---|---|
| Login/MFA/KYC/contratos | Abrir flujo, comprobar requisitos, conservar checkpoint seguro | Identificarse/aceptar/firma personal cuando corresponda |
| API o dataset restringido | Adaptador, pruebas, enlace y permisos exactos necesarios | Autorizar acceso o aportar secreto por canal seguro |
| Session Keys beta | Comprobar elegibilidad y preparar solicitud/configuración | Autorización/firma/contacto exigido por proveedor |
| Gasto incremental | Estimación, hard caps y alternativas gratuitas | Aprobar proveedor y monto máximo |
| VPS/DNS/SSH | Scripts idempotentes, dry-run, inventario y plan de cambios | Dar acceso/autorización al destino exacto |
| Celular/laptop | Perfiles seguros, QR local y prueba guiada preparada | Importar perfil/login y comprobar acceso en su equipo |
| Remitente/destinatario | Configuración, plantillas y smoke en sink | Confirmar cuenta/destinatario y verificaciones del proveedor |
| Monitor/backup externo | Integraciones listas, prueba automatizada preparada | Autorizar cuenta/destino cuando no exista |
| Fondos y live | Evidencia, presupuesto exacto, mandato y plan de pérdida máxima | Depositar/firmar/aprobar desde acceso seguro |
| Retiro | Monto líquido, costos y motivo; seguimiento contable | Retirar desde su wallet y confirmar/reconciliar |

No intentar resolver un bloqueo con credenciales de otro proyecto, saltos de seguridad o contratación silenciosa. Mientras espera, continuar módulos independientes y observación segura. Preguntar agrupado, con contexto precargado y sin hacer repetir decisiones ya resueltas.

## 23. Documentación final requerida

El repositorio debe incluir:

- `README.md`: arranque único, modos, acceso, estado real, prueba rápida y límites.
- `docs/OPERATIONS.md`: arranque/parada, aprobación, pause/cancel/halt, alertas, incidentes y renovación.
- `docs/SECURITY.md`: threat model, permisos reales de wallet, MFA/VPN, PB superusers, datos hostiles y secretos.
- `docs/PROVIDERS.md`: fuentes verificadas, versiones, costos, cuotas, licencias, delays y restricciones.
- `docs/STRATEGIES.md`: hipótesis/supuestos por plugin, entradas, salidas, fees, capacidad, calibración y estado.
- `docs/VALIDATION.md`: causalidad temporal, contaminación, selección múltiple, shadow/paper/micro-live y promoción.
- `docs/ACCOUNTING.md`: ledger, PnL, cashflows, FX, fees, reconciliación y exportación.
- `docs/DEPLOYMENT.md`: preflight, unidades, Nginx, firewall, rollout/cutover, backup/restore y rollback.
- `IMPLEMENTATION_STATUS.md`, `HUMAN_ACTIONS.md`, `ACCEPTANCE_REPORT.md`: trazabilidad y entrega honesta.

## 24. Fuentes y política de vigencia

Las referencias enlazadas en cada sección se consultaron al preparar este documento. Son puntos de partida oficiales, no garantía de permanencia de APIs, tarifas o permisos. El harness debe volver a validarlas al construir y antes de habilitar live, guardando fecha, versión y decisiones de compatibilidad en `docs/PROVIDERS.md`.

Las reglas de arquitectura, límites propuestos, schemas, tests y flujos de este documento son decisiones de diseño del proyecto, no afirmaciones de que el proveedor ya los implementa. No copiar ciegamente ejemplos de internet ni usar resultados anteriores del chat como hechos verificados.

Prioridad ante contradicción: permisos/condiciones vigentes del servicio y autorizaciones explícitas del usuario → invariantes de seguridad → requisitos funcionales → preferencia de implementación. Si cumplir un requisito exige más autoridad o riesgo, reportar el conflicto y no sortearlo.

**Resultado esperado:** un sistema completo y auditable que observa, investiga, propone, opera sólo bajo mandato y se comunica por excepción; que puede permanecer sin operar cuando no hay ventaja, y que pide ayuda únicamente cuando una decisión humana o un bloqueo real lo exige.
