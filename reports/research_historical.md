# Investigación histórica EdgeHunter

Estado: **NO_EDGE**, exploratorio; live bloqueado.

Muestra: 508 mercados/eventos; catálogo: 736 eventos; splits: {'train': 248, 'validation': 95, 'test': 122, 'purged': 43}.
Hipótesis elegida antes de abrir test: `favorite_90`; pasó train/validation: True.

| Escenario | Operaciones | PnL ficticio neto | Capital final (inicial 1000) | IC95 por días |
|---|---:|---:|---:|---|
| optimistic | 13 | -4.5884 | 995.4116 | [-29.1029158053, 12.328886343299999] |
| base | 13 | -5.3641 | 994.6359 | [-29.181080432925, 10.389368640775002] |
| conservative | 13 | -17.2203 | 982.7797 | [-38.11990319825, 2.4726962999999995] |

Los costos son supuestos de sensibilidad, no costos históricos comprobados. Precios históricos sin libro ni disponibilidad histórica verificada: este resultado NO prueba fills realizables ni rentabilidad futura.

Se registraron siete hipótesis, se eligió una con train/validation y se abrió test una vez. No se ajustó la estrategia después de ver test. `data/research/holdout_opened.json` impide reutilizarlo con otra selección.

El protocolo prospectivo requiere datos observados, 90 días, 200 eventos, 30 días independientes aproximados y estabilidad operativa de 72 horas; esos mínimos son diseño propuesto, no garantía estadística. No se han completado durante esta sesión.

Detalle, operaciones individuales, calibración, exclusiones, hashes y raw inputs: `research_historical.json` y `../data/research/`.
