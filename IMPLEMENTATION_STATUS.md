# EdgeHunter — seguimiento verificable

Entrega local actualizada: 18 de septiembre de 2026 (America/Mexico_City). Solicitud: construir y probar un proyecto que opere con dinero ficticio a partir del runbook suministrado. El runbook se conserva como especificación de referencia, no como autorización para cuentas, gastos, operaciones reales, correos o despliegues externos.

| Requisito | Estado verificado | Evidencia / límite |
|---|---|---|
| Entorno aislado y dependencias fijas | IMPLEMENTADO Y EJECUTADO | Python 3.11.1, uv.lock, bootstrap nativo Windows, PocketBase verificado por SHA-256 |
| Dominio, journal, ledger, reservas y riesgo | IMPLEMENTADO Y PROBADO | Decimal, transacciones SQLite, idempotencia, consumos, TTL, límites, desconocidos, concurrencia y crash recovery |
| Datos públicos reales Polymarket | VERIFICADO | REST catálogo/libros/históricos/fees/hora/geoblock; reports/sources.json y archivos con manifiestos |
| Paper con ejecución causal | IMPLEMENTADO Y PROBADO | Profundidad consumida, latencia, adversidad, comisiones, cancelación y liquidación ficticia |
| Candidato paper prospectivo | IMPLEMENTADO; EN OBSERVACIÓN | Modelo congelado, una entrada por mercado, horizonte 23–25 h; reloj NTP corregido y gate de riesgo habilitado |
| Demo completa | EJECUTADA | 16 fills, 8 propuestas, 4 rechazos, PocketBase real, sink local; +1.2072 ficticio neto |
| Siete familias de estrategias | LÓGICA IMPLEMENTADA; DATOS INSUFICIENTES | Plugins y pruebas; no se acreditan siete estrategias operativas con edge |
| Investigación temporal y calibración | TRES EXPERIMENTOS REPRODUCIDOS | Experimento 1 -5.3641 base; experimento 2 +1.7987 base / -6.4038 conservador; réplica externa -9.7154 base |
| BTC 5 minutos anterior | NEGATIVO AL CORTE; EN OBSERVACIÓN | 8 fills liquidados, 4 victorias, −5.35352756764 SIM_USD; esta versión no imponía reservas de capital ni frescura de libros |
| BTC aprendido, paper con 100 | IMPLEMENTADO; EN OBSERVACIÓN | Modelo congelado, máximo 10 por apuesta, reservas hasta liquidación, frescura y margen reevaluado; cuenta y manifiesto v5 separados |
| Velocidad y reanudación histórica | IMPLEMENTADO Y MEDIDO | Partición de 183,691 filas en 0.125 s; checkpoints atómicos con huellas, progreso y bloqueo de reapertura incompleta del holdout en el mismo directorio |
| Stress de capital | DIAGNÓSTICO EJECUTADO | 18 escenarios con fondos pendientes, costos y tamaño de apuesta; usa validación ya vista, sin reentrenar ni reabrir holdout |
| BTC histórico causal, 270 días | EJECUTADO; EVIDENCIA INSUFICIENTE | 183,691 observaciones; 81 reglas manuales sin candidato robusto. Modelo aprendido: +1.6989 en holdout, 2/3, sólo 3 días e IC95 que cruza cero |
| PocketBase dedicado y control humano | IMPLEMENTADO Y PROBADO LOCALMENTE | Roles, hooks, decisiones inmutables, auditoría, pausa autenticada y persistencia tras reiniciar |
| Aprobaciones de todas las clases del runbook | PARCIAL | Esquema y reglas; consumo operativo limitado a parada/pausa y aprobación simulada. Sin promoción general, nuevo presupuesto o cambio de mandato |
| Contabilidad y exportación | IMPLEMENTADA CON LÍMITE | PnL realizado, gastos, depósitos separados, reporte diario local y CSV; no valoración liquidable continua |
| Parquet y DuckDB | IMPLEMENTADO Y PROBADO | Archivo con hashes, límite de memoria y cuota aproximada; no política de archivo remoto automática |
| Radar | PARCIAL | Coinbase/RSS real mediante probe; adaptadores adicionales probados. Sin vigilancia continua de todas las clases |
| JEV probabilístico | INFERENCIA Y PAPER VERIFICADOS; RESULTADO NEGATIVO | 33 forecasts resueltos, 20 fills, 12 victorias y −35.6451 SIM_USD; Brier 0.1695 frente a 0.1305 del mercado. Presupuesto durable, timeout y abstención probados; no demuestra edge |
| WeatherNext | ADAPTADOR PROBADO CON MOCK | Sin llamada externa ni calibración meteorológica; sin gateway frontier general |
| Correo | SINK LOCAL VERIFICADO | Brevo adaptado y probado con mock; sin envío externo, callback autenticado de entrega ni escalamiento independiente |
| Treasury | RECOMENDACIONES IMPLEMENTADAS | Sólo lectura; sin retiros, asignación dinámica de capital ni reinversión automática |
| CLI, daemon y restart | IMPLEMENTADO Y EJECUTADO | Lock antes de mutación, heartbeat por tarea, lease, stop, candidato, degradación y liberación al reiniciar |
| Backup / restore | PROBADO LOCALMENTE | Copia SQLite consistente, checksums, inspección y restore aislado de PocketBase sin reaprobación |
| Despliegue Linux | PREPARACIÓN PARCIAL | Render de systemd/Nginx probado; falta instalación en host y validación efectiva de servicios/red |
| VPS, dominio, VPN/MFA, monitor externo | NO DESPLEGADO | Sin destino/cuentas/autorización; no acreditados por la operación local |
| Ganancia prospectiva ficticia | RESULTADO ANTERIOR AHORA NEGATIVO | PnL −5.35352756764 tras 8 fills; saldo nominal 994.64647243236; la ganancia anterior no se mantuvo |
| Rentabilidad repetible o real | NO DEMOSTRADA | Una victoria no estima estabilidad; intervalos previos cruzan cero, dos pruebas pierden y no existe ejecución con fondos reales |
| Wallet, firma, SDK operativo live | NO IMPLEMENTADO NI HABILITADO | Fuera de la ejecución ficticia solicitada; jamás se conecta dinero real |

## Trabajo de software todavía pendiente del runbook completo

- Suscripción WebSocket persistente y reconciliación de canal de usuario; hoy se usa sondeo REST e invalidación conservadora de cambios de libro.
- Grafo completo de exposición entre venues/factores, allocator adaptativo y drawdown diario con valoración liquidable ajustada por flujos.
- Reanudación/promoción de mandatos con todos los tipos de aprobación; el halt actual no se borra automáticamente.
- Workflow de redención/merge/split onchain y reconciliación con cuentas reales, que requieren además una capacidad financiera fuera de esta entrega.
- Gateway frontier intercambiable y radar continuo multiactivo; validación de forecasts, cohortes y fuentes para las siete familias.
- SMTP/Brevo con eventos de entrega verificados, escalamiento y monitor independiente; instalación Linux y recuperación cifrada fuera del equipo.
- Aislamiento y fencing entre hosts con signer, prueba de carga prolongada y 72 horas de estabilidad continua.

Estas carencias no se atribuyen todas a falta de credenciales: son trabajo explícito pendiente del producto completo. La entrega funcional es la plataforma local de investigación/paper con pruebas y resultados ficticios, no todo el runbook de operación financiera en producción.

Verificación actualizada y límites: [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md). Detalle de cambios, experimentos separados y resultados: [engine_improvements_20260918.md](reports/engine_improvements_20260918.md).
