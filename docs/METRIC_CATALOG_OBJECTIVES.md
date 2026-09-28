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
- `brand`, `family` y `line`: productos resueltos desde `erp_articles`.
- `commercial_structure`: agrupa los mismos `sales_scheme_key` que `sales_force`
  en los cinco bloques de negocio de Dirección Comercial. El ERP reparte cada
  pedido (incluidos los de la app B2B TeMando) en su esquema real antes de
  llegar a `erp_sales`, así que cada línea de venta pertenece a exactamente un
  bloque; no hay solapamiento entre ellos. Acepta como `scope_key` la clave del
  esquema o su etiqueta (sin distinguir mayúsculas/acentos), normalizada a la
  clave canónica:

  | Clave | Bloque |
  |---|---|
  | `1` | Bebidas |
  | `2` | Mercadería |
  | `3` | Frescos |
  | `4` | Mayorista |
  | `5` | B2B — clientes sin cobertura de preventa, pedido ingresado por la app |

  Un cliente visitado por preventa que además compra por la app cae en su
  esquema habitual (1-4), no en B2B. Mapeo confirmado con Dirección Comercial
  el 2026-09-03; no debe modificarse sin ese visto bueno (ver
  `sales_coach/domain/commercial_structure.py`).

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
