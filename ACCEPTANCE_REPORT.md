# Aceptación de la entrega local

Fecha local: 18 de septiembre de 2026. Resultado: **proyecto de investigación y simulación funcional**. El paper anterior registra 8 liquidaciones y −5.35352756764 SIM_USD al corte de 13:40 UTC; se abrió un paper aprendido separado con 100 SIM_USD y controles adicionales. **No se acredita rentabilidad robusta ni el alcance completo de producción del runbook**. El detalle de lo implementado y pendiente está en [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Resultados financieros ficticios

Cada fila es una cuenta/experimento separado; no deben sumarse.

| Evaluación | Capital inicial | Ganancia neta | Capital final | Qué demuestra |
|---|---:|---:|---:|---|
| Demo sintética de ingeniería | 1,000 | +1.2072 | 1,001.2072 | Circuito de propuesta, reserva, fills, contabilidad, PocketBase y sink |
| Histórico 1, base, 13 operaciones | 1,000 | -5.3641 | 994.6359 | Fracaso fuera de muestra preservado |
| Histórico 2, base, 33 operaciones | 1,000 | +1.7987 | 1,001.7987 | Resultado exploratorio positivo con supuestos de ejecución |
| Histórico 2, conservador, 33 operaciones | 1,000 | -6.4038 | 993.5962 | La mejora no sobrevive costos conservadores |
| Réplica externa de favoritos, base, 2 operaciones | 1,000 | -9.7154 | 990.2846 | La regla publicada no se replica en esta muestra |
| Escaneo estructural, 500 mercados | 1,000 | 0 | 1,000 | Nueve libros elegibles; cero descuentos ejecutables |
| BTC 5m anterior, 8 fills liquidados | 1,000 | -5.35352756764 | 994.64647243236 | 4 victorias y 4 derrotas; saldo nominal, esta versión no imponía reservas/frescura verificadas |
| Nuevo BTC aprendido v5, al arrancar | 100 | 0 | 100 | Cuenta separada, reserva hasta liquidación y máximo 10 por apuesta; resultado futuro en btc_challenger_live.json |
| BTC aprendido, holdout congelado de 270 días | 100 | +1.6989 | 101.6989 | 2/3 en 3 días; resultado positivo pero insuficiente, IC95 [-25.55, 26.20] |
| Daemon con libros públicos nuevos, al aceptar | 1,000 | 0 | 1,000 | Captura real y listo para riesgo paper; todavía no encontró una entrada válida |

El segundo experimento congeló el modelo antes de descargar su ventana nueva; utilizó 497 observaciones de desarrollo y 217 de holdout. El gate de selección en desarrollo falló. Se preservan 25 especificaciones consideradas entre ambos experimentos. El IC95 descriptivo base [-30.4311, 30.3200] cruza cero; no hay promoción automática ni afirmación de edge demostrado.

La evaluación BTC ampliada reunió 183,691 observaciones causales de 73,112 mercados resueltos. Ninguna de 81 reglas manuales pasó el gate de desarrollo. De 96 variantes aprendidas, 14 pasaron entrenamiento y validación; se eligió una sola antes de abrir el holdout congelado. Allí produjo +1.6989 en tres operaciones durante tres días, con IC95 [-25.55, 26.20], por lo que el estado final es `HOLDOUT_INSUFFICIENT`, no rentabilidad demostrada.

Se ejecutaron ambas reproducciones `--offline` con hashes intactos y se obtuvieron los mismos PnL. Los históricos contienen precios, no libros/fills/fees ejecutables verificados; la disponibilidad histórica y demora de redención son aproximaciones. La prueba sintética nunca se presenta como ganancia de mercado.

## Verificación ejecutada

- Suite completa de pytest, sin credenciales externas: [tests.xml](reports/tests.xml). Incluye unitarias, integración con PocketBase real, control end-to-end, concurrencia y recuperación de fallos.
- Suite ampliada: **250 pruebas aprobadas**, sin fallos ni omisiones; [tests.xml](reports/tests.xml). Después del ajuste final de captura se repitieron las 8 pruebas del nuevo motor, también aprobadas.
- `mypy src`: sin errores en 51 archivos.
- `ruff check src tests scripts deploy`: sin hallazgos.
- Bootstrap completo en Windows ejecutado, tanto `-Demo` como arranque normal; parada y nuevo arranque confirmados. Correlación de arranque probada para el PID intermediario del entorno virtual Windows.
- Demo repetida: 8 propuestas aceptadas, 4 rechazos, 16 fills; 0.9928 de comisiones y 0.20 de gasto ficticio, sin inventario ni reservas restantes. Una notificación entregada al sink local.
- PocketBase dedicado: migraciones idempotentes, cuenta daemon sin aprobación, actor humano sellado, auditoría, expiración, revocación, carrera de aprobación, restore aislado y consumos preservados.
- Journal: idempotencia, reservas/fills en transacciones, no doble gasto, fees redondeadas, ventas parciales, límites reevaluados al enviar, UNKNOWN sin liberar fondos, recuperación tras caída y fencing local.
- Reporte diario: zona America/Mexico_City, depósitos separados del PnL, gastos y exportación CSV comprobados.
- Backup real del estado local con daemon detenido: journal `journal-20260918T015036.db` y PocketBase `pb-acceptance-20260918T0150`; ambos en `.local/backups`. Inspección de integridad y restore dry-run correctos. Los tests también materializan restauraciones aisladas; no se reemplazó el estado activo.
- Fuentes públicas consultadas y administrador con `/api/health` HTTP 200; snapshot final en [acceptance.json](reports/acceptance.json).

Los casos `pytest` usan fixtures explícitas para las oportunidades y proveedores pagados. La suite no certifica rentabilidad, entrega de correo externo ni comportamiento continuo de una cuenta de trading real. No se realizó una prueba de estabilidad de 72 horas.

La ampliación añade pruebas de checkpoints/reanudación, datos causales, reservas de efectivo durante liquidaciones, ejecución del modelo congelado, velas de Coinbase en el límite de minuto, probabilidad posterior a latencia, esquema/plazo/presupuesto de JEV y lectura de `.env` sin ejecutar su contenido. La [auditoría y las mediciones](reports/engine_improvements_20260918.md) preservan el holdout original y distinguen validación reutilizada de evidencia nueva.

La llave de JEV fue aceptada mediante `GET /v1/models` con HTTP 200. No se ejecutó inferencia mientras el presupuesto configurado permaneció en cero. [Evidencia de acceso sin secretos](reports/jev_access.json).

## Estado operativo y acceso

Arranque nativo local en `PAPER`, con presupuesto de proveedores cero, capacidad live ausente y cuenta de 1,000 SIM_USD. `edgehunter status` indica URL administrativa loopback y ruta de credenciales restringidas; el puerto se elige en cada arranque. La URL de la verificación está en `reports/acceptance.json` y puede quedar obsoleta al reiniciar.

El reloj de Windows estaba adelantado unos 20.33 segundos porque el servicio `W32Time` estaba detenido y una directiva tenía el servidor NTP vacío. Se activó el cliente con arranque automático, se configuró `time.windows.com,0x8` y se sincronizó el sistema. La verificación posterior mostró un desfase NTP aproximado de 0.36 s; el daemon pasó a `OBSERVING`, `clock.status=HEALTHY` y `ready_for_new_paper_risk=true`. El control no se relajó: si vuelve a superar el umbral, bloquea riesgo nuevo.

PocketBase y journal siguen siendo locales bajo el mismo usuario de Windows; no representan separación de privilegios de producción. VPS: **NO DESPLEGADO**. Correo externo, MFA/VPN, monitor independiente, backup cifrado externo y ejecución financiera real: no habilitados.

## Evidencia y reproducibilidad

`reports/acceptance.json` conserva la aceptación inicial y sus hashes; no representa el código ampliado de esta sesión. La verificación actual está en `reports/tests.xml` y `reports/engine_improvements_20260918.md`. Los datos crudos, registros de selección y modelo congelado están en `data/research/`, `data/research_v2/` y `data/btc_history_270d/`; están excluidos de Git pero se conservan en esta máquina. La revisión del código sin esos insumos no permite reproducir offline el histórico.

Se creó un repositorio Git local sin publicación remota. No se contrataron proveedores, enviaron correos externos ni usaron fondos reales. El único cambio del sistema fue reparar y activar la sincronización horaria de Windows. Las intervenciones externas opcionales se explican en [HUMAN_ACTIONS.md](HUMAN_ACTIONS.md).
