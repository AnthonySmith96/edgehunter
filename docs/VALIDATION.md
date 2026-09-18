# Validación de investigación

Se realizaron dos experimentos. El primero perdió **5.3641 unidades ficticias** fuera de muestra. El segundo ganó **1.7987** en el escenario base y perdió **6.4038** en el conservador. No hay ventaja financiera demostrada. Se preservaron fechas, modelos congelados, resultados negativos y limitaciones; no se volvieron a seleccionar modelos contra un holdout ya abierto.

## Experimento 2 y estado de entrega

Después del fracaso inicial se declararon los datos ya inspeccionados como desarrollo contaminado para un segundo experimento. Se compararon 18 especificaciones de calibración regularizada en ventanas temporales; ninguna superó el gate de desarrollo. Se congeló el mejor candidato para auditoría antes de descargar una ventana nueva y fija del 1 al 16 de septiembre de 2026.

La ventana nueva produjo 217 observaciones y 33 operaciones. El resultado base fue +1.7987 sobre 1,000 iniciales, con IC95 descriptivo [-30.4311, 30.3200]. El escenario optimista dio +5.0025 y el conservador -6.4038. Se informan 25 especificaciones totales entre ambos experimentos; no hay afirmación de significancia familiar ni rentabilidad robusta. Los detalles están en [research_calibrated.md](../reports/research_calibrated.md).

Las siete familias de plugins son distintas de las siete hipótesis del primer experimento. El candidato prospectivo tiene horizonte limitado de 23–25 horas, libros actuales y costos explícitos distintos del histórico; etiquetas faltantes usan `other` y se registra ese fallback. Esta adaptación no hereda el resultado histórico como validación. El modelo permanece congelado; sus límites probabilísticos son heurísticos, no intervalos calibrados.

Reproducción del segundo experimento: `.venv\Scripts\python.exe scripts/research_calibrated.py --offline`. Ambas reproducciones offline fueron ejecutadas y devolvieron los mismos resultados. Las pruebas de software y la aceptación de esta entrega están en [ACCEPTANCE_REPORT.md](../ACCEPTANCE_REPORT.md).

## Experimento 1 preservado

| Comprobación | Evidencia |
|---|---|
| Registro previo | `data/research/hypothesis_registry.json`, escrito antes de descargar resultados |
| Catálogo | 736 eventos; primeras 32 entradas por ID en cada cuarto día entre junio y agosto de 2026 |
| Datos utilizables | 508 mercados/eventos, una observación por evento; 225 exclusiones de elegibilidad y 3 sin histórico oportuno |
| Splits | Train 248, validación 95, test 122, purgados 43; embargo de 24 horas y clusters disjuntos |
| Selección | `favorite_90`: train +4.2859, validación +10.0080; familia fija de 7 hipótesis, incluidas variantes perdedoras |
| Holdout base | 13 operaciones, PnL -5.3641, capital final 994.6359; IC95 por días [-29.1811, 10.3894] |
| Costos optimistas / conservadores | -4.5884 / -17.2203; el escenario conservador puede seleccionar menos operaciones válidas y cambiar la ocupación de capital |
| Walk-forward de desarrollo | Tres ventanas, selección solamente con labels maduros anteriores; suma de PnL de ventanas -67.5127; capital simulado reiniciado por ventana |
| Reproducción | Entradas HTTP crudas con SHA-256 y URL/fecha de consulta; dataset congelado y selector completo inmutables al reabrir test |

El IC es un bootstrap descriptivo por días, con sólo cuatro clusters negociados en el escenario base. No representa una probabilidad de que una estrategia sea rentable ni corrige toda dependencia entre días. La familia de hipótesis y la separación temporal controlan parte de la selección; no se afirma significancia estadística ni promoción a producción.

Se aplicaron máximo 10 unidades de costo por evento, máximo 100 bloqueadas simultáneamente, mínimo supuesto de 5 shares, slippage de 0.01/share y tasa de fee estimada 0.07 en el escenario base. El cash se descuenta al entrar y se libera al liquidar; una posición abierta no permite gastar su reserva otra vez. La liquidación se aproximó con `closedTime + 24h`.

Limitaciones materiales: catálogo retrospectivo y sólo mercados cerrados; metadatos Gamma actuales sin historial de revisiones; timestamp de precio más 60 segundos de demora inferida; ausencia de `first_seen_at` histórico, asks ejecutables, libro/cola, fees históricos y tiempo real de redención. El drawdown usa costo de posiciones abiertas y liquidaciones, no mark-to-liquidation continuo. Por estas limitaciones, aun un resultado positivo sería exploratorio.

Para reproducir sin consultas de red desde la raíz:

```powershell
.venv\Scripts\python.exe scripts/research_historical.py --offline
.venv\Scripts\python.exe -m pytest tests/unit/test_strategies.py tests/unit/test_research.py -q
.venv\Scripts\python.exe -m mypy src/edgehunter/strategies src/edgehunter/research
```

El programa rechaza un cache alterado y otra hipótesis/dataset contra el holdout ya abierto. Los resultados completos, trades de cada variante, calibración, exclusiones y escenarios están en `reports/research_historical.json`; el resumen está en `reports/research_historical.md`.

La etapa prospectiva registrada requiere 90 días, 200 eventos, aproximadamente 30 clusters de día, costos conservadores, books/fees y primera observación registrados, más 72 horas de estabilidad. Son mínimos de diseño propuestos, no garantía de potencia estadística. No se completaron en esta sesión. El experimento 1 conserva **NO_EDGE** y el experimento 2 **EXPLORATORY_POSITIVE**; ambos mantienen `financial_edge_proven=false` y `live_gate=BLOCKED`.
