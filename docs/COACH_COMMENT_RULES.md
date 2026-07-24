# Motor determinista de comentarios — FASE 6

## Principios

- No utiliza IA ni `eval`.
- Una regla sólo produce comentario si toda su evidencia está disponible.
- Fortalezas y oportunidades se limitan a tres por evaluación.
- Las prioridades más altas se muestran primero.
- `conflict_group` impide mensajes contradictorios.
- Cada salida informa `rule_id`, versión, prioridad y evidencia.
- Cada ficha consultada crea una auditoría persistente.

## Formato

```json
{
  "rule_id": "SELLER_GROWTH_ABOVE_TEAM",
  "version": 1,
  "priority": 88,
  "category": "strength",
  "condition": {
    "metric": "growthPct",
    "operator": "gte_context",
    "context": "teamGrowthPct"
  },
  "message_template": "La venta creció {growthPct:.1f}%, frente a {teamGrowthPct:.1f}% del equipo.",
  "action_template": "Repetir las prácticas de crecimiento en las rutas con mayor potencial.",
  "evidence_fields": ["growthPct", "teamGrowthPct"],
  "conflict_group": "growth"
}
```

Operadores permitidos:

- `eq`, `lt`, `lte`, `gt`, `gte`;
- variantes `*_context` para comparar contra un benchmark calculado.

No se aceptan expresiones Python, JavaScript, SQL ni código libre.

## Temas cubiertos

- top 3;
- mejora de ranking;
- crecimiento y caída;
- recuperación y pérdida neta de clientes;
- participación;
- mix y cross-sell;
- concentración;
- clientes activos;
- ticket;
- cantidad;
- cumplimiento de objetivos;
- categoría fuerte y débil.

## Versionado

`coach_rule_sets` conserva versiones inmutables. Crear una versión:

```http
POST /api/coach/rules
Content-Type: application/json
X-CSRF-Token: ...

{
  "entity_type": "seller",
  "notes": "Umbrales aprobados por Dirección Comercial",
  "rules": [...]
}
```

La versión anterior se desactiva pero no se elimina. Si la inserción falla se
restaura la configuración anterior.

## Auditoría

`coach_comment_audits` guarda:

- usuario;
- vendedor;
- período;
- rule set y versión;
- fortalezas;
- oportunidades;
- reglas y evidencia seleccionadas;
- fecha.

Consulta administrativa:

```http
GET /api/coach/audits?limit=50
```

No se guardan contraseñas, sesiones, cookies ni secretos.
