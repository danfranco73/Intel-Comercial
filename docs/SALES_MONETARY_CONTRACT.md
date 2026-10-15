# Contrato monetario compatible para ventas Chess

La moneda principal acordada es ARS nominal, conciliable con Chess. Este cambio
no convierte a USD, no ajusta inflación ni recalcula el histórico 2022–2026.
`amount_final` es el nombre canónico del total final; `amount_gross` queda como
alias obsoleto de compatibilidad para consumidores existentes.

| Concepto | Campo Chess | Contrato V1 |
| --- | --- | --- |
| Bruto antes de bonificación | `subtotalBruto` | Sin columna propia; no llamarlo `amount_gross` |
| Bonificación monetaria | `subtotalBonificado` | Sin columna propia; distinta del porcentaje `bonificacion` |
| Neto | `subtotalNeto` | `amount_net` |
| Impuestos internos | `internos` | `internal_taxes` |
| Neto más internos | Derivado informativo | `amount_net_internal` |
| Total final | `subtotalFinal` | `amount_final` |

`subtotalFinal` ya incorpora los componentes tributarios. No se reconstruye
mediante bruto, bonificación, IVA, percepciones o internos cuando Chess entrega
el importe explícito. Los ceros explícitos y los signos de las devoluciones se
preservan. `internos` tiene precedencia sobre aliases anteriores, incluso si vale
cero. Los fallbacks existentes para formatos anteriores permanecen; no prueban
que un dato sin `subtotalFinal` sea conciliable ni autorizan publicarlo como tal.

## Migración compatible

La etapa implementada reutiliza `amount_final`, que ya existe en Mongo y
ClickHouse. El enriquecimiento del BI conserva ese valor explícito, incluso al
procesar por segunda vez un registro. Sólo para payloads antiguos sin ese campo,
recupera el alias `amount_gross` o el `amount` original. No exige DDL ni backfill.

Se preservan las semánticas legadas de `amount`: total final en la persistencia
V1, neto en las filas enriquecidas de análisis. Los nuevos consumidores deben
elegir explícitamente `amount_net` o `amount_final`; no deben inferir el concepto
comercial a partir de `amount`.

Durante la convivencia, `amount_gross` devuelve el mismo total final como alias
obsoleto. No se elimina ni se reasigna al bruto verdadero, porque eso rompería
consumidores silenciosamente. La migración siguiente deberá anunciar su retiro,
medir los consumidores restantes y quitarlo sólo en una versión incompatible
expresamente aprobada. Si se agrega el bruto real, tendrá un campo independiente
como `amount_before_discount`, acompañado de su procedencia; nunca se rellenará
con `subtotalFinal` ni se estimará por diferencia.

Corregir el normalizador afecta nuevas cargas cuando este código se despliegue.
No repara filas existentes automáticamente. Los scripts de auditoría conservan
una proyección explícita del mapeo V1 capturado, que ignoraba `internos`, para que
la propuesta y la evidencia histórica sigan siendo reproducibles tras el cambio.

Los grupos íntegramente cero ausentes no se incorporan con esta reparación ni
cuentan como ventas en el alcance del gate. Su trazabilidad queda en el archivo
Chess conservado. La futura portada deberá explicitar por separado su criterio
de conteo de documentos. PRVTA/PRDVO mantienen el tratamiento actual.
