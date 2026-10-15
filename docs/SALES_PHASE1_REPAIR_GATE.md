# Gate de reparación monetaria de ventas

El alcance aprobado es del 22 al 28 de septiembre de 2026. El ejecutor modifica
17.805 claves distintas de V1 en cada destino, sin insertar ni eliminar registros.
Sólo permite `amount`, `amount_net`, `amount_final`, `internal_taxes` y
`amount_net_internal`. No modifica cantidades, dimensiones ni PRVTA/PRDVO.
Este documento describe el mecanismo; el resultado observado queda en
`artifacts/<ejecución>/gate-report.json`.

La prueba usa respaldos reales de Mongo y ClickHouse y respuestas reales de Chess
conservadas en `artifacts/phase1-live-2026-10-05`. Verifica nuevamente las dos
pasadas de Chess y reproduce la propuesta revisada antes de escribir. No consulta
fuentes remotas ni carga `.env`. Los controles fuera del período son 6.103 filas
reales del 21/09, tomadas de un archivo anterior cuya huella se verifica.

## Ejecución aislada

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/run_sales_phase1_gate.py \
  --output artifacts/phase1-gate-NOMBRE-NUEVO
```

Requiere `mongod` instalado y el binario ClickHouse del piloto local. El directorio
de salida debe ser nuevo. Ambos motores se ejecutan en puertos locales exclusivos,
con almacenamiento propio; se detienen al terminar incluso si la prueba falla.
No se crea ni reconstruye una tabla V2. No se copia todo el histórico.

El gate exige las huellas completas originales, aplica la propuesta, verifica
conteos y campos, concilia pesos con Chess y ejecuta el rollback. También interrumpe
una aplicación después del primer lote y recupera con una instancia nueva del
ejecutor y sus respaldos persistidos. Las pruebas unitarias usan datos sintéticos
únicamente para verificar rechazos y contratos; no demuestran ventas reales.

## Mecanismo compartido de reparación y rollback

`sales_coach/services/sales_phase1_repair.py` recibe clientes explícitos. No busca
credenciales ni contiene una orden que habilite producción. Los mismos adaptadores
y el mismo algoritmo sirven para aplicación y rollback:

- Mongo usa actualizaciones sin `upsert`, con identidad y valores previos en el
  filtro. Guarda un respaldo por destino que conserva los tipos BSON.
- ClickHouse usa `ALTER TABLE ... UPDATE`, con claves exactas, rango de fechas y
  valores previos en el predicado; espera las mutaciones con `mutations_sync=2`.
  No reemplaza particiones ni usa tablas auxiliares o subconsultas mutables.
- El avance por lote y sus huellas se escriben a disco antes y después de cada
  operación. El directorio tiene exclusión local y archivos privados.
- Antes de aplicar se validan ambos destinos. Una segunda aplicación ciega se
  rechaza. El rollback admite filas en estado original o reparado y rechaza
  cualquier otro estado para no pisar cambios ajenos. Repetir un rollback completo
  no vuelve a modificar filas.
- Toda mutación ClickHouse pendiente bloquea la validación o recuperación. Ante
  un timeout hay que esperar y verificar su finalización antes de reintentar el
  rollback; no se puede asumir que un timeout canceló la escritura.

El respaldo del propio ejecutor, su journal y la propuesta deben conservarse
juntos en almacenamiento durable. Un respaldo ClickHouse no reemplaza el respaldo
Mongo: un `0.0` no restaura la huella BSON de un `0` original.

## Conciliación y límites

Los importes explícitos `subtotalNeto` y `subtotalFinal` son autoridad. `internos`
se conserva como componente informativo; no se suma al final. La suma decimal de
las representaciones de los valores almacenados debe coincidir exactamente con
Chess en este período: tolerancia monetaria del gate igual a cero. Esto no supone
que una suma nativa Float64 sea aritmética decimal exacta. No se redondean pesos
para ocultar diferencias. Las cantidades permanecen idénticas al respaldo.

Los 440 grupos íntegramente cero ausentes de V1 se conservan como evidencia en la
captura y en el informe de conciliación. No se insertan mediante esta reparación.
Los conteos de origen incluyen cancelados y esos grupos; el informe explica esa
diferencia y no exige que el número de líneas raw sea igual al de grupos V1.

Los clones usan MongoDB local y ClickHouse MergeTree. Producción usa ClickHouse
SharedMergeTree. La prueba demuestra el algoritmo y el rollback contra motores
reales, pero no prueba replicación, permisos ni rendimiento del servicio cloud.
Sólo hay controles reales anteriores al período en los archivos disponibles; la
cota superior también queda explícita en el SQL y se verifica en pruebas de
rechazo de planes fuera de rango.

## Condiciones para una futura reparación productiva

Un gate aislado aprobado no autoriza producción. Antes de esa reparación se debe
volver a comprobar que las huellas del período coincidan con los respaldos
aprobados. Si cambiaron, se detiene y se revisa la evidencia; no se adapta la
propuesta automáticamente.

Se necesita una ventana sin ingestión concurrente y sin publicación de resultados
parciales, además de respaldos durables y permisos de mutación en el destino
cloud. El lock local del ejecutor no bloquea otros servidores. No existe una
transacción distribuida entre Mongo y ClickHouse: ante falla de un destino se
mantiene la publicación cerrada y se restaura el estado confirmado con el mismo
ejecutor. Estas condiciones son necesarias para producción y no están presentadas
como infraestructura ya implementada por este gate.

## Validación para versionado

La suite del proyecto se ejecuta con:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q
```

`pytest.ini` limita el descubrimiento a `tests/`, evitando cargar copias de
pruebas conservadas dentro de capturas privadas. `tests/conftest.py` bloquea
la carga de `.env` y las conexiones externas. La revisión de este cambio obtuvo
213 pruebas aprobadas.

También se revisó la ejecución archivada
`artifacts/phase1-gate-2026-10-05-run3/gate-report.json`: resultado
`PASS_ISOLATED_ONLY_STOP_BEFORE_PRODUCTION`, rollback completo y recuperación
tras interrupción con las huellas originales de ambos destinos, controles fuera
del período intactos y procesos locales detenidos. Las copias de los nueve
archivos Python conservadas con esa ejecución coinciden byte a byte con las
versiones revisadas. Esta comprobación reutiliza evidencia local existente;
no constituye una nueva ejecución del gate ni una reparación productiva.
