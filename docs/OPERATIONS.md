# Operación local

Esta entrega opera con fondos ficticios. `shadow` consulta fuentes públicas y conserva decisiones sin ejecutar. `paper` puede ejecutar un candidato de investigación cuando existe un modelo congelado y se cumplen sus controles; si falta el modelo o un insumo necesario, se abstiene. Ese candidato no está certificado como rentable en mercado real. `demo` ejecuta un escenario sintético completo y conserva esa etiqueta en sus resultados. La evaluación histórica es otra evidencia, descrita en [VALIDATION.md](VALIDATION.md).

Desde la raíz del proyecto, en PowerShell:

```powershell
.\scripts\bootstrap.ps1
.\.venv\Scripts\python.exe -m edgehunter status
```

El bootstrap crea el entorno aislado, instala las versiones del lockfile, verifica PocketBase y sus migraciones, ejecuta `doctor` y arranca el daemon local en segundo plano. `scripts/pocketbase_setup.py` acepta ejecución sin argumentos: usa `.local/pocketbase`. `--data-dir RUTA` permite preparar otra instancia dedicada. No reemplaza identidades de una base existente si se perdieron sus credenciales.

Para ejecutar la demostración y cerrarla al terminar:

```powershell
.\scripts\bootstrap.ps1 -Demo
```

Cada demo usa `.local/demo/<run-id>/`; su correo se escribe en un sink local y su aprobación humana es una simulación identificada. La instancia PocketBase de la demo se cierra en el bloque de limpieza. `reports/demo.json` apunta al último resultado.

## Control diario

```powershell
.\.venv\Scripts\python.exe -m edgehunter doctor --redact
.\.venv\Scripts\python.exe -m edgehunter sources probe
.\.venv\Scripts\python.exe -m edgehunter strategies evaluate
.\.venv\Scripts\python.exe -m edgehunter approvals list
.\.venv\Scripts\python.exe -m edgehunter reconcile --read-only
.\.venv\Scripts\python.exe -m edgehunter report --period all
```

`status` muestra estado, edad del heartbeat, avance por tarea, errores y URL del administrador. Un heartbeat con más de 90 segundos pasa a `STALE_OR_STOPPED`; revisar también el avance de ingestión y control. `doctor` describe el entorno y los bloqueos; no acredita preparación para producción. La reconciliación es local y contable: no compara saldos con una cuenta de trading.

Las credenciales locales se guardan en `.local/pocketbase/credentials.json`, dentro de un directorio restringido por ACL en Windows o modo `0700` en Linux. El bootstrap muestra su ruta, no contraseñas. El administrador se abre en la URL loopback anunciada por `status`, con puerto elegido al arrancar. No exponer esa instancia local a Internet.

Para detener o pausar:

```powershell
.\.venv\Scripts\python.exe -m edgehunter pause --reason "Revisión del operador"
.\.venv\Scripts\python.exe -m edgehunter stop
.\.venv\Scripts\python.exe -m edgehunter status
.\.venv\Scripts\python.exe -m edgehunter cancel-open --scope edgehunter
```

`pause` persiste un halt en el journal. `stop` solicita una parada ordenada mediante un archivo de control; esperar `STOPPED` antes de intervenir datos. `cancel-open` es exclusivamente del simulador y requiere que el daemon haya liberado el lock; una orden `UNKNOWN` o `SUBMITTING` necesita reconciliación. No se matan procesos Python/PocketBase de otros proyectos.

Para una captura acotada en primer plano, una vez detenido el daemon anterior:

```powershell
.\.venv\Scripts\python.exe -m edgehunter run --mode shadow --duration 60 --market-limit 5
```

Las opciones `--state-dir` de los comandos operativos deben señalar el mismo directorio usado al arrancar. El predeterminado es `.local/runtime`. `--root` es una opción global y se coloca antes de `run`, `status` u otro subcomando.

## Aprobaciones y degradación

El daemon propone; el superuser designado decide editando sólo `human_decision` a `APPROVED` o `REJECTED` en el administrador nativo. Los hooks sellan actor y hora, conservan el payload y generan auditoría transaccional. El cliente obtiene el actor designado por `GET /api/edgehunter/control-identity`, accesible sólo al daemon autenticado y no revocado. Ese endpoint devuelve únicamente el identificador del actor.

La ruta de consumo de comandos se limita a `PAUSE_NEW_RISK` y `EMERGENCY_HALT`. El journal exige una propuesta local coincidente, actor esperado, hash, nonce, revisión y vigencia; un comando ya consumido no se vuelve a aplicar. Esto no constituye un canal para activar trading real ni aprobar transferencias.

Ante pérdida del control, el lease caduca y el motor de riesgo rechaza riesgo nuevo. Ante datos incompletos, reloj degradado, costes no certificados o contratos sin certificar, el estado y las decisiones conservan la causa de abstención. Ante disco bajo se persiste halt y se detiene el daemon. Un halt requiere revisar la causa; esta entrega no incluye un comando general de reanudación que lo borre.

## Respaldo y recuperación

El journal y PocketBase son respaldos distintos. Para obtener un punto de recuperación coordinado, detener primero el daemon y su instancia dedicada; verificar que terminaron y conservar los dos respaldos junto con la revisión del código y sus manifests.

```powershell
.\.venv\Scripts\python.exe -m edgehunter backup
.\.venv\Scripts\python.exe scripts/pocketbase_backup.py .local/backups/pb-ejemplo --data-dir .local/pocketbase
.\.venv\Scripts\python.exe -m edgehunter restore --dry-run .local/backups/journal-FECHA.db
.\.venv\Scripts\python.exe scripts/pocketbase_restore.py .local/backups/pb-ejemplo --destination .local/restore-pb-ejemplo
```

Reemplazar las rutas de ejemplo por las emitidas en la ejecución. Ambos motores usan la API de backup SQLite, incluyendo el estado comprometido que puede residir en WAL. El script PocketBase sólo soporta las colecciones JSON del proyecto: rechaza archivos en `pb_data/storage`. No detecta por sí solo una instancia iniciada por otro proceso; la parada previa es un requisito operativo.

La restauración PocketBase es dry-run por defecto. `--restore-isolated` materializa una copia únicamente en un directorio nuevo; `--credentials-source RUTA` puede aportar por separado las credenciales originales. Sin ellas, una base restaurada no se vuelve a sembrar con identidades nuevas. Se validan checksum, integridad SQLite, tablas esperadas, versión PocketBase y revisión exacta de hooks/migraciones. La copia conserva decisiones y auditoría, y deja `RESTORE_REQUIRES_RECONCILIATION.json`.

Cuando el daemon administra esa instancia local restaurada, detectar el marcador persiste un halt en el journal. Esto no verifica por sí solo un PocketBase remoto suministrado por URL; para esa topología la reconciliación y el arranque en shadow siguen siendo requisitos operativos.

La validación del journal no reemplaza el archivo activo. Conservar el journal correspondiente, verificar consumos y estados desconocidos en el ambiente aislado y reconciliar antes de promoverlo. No usar una copia vieja de PocketBase para producir intenciones financieras nuevas. Los snapshots contienen datos sensibles y hashes de contraseñas; no son respaldos cifrados ni copias externas. Destino externo, cifrado y recuperación del host completo siguen pendientes.
