# Contabilidad de simulación

El journal SQLite es la autoridad financiera local. PocketBase recibe proyecciones: modificar una proyección no cambia saldos. El ledger es append-only, usa cantidades `Decimal`, partidas balanceadas por activo y claves de idempotencia. Intenciones, reservas y outbox se comprometen transaccionalmente; el archivo usa WAL y sincronización FULL.

Los depósitos aumentan caja y capital aportado; nunca ganancias. Una compra transforma caja/reserva en inventario a costo. Las comisiones son gasto separado. Una venta o liquidación confirmada del simulador elimina costo y registra diferencia como ingreso realizado. Gastos operativos se descuentan del resultado del negocio.

```text
PnL trading neto = ingreso realizado - comisiones
PnL negocio neto = PnL trading neto - costos operativos registrados
```

`report --period daily` calcula movimientos del día en `America/Mexico_City`; las marcas del ledger permanecen en UTC. `--period all` acumula todo el journal. `reports/paper.json` incluye saldos actuales como sección separada, y `reports/ledger.csv` exporta las partidas completas. Los saldos actuales no son automáticamente el saldo al comienzo o cierre del período seleccionado.

El PnL es realizado, con posiciones abiertas informadas a costo. No se implementó mark-to-liquidation continuo, drawdown diario ajustado por flujos, impuestos ni prorrateo de infraestructura. Los high-water marks y controles actuales usan resultado realizado acumulado, incluidos los gastos registrados. Las piernas liquidadas secuencialmente pueden generar un máximo intermedio; no representa valoración conjunta de mercado.

Las fills simuladas consumen profundidad disponible una sola vez por snapshot, descuentan latencia/adversidad y respetan límite, mínimo, tick y reserva. Las tasas del candidato prospectivo son supuestos explícitos de sensibilidad; el core redondea débitos hacia arriba y créditos hacia abajo a seis decimales. Los replays históricos emplean otro modelo de costos documentado y no replican el matching del venue.

Una operación `UNKNOWN` mantiene sus reservas; una retransmisión no duplica fills. El candidato actual compra una sola share binaria por posición y simula IOC sobre la cantidad propuesta. Para liquidar, exige metadata terminal `closed=true`, `umaResolutionStatus=resolved` y payoffs terminales válidos. Esto liquida dinero ficticio; no ejecuta redención onchain ni acredita fondos reales reclamables.

La demo, los experimentos y el daemon tienen estados separados. Sus ganancias no se suman como una sola cuenta. Los costos de la demo son ficticios. El presupuesto cero bloquea proveedores pagados; no significa que electricidad, equipo, red o impuestos tengan costo económico cero.

Treasury genera recomendaciones de sólo lectura y conserva costos/impuestos desconocidos como bloqueos. No retira ni reinvierte fondos automáticamente.
