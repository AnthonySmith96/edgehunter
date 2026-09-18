# Intervenciones externas

La demo y la reproducción histórica local no necesitan cuentas, depósitos ni claves adicionales. Las tareas de instalación, pruebas y documentación se ejecutaron en el proyecto.

| Cuándo | Acción imprescindible | Motivo |
|---|---|---|
| Sólo si se desea correo externo | Proporcionar cuenta/clave de Brevo, remitente verificado, destinatario y autorización de envío | Hoy las notificaciones llegan al sink local; no hace falta correo para simular |
| Sólo si se desea inteligencia pagada | Habilitar personalmente acceso a [Typesafe](https://docs.typesafe.ai/api) o [WeatherNext](https://developers.google.com/weathernext/guides/access-forecast) y definir presupuesto | Las pruebas de adaptadores no confirman acceso ni costos; presupuesto actual cero |
| Sólo para acceso remoto | Identificar host/dominio autorizados, acceso administrativo y destino de respaldo; realizar MFA/identidad cuando corresponda | No se proporcionó servidor ni se desplegó VPS; las plantillas requieren instalación y pruebas de host |

No se solicita dinero real: el proyecto no tiene esa capacidad y no superó validación financiera. Las carencias de software están en `IMPLEMENTATION_STATUS.md`; obtener credenciales no las resuelve automáticamente.

El problema local del reloj ya fue resuelto: el servicio `W32Time` está activo con arranque automático, la directiva usa `time.windows.com,0x8` y el daemon verifica la hora del venue en cada ciclo. No requiere una acción manual mientras `edgehunter status` muestre `clock.status=HEALTHY` y `ready_for_new_paper_risk=true`.
