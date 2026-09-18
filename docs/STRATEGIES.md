# Estrategias EdgeHunter

Las siete familias tienen lógica de propuesta y abstención en `src/edgehunter/strategies/plugins.py`. Son componentes de investigación: no reciben credenciales ni clientes capaces de enviar órdenes. Su registro declara `IMPLEMENTED` para la lógica, `DATA_BLOCKED` para operación y permite solamente REPLAY, SHADOW y PAPER.

| Familia | Lógica implementada | Insumo que impide operación automática hoy |
|---|---|---|
| Structural | Menor pago entre todos los escenarios, compra de cada leg, comisiones, conversión, selección adversa y pérdida de unwind; exige atomicidad o ruta de salida verificada | Matriz contractual verificada y ruta de ejecución/unwind demostrada con libros actuales |
| Market making | Cotizaciones post-only ajustadas a tick, sesgo por inventario, cooldown, tamaño vendible, probabilidad de fill y costo maker explícitos | Modelo validado de cola/fills/markout, inventario reconciliado y soporte del venue |
| Wallet intelligence | Reconstrucción de compras, ventas, fees y redenciones; depósitos/retiros excluidos del PnL; cohorte elegida antes de observar su futuro | Cashflows completos, valor liquidable de posiciones y validación prospectiva de cohorte |
| Weather | Estación/unidad/período validados por el adaptador, miembros recibidos y frecuencia del rango; no presume independencia del ensemble | Archivo de forecasts emitidos, mapeo contractual y calibrador temporal verificados |
| News / cross-market | Entidad/fuente primaria, publicación y primera observación, descarte de noticia reciclada y movimiento ya observado | Efecto calibrado por target; sin él sólo alerta manual |
| Forecasting | Límite inferior del intervalo de probabilidad menos ask, fee y selección adversa; verifica orden temporal del entrenamiento | Forecast/calibrador validado, contrato coincidente y libro fresco |
| Radar | Volumen anómalo con baseline comparable, ajuste de estacionalidad/acciones corporativas, catalizador y liquidez de salida | Datos legítimos de las clases vigiladas; sólo produce observación manual |

Un `PROPOSE` requiere revisión del core de riesgo. `expected_edge` es una estimación por unidad, no PnL realizado; en alertas manuales es `null` y el tamaño es cero. La vigencia vence según el insumo más antiguo, no se renueva al reevaluar. Disponibilidad desconocida/futura, libro viejo, campos ausentes o valores inválidos producen `ABSTAIN`.

La fórmula de sensibilidad de comisiones usa `shares × fee_rate × price × (1-price)`, conforme a la [documentación oficial consultada](https://docs.polymarket.com/trading/fees). La tasa debe venir del mercado y momento correspondientes; no se presume una tasa histórica verificada. La aproximación sin redondeo de la simulación no replica el matching del venue. Rebates no confirmados no aumentan ingresos.

La investigación histórica utiliza siete hipótesis sencillas de favorito/underdog, distintas de siete familias de plugins. No demuestra que weather, wallets, noticias o market making hayan tenido cobertura real ni rentabilidad. Sus resultados y pérdidas están en [VALIDATION.md](VALIDATION.md).

Las pruebas `tests/unit/test_strategies.py` comprueban escenarios sintéticos identificados: costos, cobertura incompleta, insolvencia de una leg, inventario, timestamps, fuentes ausentes y abstenciones. Los ejemplos rentables prueban aritmética y controles, no existencia de arbitraje en el mercado.
