# Gestión Comercial Local

Versión local para analizar ventas históricas de una distribuidora relacionando cuatro datasets:

1. Venta por cliente
2. Maestro de artículos
3. Maestro de rutas
4. Maestro de vendedores

La venta por cliente ahora puede venir desde archivos Excel, directo desde ChessERP o desde ventas ERP persistidas en MongoDB.
El maestro de artículos también puede poblarse desde ChessERP con agrupaciones como línea, familia, marca, sabor, proveedor, segmento y unidad de negocio.
Los maestros de vendedores y rutas se derivan automáticamente de las ventas ERP y también se persisten en MongoDB.

## Cómo usar

1. Abrí una terminal en VS Code dentro de esta carpeta.
2. Ejecutá:

```bash
python3 app.py
```

3. Abrí `http://127.0.0.1:8765`
4. Cargá o elegí los archivos para cada dataset.
5. En `Venta por cliente` podés agregar varios archivos, por ejemplo uno por año.
6. Elegí hoja y fila de encabezado por dataset o por archivo de ventas.
7. Mapeá columnas según el tipo de archivo.
8. Ejecutá el análisis.

## Uso con ChessERP

1. Configurá en `.env`:

```bash
CHESS_ERP_BASE_URL=...
ERP_LOGIN_PATH=/auth/login
CHESS_ERP_USERNAME=...
CHESS_ERP_PASSWORD=...
CHESS_ERP_TIMEOUT=30
CHESS_ERP_VERIFY_SSL=true
```

2. En la tarjeta `Venta por cliente`, elegí `ChessERP`.
3. Definí `Fecha desde` y `Fecha hasta`.
4. Si querés persistir la información, usá `Sincronizar en MongoDB`.
5. Esa sincronización guarda ventas ERP y también el maestro de artículos con sus agrupaciones.
6. Para trabajar sin depender del ERP ni del Excel, cambiá la fuente a `MongoDB`.
7. Si querés enriquecer todavía más el análisis, podés seguir cargando archivos manuales, pero ya no son obligatorios para ventas, artículos, vendedores y rutas.
8. Ejecutá el análisis.

## Persistencia en MongoDB

- Las ventas sincronizadas desde ChessERP se guardan en la colección `erp_sales`.
- Los artículos y sus agrupaciones se guardan en `erp_articles`.
- Los vendedores derivados del ERP se guardan en `erp_sellers`.
- Las rutas derivadas del ERP se guardan en `erp_routes`.
- Cada sincronización queda registrada en `erp_sync_runs`.
- La configuración de la pantalla sigue guardándose en `sessions`.
- Los análisis ejecutados siguen registrándose en `registros`.

## Qué resuelve esta versión

- Relaciona ventas con información de artículos
- Relaciona ventas con fuerza de ventas, vendedor y descripción de ruta
- Cruza la ruta de la venta con el maestro de vendedores y completa fuerza de ventas desde maestros
- Mide cobertura real de los maestros
- Calcula ventas históricas con contexto territorial y de mix
- Detecta clientes activos, dormidos, reactivables y perdidos
- Calcula ratios comerciales clave
- Estima potencial de mejora por recuperación, cross-sell y ruteo
- Genera proyecciones trimestrales simples
- Muestra gráficos para presentación
- Redacta insights y planes de acción
- Permite filtrar el informe por `Año`, `Mes`, `Familia`, `Línea`, `Proveedor`, `Fuerza de ventas`, `Ruta`, `Vendedor` y `Canal`

## Campos mínimos sugeridos

### Venta por cliente

- `Fecha` o bien `Año` + `Mes`
- `Código cliente`
- `Importe`
- Datos sugeridos: `Ruta`, `Código vendedor`, `Nombre vendedor`, `Código artículo`, `Canal`, `Cantidad`

### Maestro de artículos

- `Código artículo`
- Datos sugeridos: `Familia`, `Línea`, `Proveedor`, `Sabor`, `UxB`, `Calibre`

### Maestro de rutas

- `Vendedor`
- Datos sugeridos: `Fuerza de ventas`, `Descripción de la ruta`

### Maestro de vendedores

- `Código vendedor` o `Nombre vendedor`
- Datos sugeridos: `Ruta`, `Fuerza de ventas`

## Notas técnicas

- No usa dependencias externas.
- Lee `.xlsx` y `.xlsm`.
- La preview está optimizada para archivos grandes.
- Si un maestro no se carga, el análisis sigue funcionando pero baja la calidad de la lectura comercial.
