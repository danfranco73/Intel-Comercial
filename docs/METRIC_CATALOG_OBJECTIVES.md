# Catálogo gobernado de métricas de objetivos

Fuente inicial: ventas normalizadas de `erp_sales`. La fecha de actualización se
obtiene del pipeline de sincronización de FASE 3. Importes y metas se persisten
como `Decimal128`; el cálculo sobre ventas conserva temporalmente el tipo de la
fuente actual.

| Código | Nombre | Fórmula | Unidad | Granularidad | Filtros/alcances | Tratamiento actual |
|---|---|---|---|---|---|---|
| `net_sales` | Venta neta | `SUM(amount_net)` | moneda | mes | empresa, sucursal, fuerza, supervisor, vendedor, ruta, canal, marca, familia | utiliza importe neto normalizado; anulaciones/devoluciones impactan con su signo de origen |
| `quantity` | Cantidad | `SUM(quantity)` | unidad comercial normalizada | mes | mismos alcances | suma cantidades con signo; no se denomina pack/bulto sin definición de Chess |
| `active_clients` | Clientes activos | `COUNT(DISTINCT client_key)` | clientes | mes | mismos alcances | cliente con al menos una venta incluida |
| `mix` | Mix activo | `COUNT(DISTINCT product_key)` | productos | mes | mismos alcances | profundidad de productos distintos; no es porcentaje |
| `new_clients` | Clientes nuevos | primera compra dentro del período | clientes | mes | mismos alcances | requiere histórico disponible; la retención Mongo puede subestimar |
| `recovered_clients` | Clientes recuperados | activo actual, inactivo en ventana anterior equivalente y con compra previa | clientes | mes | mismos alcances | requiere histórico anterior a ambas ventanas |

## Alcances

- `company`: sin filtro comercial adicional.
- `seller`: `seller_key`.
- `sales_force`: `sales_scheme_key` o nombre de fuerza.
- `route`: descripción normalizada de ruta.
- `channel`: canal normalizado.
- `branch` y `supervisor`: vendedores resueltos desde `erp_sellers`.
- `brand` y `family`: productos resueltos desde `erp_articles`.

## Evidencia entregada

Cada objetivo devuelve:

- valor real;
- meta;
- cumplimiento;
- brecha;
- proyección;
- tendencia contra baseline, cuando existe;
- período;
- fecha de cálculo;
- fórmula.

Responsable de negocio pendiente de designación formal: Dirección Comercial.
Hasta esa confirmación no deben modificarse fórmulas ni semántica desde la UI.
