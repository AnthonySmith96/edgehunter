# Revisión prospectiva de JEV — 18 de septiembre de 2026

JEV sí tiene inferencias y operaciones paper registradas. Con la evidencia disponible no supera al mercado en calidad de probabilidades ni demuestra una mejora financiera: de 100 SIM_USD conserva **64.3549**, con **−35.6451** realizado. Hay trabajo útil en la integración, pero arriesgar más todavía no está respaldado por esta sesión.

## Corte y reproducción

La auditoría usa un snapshot de 125 eventos en `data/btc_jev/events.jsonl`: 36 oportunidades, 33 decisiones, 20 fills, 20 liquidaciones, 13 abstenciones, dos errores y un rechazo. Las decisiones abarcan 19:12:45–22:02:23 UTC, el 18 de septiembre. El reporte vivo anterior se actualizó a las 22:04 UTC. `btc_challenger_live.json` pertenece a otro experimento: su corte es 16:09 UTC, registra 25 predicciones y cero fills; no representa la actividad posterior de JEV.

Se cotejaron las **33** decisiones con resultados oficiales obtenidos mediante GET público y conservados en `data/btc_jev/review_outcomes_20260918.json` a las 22:39:27 UTC. No se hicieron nuevas inferencias para esta revisión. El [snapshot calculado](jev_review_20260918.json) conserva hashes SHA-256 de ambas entradas, datos por operación y alcance de cada métrica.

```powershell
python scripts/audit_btc_jev.py --outcomes data/btc_jev/review_outcomes_20260918.json
```

El [script offline](../scripts/audit_btc_jev.py) imprime JSON. `--output ruta_nueva.json` crea un archivo y rechaza sobrescrituras. Valida etiquetas, mercados coincidentes, tiempo de obtención posterior al vencimiento y conflictos con liquidaciones; no rellena desenlaces ausentes. Excluye respuestas tardías o fallidas y resultados no binarios del cálculo de probabilidades. También admite los eventos `OUTCOME` de los futuros diarios. El JSON conserva las 33 filas emparejadas para recalcular las métricas aun sin acceso al directorio local `data/`. No modifica diarios, saldo, modelo ni configuración.

## Resultado financiero y probabilidades

| Indicador | Resultado |
|---|---:|
| Operaciones liquidadas | 20 |
| Ganadas / perdidas | 12 / 8 |
| Acierto del lado comprado | 60.00% |
| Ganancia total en operaciones ganadoras | +37.0641 SIM_USD |
| Pérdida total en operaciones perdedoras | −72.7092 SIM_USD |
| Ganancia media / pérdida media | +3.0887 / −9.0887 SIM_USD |
| PnL realizado | −35.6451 SIM_USD |
| Equity realizado final / máximo observado | 64.3549 / 104.3667 SIM_USD |
| Caída máxima desde un máximo de equity realizado | 40.92% |

El 60% de aciertos convive con pérdidas porque las ganancias por acierto son menores que las pérdidas por fallo. La probabilidad media asignada al lado comprado fue **81.55%**, frente al **60%** observado. El costo medio por cuota fue 0.6611 SIM_USD, pero las cuotas y los tamaños varían: ese promedio no es una tasa única de equilibrio para toda la cartera.

Para comparar probabilidades se usa exactamente el mismo conjunto de desenlaces y las probabilidades de mercado registradas en cada decisión (asks normalizados y redondeados). Menor Brier y menor log loss son mejores; ninguno acredita ejecución rentable por sí solo.

| Cohorte y métrica | JEV | Mercado |
|---|---:|---:|
| 33 decisiones, aciertos de dirección | 26/33 (78.79%) | 26/33 (78.79%) |
| 33 decisiones, Brier | 0.169533 | **0.130502** |
| 33 decisiones, log loss | 0.513929 | **0.412678** |
| 20 decisiones con fill liquidado, aciertos de dirección | 15/20 (75%) | 15/20 (75%) |
| 20 decisiones con fill liquidado, Brier | 0.220985 | **0.150583** |
| 20 decisiones con fill liquidado, log loss | 0.667222 | **0.469859** |

Empatan en cantidad de aciertos, pero no en todos los casos: difieren en dos direcciones. El acierto de dirección también difiere del acierto del lado comprado: el sistema puede comprar un lado minoritario si estima ventaja respecto al precio. Por eso 15/20 pronósticos de dirección correctos no equivalen a las 12/20 compras ganadoras.

JEV expresó confianza de al menos 90% en **25/33** decisiones. En esas 25, la confianza media fue **94.44%** y acertó **84%**. Es evidencia descriptiva de exceso de confianza en esta sesión; no basta para estimar una corrección estable. No se seleccionaron nuevos pesos, temperaturas, umbrales ni tamaños usando estos resultados.

