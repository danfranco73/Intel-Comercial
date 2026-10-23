# Inspección acotada sobre el ODBC existente

## Diagnóstico completo con una sola ejecución

Para evitar el intercambio manual de resultados, se preparó un colector Windows
en `scripts/windows/collect_chess_diagnostics.py` y un lanzador PowerShell.
`scripts/receive_chess_diagnostics.py` entrega ese lanzador y recibe un único
informe a través de Tailscale. Es una transferencia temporal de diagnóstico,
no un gateway de Chess ni una tarea recurrente.

El receptor se vincula sólo a la IP Tailscale de la Mac, acepta únicamente la IP
de PC-Techstation y una ruta aleatoria por sesión, y se cierra al recibir el
informe o al vencer una hora. No sirve archivos arbitrarios ni expone informes.
Los archivos recibidos quedan bajo `private/odbc/`, con permisos locales restrictivos.

El bloque Windows usa `C:\Python314\python.exe`, lee estado/último resultado y
rutas de las dos tareas de TMA, calcula hashes de sus scripts y extrae sólo fechas
y estados OK/ERROR de las últimas líneas de logs. No incluye código fuente,
mensajes de error originales, contraseñas, secretos, argumentos completos ni datos
de ventas. Usa el DSN `CHESS_CODENOA` y `CHESS_ODBC_PWD` existente; no modifica
variables persistentes ni importa/ejecuta los scripts de sincronización.

La lectura ODBC se limita al catálogo `PUB`, conocido por el puente de stock:
hasta 1000 nombres de tablas/vistas y columnas/claves de hasta 12 candidatas
seleccionadas por nombre. No certifica que esas candidatas sean tablas de ventas.
Presupuesto máximo: 25 llamadas de catálogo, 120 columnas y 20 componentes de
clave por objeto, timeout por consulta de diez segundos y 120 segundos totales.
Si vence el plazo, sólo se termina el proceso de diagnóstico recién creado y se
envía el informe parcial guardado hasta ese momento. Las tareas de TMA siguen
intactas. Los límites de consumo no limitan el escaneo interno del catálogo.

Ejemplo de inicio del receptor en la Mac (usar una carpeta nueva en cada sesión):

```bash
.venv/bin/python scripts/receive_chess_diagnostics.py \
  --bind 100.100.214.121 --peer 100.83.8.28 \
  --output-dir private/odbc/diagnostico-sesion
```

El receptor imprime la URL temporal del bloque PowerShell. El usuario lo ejecuta
una sola vez en Windows. La entrega no requiere token del gateway ni acceso al
puerto 8765. Si falla el envío, el archivo `informe.json` queda en una carpeta
temporal de Windows cuya ruta se muestra al usuario. La ejecución real de
PowerShell/DataDirect aún requiere validación en esa PC; las pruebas locales
verifican el protocolo HTTP y el catálogo con un driver simulado.

## Inspector individual

Esta herramienta prepara el mapeo de IntelComercial sobre la conexión ya instalada
en PC-Techstation. No instala otro gateway, no cambia el DSN, no consulta ventas y
no habilita un sincronizador. El gateway actual permanece en `C:\ChessBI`.

## Preparación local sin red

```bash
.venv/bin/python scripts/inspect_chess_odbc.py
```

El modo predeterminado imprime un plan JSON y no carga secretos, `.env`, pyodbc,
MongoDB, ClickHouse ni el servidor de la aplicación.

## Inspección de catálogo en Windows

Prerequisitos: el Python con el que ya funciona pyodbc, el mismo DSN, usuario con
permisos efectivos de lectura y un esquema/prefijo autorizado por el responsable
de Chess. La conexión fue probada en el antecedente compartido; no hace falta
reinstalar OpenEdge, OpenVPN ni Tailscale.

Usar las variables del gateway existentes en la sesión/proceso autorizado:

- `CHESS_ODBC_USER`.
- `CHESS_ODBC_PASSWORD`.
- `CHESS_ODBC_DSN`, opcional: por defecto `CHESS_CODENOA`.

No copiar el token API ni las credenciales al repositorio. Las variables de otra
ventana PowerShell no se heredan automáticamente. El inspector no utiliza
`CHESS_API_TOKEN` porque se ejecuta en Windows junto al driver.

