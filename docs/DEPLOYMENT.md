# Preparación de despliegue

Estado: **plantillas preparadas; servidor remoto no desplegado**. Esta entrega no crea cuentas de host, certificados, reglas de firewall, peers VPN, DNS ni servicios del sistema. El perfil propuesto arranca exclusivamente en `SHADOW`, sin capacidad financiera real.

El renderizador genera un paquete revisable sin modificar servicios. Su ejecución predeterminada es dry-run:

```powershell
.\.venv\Scripts\python.exe deploy/install.py
.\.venv\Scripts\python.exe deploy/install.py --host control.example.invalid --pb-port 8097 --admin-bind 127.0.0.1 --profile vpn --output .local/deploy-review
.\.venv\Scripts\python.exe -m edgehunter deployment preflight
```

`--output` exige un directorio nuevo y escribe dos unidades systemd y un archivo Nginx. Reemplazar host, dirección administrativa, puerto, arquitectura y raíz de release después de inspeccionar el servidor autorizado. El resultado incluye hashes de los archivos y pendientes de instalación. Las pruebas locales comprobaron ambos perfiles, IPv6 privado, rechazo de entradas inválidas, ausencia de escrituras en dry-run y rechazo de sobrescritura. `nginx -t` y `systemd-analyze verify` no estaban disponibles en el entorno Windows; su sintaxis efectiva queda pendiente de validación en Linux.

## Preflight de host

Antes de instalar, registrar versión del sistema, arquitectura, discos, usuarios, puertos ocupados, Nginx existente, nombres DNS, certificados, firewall, red VPN y política de backups. Elegir un puerto PocketBase sin colisión; el valor del ejemplo no constituye una reserva. Conservar el estado de servicios ajenos al proyecto.

La estructura propuesta es:

| Ruta | Propietario/acceso previsto | Uso |
| --- | --- | --- |
| `/srv/edgehunter/releases/<revision>` | root, lectura de servicios | Código, entorno y binario verificados |
| `/srv/edgehunter/current` | symlink administrado por root | Release seleccionada |
| `/var/lib/edgehunter` | `edgehunter`, modo `0700` | Journal, archivo de mercado, heartbeat y sink |
| `/var/lib/edgehunter-pb` | `edgehunter-pb`, modo `0700` | PocketBase y credenciales originales |
| `/etc/edgehunter/daemon.json` | root:`edgehunter`, modo `0640` | Sólo `email` y `password` de servicio |
| `/etc/edgehunter/pocketbase.env` | root, modo `0600` | `EDGEHUNTER_PB_HUMAN_EMAIL` designado |

Preparar dependencias desde `uv.lock`, el binario Linux correspondiente y migraciones antes de endurecer la release como sólo lectura. El aprovisionamiento debe conservar la identidad existente; no ejecutar setup de desarrollo contra una base de otro proyecto. No dejar `EDGEHUNTER_PB_BOOTSTRAP` en el entorno de servicio. La credencial humana no debe estar en `daemon.json`.

## Servicios y acceso

`edgehunter-pocketbase.service` escucha en loopback con el puerto elegido y usa el usuario separado `edgehunter-pb`. `edgehunter-daemon.service` usa `edgehunter`, se conecta por HTTP loopback y sólo puede escribir su directorio de estado. Ambos tienen reinicio con espera, límite de intentos, recursos acotados y restricciones de privilegios. No incluyen `WatchdogSec`: el daemon no implementa el protocolo `sd_notify`.

El arranque externo usa el contrato:

```text
edgehunter --root /srv/edgehunter/current run --mode shadow --state-dir /var/lib/edgehunter --pocketbase-url http://127.0.0.1:8097 --daemon-credentials /etc/edgehunter/daemon.json
```

La identidad humana se consulta por API autenticada y se fija en el estado local; no requiere login administrativo del daemon. Mantener el estado al cambiar de release. Un cambio de identidad requiere revisión, no reemplazo automático del pin.

El perfil `vpn` limita Nginx a la dirección administrativa elegida y mantiene TLS. Sólo una red privada y peers revisados hacen efectivo ese aislamiento: generar la plantilla no configura WireGuard. El perfil `web` escucha públicamente y permanece bloqueado hasta probar MFA PocketBase, SMTP y recuperación, protección del origen y accesos desde los dispositivos autorizados. Basic Auth sólo cubre `/_/`; `/api/` conserva la autenticación de PocketBase. Ambos perfiles mantienen SSE sin buffering en `/api/realtime` y excluyen query strings del formato de access log.

No se entrega un firewall genérico porque depende de las reglas y servicios existentes. El resultado esperado es: puerto PocketBase inaccesible externamente; SSH limitado a administración; acceso TLS sólo por la vía administrativa seleccionada. Debe probarse desde otra máquina, incluyendo una ruta no autorizada.

En el host destino, antes de habilitar servicios, ejecutar las herramientas de validación disponibles contra los archivos ya instalados en su contexto:

```sh
systemd-analyze verify /etc/systemd/system/edgehunter-pocketbase.service /etc/systemd/system/edgehunter-daemon.service
nginx -t
```

Estas comprobaciones necesitan usuarios, ejecutables, certificados e inclusiones reales del host. No bastan para demostrar aislamiento ni acceso desde celular.

## Cutover, recuperación y rollback

1. Conservar la release anterior y obtener snapshots coordinados del journal y PocketBase, con el daemon detenido y hashes registrados.
2. Preparar la nueva release y probar migraciones en una copia aislada. Una diferencia de versión, hooks o migraciones requiere revisión explícita del restore.
3. Preparar identidades y permisos separados; validar Nginx y systemd. Cifrar los respaldos antes de copiarlos a un destino externo autorizado.
4. Arrancar PocketBase dedicado, comprobar autenticación, identidad designada y permisos de daemon. Arrancar `SHADOW`; verificar avances de control e ingestión, halt persistido y estados de abstención.
5. Probar acceso y pérdida de control, reinicio, parada ordenada, límites de restart, alertas desde un monitor externo y recuperación desde la copia externa.

El cambio de `/srv/edgehunter/current` puede ser atómico una vez completadas las verificaciones. Un rollback de código no revierte operaciones ni autoriza restaurar una base vieja sobre la activa. Si el esquema no es compatible, mantener la parada y restaurar en aislamiento; preservar nonces, consumos y auditoría. La guía local de snapshots y sus límites está en [OPERATIONS.md](OPERATIONS.md).

La prueba local de backup/restore conserva decisiones consumidas y rechaza datos alterados. No mide pérdida total del host ni acredita RPO/RTO remoto. Faltan host y dominio autorizados, MFA/correo, monitor independiente, destino externo de respaldo y pruebas remotas. El preflight reporta esos pendientes; no promueve el proyecto a producción.
