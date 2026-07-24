# Métricas y fórmulas — Sales Coach FASE 5

Todas las ventas de Sales Coach usan `amount_net` con fallback documentado a
`amount` cuando el campo neto no está disponible.

## Ranking de vendedores

No existe un ranking compuesto.

| Campo | Fórmula |
|---|---|
| `rankSales` | posición descendente por venta neta |
| `rankQuantity` | posición descendente por cantidad |
| `rankGrowth` | posición descendente por `(actual - anterior) / abs(anterior)` |
| `rankActiveClients` | posición descendente por clientes únicos con compra |
| `sharePct` | venta neta vendedor / venta neta total autorizada |
| `incrementalSales` | venta neta actual - venta neta comparativa |
| `stabilityPct` | `max(0, 100 - coeficiente de variación mensual * 100)` |
| `top3ClientsSharePct` | venta neta top 3 clientes / venta neta vendedor |
| `mixCount` | familias distintas; usa productos si falta el maestro |
| `potentialEstimated` | brecha positiva de venta/cliente contra promedio del equipo × clientes × 25% |

`potentialEstimated` es una heurística visible, no una predicción ni IA.

## Ficha del vendedor

Incluye:

- venta neta, cantidad, clientes, ticket, participación y crecimiento;
- recuperados, perdidos, mix, concentración, estabilidad e incremental;
- período anterior, promedio del equipo, umbral top 25% y año anterior cuando
  el histórico está disponible;
- objetivos personales autorizados;
- hasta tres fortalezas y tres oportunidades;
- hasta tres acciones, cada una con evidencia.

Las observaciones son deterministas. No se presentan como inteligencia
artificial; su configuración/versionado formal corresponde a FASE 6.

## Ficha del cliente

Incluye venta neta, cantidad, frecuencia, pedidos, ticket, mix, última compra,
recencia, vendedor, ruta, historia mensual, riesgo y oportunidades con evidencia.

El cliente no puede consultarse si está fuera de la cartera o estructura
autorizada del usuario.
