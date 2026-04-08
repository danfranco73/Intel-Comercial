# Gestión Comercial Local

Versión local para analizar ventas históricas de una distribuidora relacionando cuatro datasets:

1. Venta por cliente
2. Maestro de artículos
3. Maestro de rutas
4. Maestro de vendedores

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