Ejemplo de plantilla, **sustituir esquema y prefijo por nombres verificados**:

```powershell
py scripts\inspect_chess_odbc.py --inspect-schema `
  --schema "ESQUEMA_CONFIRMADO" `
  --table-pattern "PREFIJO_CONFIRMADO%" `
  --max-tables 5 --max-columns 200
```

No ejecutar la plantilla literalmente. No se asume un esquema comercial `PUB`
ni tablas de ventas por nombre. Si sólo se conoce el catálogo técnico, primero
recuperar el diccionario o el alcance de exploración con el administrador.

El Python del antecedente ya tiene pyodbc 5.3.0. `requirements-odbc.txt` sirve sólo
si se prepara otro entorno aislado de herramientas; no requiere tocar el entorno
del gateway ni instalar dependencias web.

## Límites e interpretación

- Sólo llama a `tables`, `columns` y `primaryKeys`; no ofrece ejecución SQL.
- Esquema exacto; patrón de tabla explícito con parte del nombre.
- Por defecto consume hasta cinco objetos, 200 columnas y 200 componentes de PK
  por objeto, más una fila para detectar truncamiento. Máximos configurables: 20
  objetos y 500 columnas/componentes. Presupuesto: `1 + 2 * objetos` llamadas de
  catálogo; con cinco objetos son once, además de conexión/metadatos del driver.
- Solicita diez segundos de timeout de conexión y quince por consulta. La
  aplicación efectiva de esos límites depende del driver y debe verificarse en
  Windows. El límite de filas limita consumo, no el trabajo interno del servidor.
- No hay reintentos automáticos. Ante fallo del driver devuelve un error genérico
  sin cadena de conexión ni diagnóstico que pueda contener secretos.
- Bloqueo por directorio atómico bajo el perfil de usuario:
  `~/.intelcomercial/odbc-inspection.lock`. Es compartido entre clones para esa
  cuenta; no coordina con otras cuentas, el gateway ni el scheduler productivo.
  Tras terminar normalmente se retira. Si el proceso muere, verificar que no queda
  una ejecución activa antes de retirar manualmente ese directorio vacío.
- Los cursores y la conexión se cierran al finalizar o fallar. No se crea una
  tarea programada ni se reinicia el gateway.

| Salida | Código | Significado |
| --- | --- | --- |
| `mode=offline` | 0 | Plan local; no hubo conexión |
| `status=inspected` | 0 | Metadatos dentro del alcance y límites, no un contrato comercial certificado |
| `status=truncated` | 2 | Resultado parcial; reducir/dividir el alcance |
| `status=no_matches` | 2 | Sin objetos visibles; no significa ausencia de ventas |
| `status=failed` | 1 | No usar como evidencia completa; no se publica un catálogo parcial |

Una vista puede no publicar PK; una lista vacía de claves no prueba que no exista
una identidad lógica. Si el driver no soporta una operación de catálogo, la
inspección falla sin ampliar consultas automáticamente. No verifica permisos con
intentos de escritura. Los campos `permissions_verified` y
`business_mapping_verified` permanecen en `false`.

Guardar la salida real, si se necesita, dentro de `private/odbc/`, excluido de Git.
No versionar catálogos empresariales ni volcados. Esta etapa no escribe archivos
de evidencia automáticamente.

## Fuentes técnicas

Las operaciones de catálogo usan la API documentada de
[Cursor de pyodbc](https://github.com/mkleehammer/pyodbc/wiki/Cursor).
El timeout de consulta utiliza la opción
[QueryTimeout de DataDirect OpenEdge](https://docs.progress.com/bundle/datadirect-progress-openedge-odbc-80/page/Query-Timeout.html)
en la cadena de conexión, como el conector existente de TMA. No se asigna
`connection.timeout`, incompatible según el antecedente de esa instalación.
El timeout de inicio de sesión es independiente. El parámetro `readonly` se documenta en
[The pyodbc Module](https://github.com/mkleehammer/pyodbc/wiki/The-pyodbc-Module).
Su comportamiento real con DataDirect/OpenEdge queda pendiente de validación.
