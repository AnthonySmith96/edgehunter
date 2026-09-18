# Límites y evidencia de seguridad

La versión actual no implementa firma, wallet ni envío de órdenes financieras reales. La CLI admite `paper` y `shadow`; el motor de riesgo admite ejecución simulada `PAPER`/`REPLAY` y rechaza identidades o modos reales. Una variable de entorno o una aprobación PocketBase no habilita una capacidad ausente.

`HardEnvelope` es una estructura congelada dentro del proceso para las pruebas de investigación. No es una política inviolable frente al propietario del equipo ni un envelope de host desplegado. Las plantillas de Linux proponen cuentas separadas y restricciones systemd; esas restricciones no han sido instaladas ni verificadas en un servidor.

## Control humano

PocketBase usa una cuenta de servicio `daemon` y un superuser humano designado. Las API rules limitan al daemon; los hooks validan también las peticiones del superuser. Las aprobaciones deben conservar payload, hash, revisión, importe, nonce y expiración. El cliente no puede fijar `decided_by` ni `decided_at`: el servidor los escribe junto con el registro de auditoría en una transacción.

La transición humana ocurre una sola vez desde `PENDING/WAITING`; una decisión vencida, invalidada o consumida no admite otra aprobación. El daemon no puede aprobar, modificar el actor, elevar su rol ni editar auditoría. Una ejecución necesita aprobación y no puede retroceder desde estados terminales. Las retransmisiones terminales idénticas son idempotentes. Las proyecciones exigen una revisión creciente y tienen identidad de negocio única.

El endpoint de identidad del control devuelve sólo `actor_id`, requiere autenticación del daemon y vuelve a comprobar revocación. Su implementación usa el enrutamiento y middleware documentados por [PocketBase](https://pocketbase.io/docs/js-routing/). El pin del actor en el estado local permite detectar cambios; la confianza inicial sigue dependiendo de conectarse a la instancia correcta por loopback o HTTPS.

El journal verifica por separado sus propuestas de parada, actor esperado, payload, nonce y TTL, y registra el consumo en la misma transacción que aplica el comando. Las aprobaciones financieras del simulador llevan actor `SIMULATED_RESEARCH_ACTOR`; no equivalen a una autorización humana de fondos reales. Un diccionario con campos de aprobación no prueba autenticación: debe provenir de la conexión de control verificada.

Los hooks protegen las rutas API implementadas. Un propietario root o superuser con capacidad para cambiar colecciones, hooks o archivos del servidor puede modificar el sistema. Los hashes de snapshots detectan cambios respecto de un manifest conservado; no son una firma que autentique un manifest también reemplazado por un atacante.

## Secretos y red

El instalador descarga la versión fijada en `pocketbase/releases.json` desde la publicación oficial, verifica SHA-256 del ZIP y compara el ejecutable con el contenido del ZIP verificado. No acepta actualización silenciosa. Las migraciones se ejecutan desde el repositorio con automigración deshabilitada.

El bootstrap genera credenciales aleatorias y las guarda fuera del repositorio. En la experiencia local, ambas identidades quedan bajo el mismo usuario del sistema operativo: esta comodidad de desarrollo no proporciona separación de privilegios entre procesos de ese usuario. En Linux, las plantillas separan `edgehunter` y `edgehunter-pb`; el daemon debe recibir sólo un JSON con `email` y `password`, nunca la credencial humana ni el directorio PocketBase.

El cliente PocketBase rechaza HTTP remoto y credenciales incrustadas en URL. El binario local escucha sólo en `127.0.0.1`. Los enlaces de registros no incluyen tokens. Los diagnósticos de migración permanecen en el directorio restringido; no publicar bases, logs o snapshots sin revisión. `.local`, `.tools`, `.env`, bases y logs están ignorados por Git.

En los proxies propuestos, `Authorization` se conserva para `/api/`. Basic Auth, cuando se usa el perfil web, sólo protege los recursos del administrador `/_/`; no constituye MFA ni protege adicionalmente toda la API. El perfil web queda bloqueado hasta configurar y probar MFA, recuperación por correo y protección del origen. La red de administración/VPN y sus peers todavía no existen como resultado de esta entrega.

## Comprobaciones y pendientes

Las pruebas con un proceso PocketBase real verifican migración repetida, separación de actores, revocación, restricciones de campos, carrera de doble aprobación, expiración, auditoría, persistencia y restore aislado sin reaprobación. Las pruebas del renderizador verifican entradas, rutas de escritura y límites de las plantillas. El estado global de pruebas está en [VALIDATION.md](VALIDATION.md).

No se han certificado endurecimiento del host, penetración remota, MFA, recuperación de correo, navegación móvil, monitor externo, backup cifrado fuera del equipo ni disponibilidad 24/7. Las alertas de la experiencia local llegan a archivos del sink; no acreditan entrega por correo externo. El heartbeat local y las políticas de reinicio propuestas no equivalen a un monitor independiente ni a un watchdog integrado con systemd.
