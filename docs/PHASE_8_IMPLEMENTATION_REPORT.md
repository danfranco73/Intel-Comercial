# Informe de implementación — FASE 8 (PDF)

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Alcance ejecutado

Se implementó preparación de reuniones, informes PDF A4 y PowerPoint editable
16:9. El PPTX se incorporó después de validar el PDF, conforme al orden definido
para la fase.

Cada informe se calcula una sola vez desde Sales Coach y se guarda como snapshot
versionado. La vista previa y cada descarga posterior usan ese mismo snapshot:
no recalculan cifras ni dependen del estado posterior de las bases.

## Contenido

Informe general:

- resumen ejecutivo determinista;
- KPIs del período;
- gráfico y ranking de vendedores;
- objetivos activos;
- alertas y oportunidades;
- reconocimientos;
- período, filtros y fecha real de actualización;
- identificador y versión.

Paquete por vendedores:

- informe general;
- una ficha iniciada en página nueva por vendedor;
- identificación y KPIs;
- fortalezas, oportunidades y evidencia;
- plan de acción;
- objetivo autorizado;
- espacio de compromiso y resultado.

PowerPoint:

- entre 8 y 9 diapositivas según el contenido disponible;
- máximo técnico de 12;
- portada, resumen, ranking, objetivos, alertas, reconocimientos, coaching y
  compromisos;
- gráficos nativos editables;
- tablas, textos, colores y formas editables;
- narrativa breve basada en el mismo snapshot del PDF.

## Datos y consistencia

El servicio reutiliza `SalesCoachService` y no contiene un segundo motor de
métricas. Los datos estructurados se persisten antes de renderizar. ReportLab
recibe exclusivamente ese JSON persistido.

Colección `meeting_reports`:

- `report_id`;
- `version`;
- `report_type`;
- `period` y `comparison_period`;
- `filters`;
- `seller_keys`;
- `scope_snapshot`;
- `data_updated_at`;
- `payload`;
- `created_by` y `created_at`.

Índices:

- `meeting_report_id_unique`;
- `meeting_creator_created`;
- `meeting_period_type`.

## API

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/meetings` | listar snapshots autorizados |
| GET | `/api/meetings/report?report_id=...` | recuperar el JSON exacto |
| POST | `/api/meetings` | crear un snapshot |
| POST | `/api/meetings/pdf` | descargar el PDF del snapshot |
| POST | `/api/meetings/pptx` | descargar el PowerPoint editable |

Las mutaciones requieren sesión y CSRF.

## Seguridad

- admin y dirección acceden a informes globales;
- supervisor sólo accede si todos los vendedores siguen dentro de su alcance;
- vendedor sólo genera/descarga su propia ficha;
- viewer puede consultar snapshots autorizados, pero no generar nuevos;
- los filtros se validan mediante lista permitida;
- `seller_keys`, sucursal y supervisor se resuelven en backend;
- el PDF no consulta libremente MongoDB ni ClickHouse.

## Dependencia

```text
reportlab>=4.2
python-pptx>=1.0
```

ReportLab y python-pptx generan los documentos en memoria. No se escriben
archivos temporales en el servidor.

## Migración

```bash
.venv/bin/python scripts/migrate_phase8_meetings.py
```

La migración crea o verifica la colección y sus índices. No modifica ventas,
usuarios, objetivos, alertas, sesiones ni informes previos.

## Validación manual

1. Generar el dashboard para el período requerido.
2. Aplicar filtros comerciales, si corresponden.
3. Abrir `Reuniones`.
4. Elegir `Informe general` o `General + una ficha por vendedor`.
5. Generar el snapshot.
6. Verificar período, actualización y cifras de la vista previa.
7. Descargar el PDF.
8. Descargar el PPTX y comprobar que textos, tablas y gráfico sean editables.
9. Comparar venta, clientes y crecimiento con el dashboard.
10. Volver a descargar el mismo snapshot: las cifras deben ser idénticas.
11. Repetir con roles supervisor y vendedor para verificar aislamiento.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_phase8_meetings.py
.venv/bin/python -m pytest -q
node --check static/app.js
```

Se valida:

- fechas y filtros;
- snapshot sin recálculo;
- versión;
- aislamiento del vendedor;
- denegación al supervisor fuera de alcance;
- PDF válido;
- paquete multipágina.
- PowerPoint válido y 16:9;
- gráfico y tablas editables;
- máximo de 12 diapositivas.

## Rollback

1. Volver al código de FASE 7.
2. Conservar `meeting_reports`; el código anterior la ignora.
3. Quitar ReportLab sólo si ninguna otra función lo utiliza.
4. No borrar snapshots sin una política de retención aprobada.

No se incorporaron IA, predicciones ni recomendaciones de fases posteriores.