## Cambio de tamaño y evidencia histórica

El campo `kelly_fraction` aparece por primera vez a las **21:37:22 UTC**. Antes hay 18 fills, 11 ganados y **−35.6293**. Después hay solamente dos fills liquidados, uno ganado y **−0.0158**. Esta separación se infiere del esquema del journal; el experimento mantuvo el mismo nombre y no conservó una huella de política por cada decisión. Dos operaciones no permiten concluir que Kelly mejoró o empeoró el modelo.

El reporte [jev_holdout_comparison.json](jev_holdout_comparison.json) sí contiene una comparación favorable a JEV: Brier 0.0226 frente a 0.0713 del modelo aprendido y 0.0681 del mercado. Su alcance es mucho menor que el nombre del archivo puede sugerir:

- El [script](../scripts/compare_jev_holdout.py) toma las **primeras diez filas elegibles** desde el inicio del holdout, no una evaluación completa de las 5,028 filas de prueba del experimento histórico. Son diez ventanas consecutivas del 12 de septiembre, 06:50–07:40 UTC, equivalentes a 50 minutos de contratos.
- Ambos modelos acertaron 10/10. El resultado favorable de calibración en esta muestra no se repite en las 33 predicciones prospectivas observadas.
- `stake = 10` se usa como **diez cuotas**, cuyo desembolso depende del precio; no como una apuesta fija de diez dólares. El +15.55 histórico no es directamente comparable con el saldo paper de 100.
- Usa umbrales distintos: 0.06 para el modelo aprendido y 0.04 para JEV. Suma 0.03 de deslizamiento por cuota, pero no modela la comisión de las ejecuciones prospectivas, reservas de capital ni profundidad de libro.
- El histórico ya se abrió y utilizó para comparación. No debe volver a presentarse como un conjunto intacto para seleccionar mejoras y probarlas después sobre los mismos resultados.

## Decisión de mejora

Conviene mantener el aprendizaje prospectivo con una política identificable y corregir primero la medición y ejecución. Esta revisión conserva el prompt y las probabilidades; no atribuye ganancias futuras a cambiar el riesgo.

La siguiente evaluación útil debe recoger también las abstenciones, comparar JEV y mercado sobre los mismos contratos, controlar frescura y tiempo antes del fill, y registrar la política utilizada. Una eventual corrección de confianza debe ajustarse con datos de desarrollo y evaluarse en un período posterior sin escoger sus parámetros sobre estas 33 decisiones. La sesión es demasiado corta para declarar una ventaja repetible o recomendar más capital.

Los PnL de este documento reproducen el journal existente. No certifican profundidad ejecutable ni comisiones reales; cambiar ahora el motor no reescribe esos fills históricos. Las comprobaciones de código añadidas durante la revisión se documentan separadamente de estos resultados anteriores al cambio.

## Cambios implementados y estado de aplicación

Se corrigieron el mínimo que elevaba una apuesta Kelly por encima de su cálculo, el presupuesto cero convertido en cinco dólares, los valores de `.env` ignorados por el CLI y la aceptación de datos o respuestas tardías. El motor vuelve a comprobar precio y profundidad después de la inferencia; registra desenlaces de abstenciones y conserva la versión devuelta por JEV. Los reportes nuevos se escriben de forma atómica y la parada normal publica `STOPPED`. Detalles en [JEV_EXPERIMENT.md](../docs/JEV_EXPERIMENT.md).

Se mantuvieron el prompt, las probabilidades y los umbrales actuales. Son correcciones de ejecución y evaluación, no una mejora predictiva ni una promesa de mayor rentabilidad. Los eventos nuevos llevan `execution_revision=jev_execution_v2_fresh_inputs_kelly_cap`; el historial conserva su revisión anterior y su saldo.

El proceso JEV estaba detenido durante la revisión; no se arrancó ni se hicieron inferencias pagadas. Los cambios se aplicarán al siguiente inicio. `btc_jev_live.json` conserva el corte histórico, incluida su etiqueta antigua de estado, para no presentar una nueva corrida inexistente.

Validación final: **311 pruebas aprobadas** en 54.20 segundos; mypy sobre los 53 archivos de `src`, Ruff y `git diff --check` correctos. Las regresiones cubren Kelly bajo el mínimo, presupuesto cero, respuestas tardías, datos incompletos, precio cambiado, abstenciones y resoluciones decimales/no binarias. Los hashes confirman que el journal histórico sigue intacto y las 33 filas del snapshot reproducen ambos Brier.
