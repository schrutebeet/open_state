# Seguimiento de leyes ordinarias

## Historial local y ELI

Cada consulta normal actualiza `data/history.db`. Agrega las tablas
`legislation_laws`, `legislation_identifiers`, `legislation_events` y
`legislation_collection_runs`, con cada fecha, fuente, URL y nivel de confianza.

La fecha de sancion/promulgacion se obtiene del indice diario ELI oficial del BOE. Es HTML
estructurado y da una URL ELI estable por ley. Una iniciativa sin `boe_id` sigue siendo valida:
ese identificador solo existe tras la publicacion. La base une datos solo por expediente, BOE,
ELI o numero/año oficial; nunca por titulos parecidos.

`--history-only` consulta solo lo que ya esta guardado:

```powershell
.\.venv\Scripts\python.exe scripts\leyes.py --date 2025-07-25 --history-only
```

La fecha de entrada en vigor del XML del BOE es la fecha general. Una ley puede tener articulos
con entrada en vigor posterior; el contador actual no pretende representar esas fechas parciales.

Esta primera versión automatiza el recorrido de las leyes ordinarias estatales usando
únicamente datos oficiales estructurados. Lee JSON, XML, CSV, HTML y RSS. No descarga ni
parsea PDF: si una etapa solo tiene evidencia en BOCG o Diario de Sesiones, conserva el
enlace cuando aparece en los datos y deja un aviso de verificación.

## Fuentes y alcance

| Etapa | Fuente oficial | Dato usado | Resultado |
| --- | --- | --- | --- |
| Inicio | [Open Data del Congreso](https://www.congreso.es/es/opendata/iniciativas) | `FECHAPRESENTACION` de proyectos y proposiciones | Automático |
| Inicio en el Senado | [Datos abiertos del Senado](https://www.senado.es/web/relacionesciudadanos/datosabiertos/catalogodatos/iniciativas/index.html) | XML de iniciativas y fecha de presentación/entrada | Automático si el XML contiene el campo |
| Calificación | Congreso y Senado | `FECHACALIFICACION` y equivalentes XML | Automático si existe |
| Tramitación | Congreso | `TRAMITACIONSEGUIDA`, incluyendo fases con fechas | Automático para fases etiquetadas; no se inventan fases |
| Aprobación parlamentaria definitiva | Congreso | Inicio de la fase `Concluido - (Aprobado...)` en `TRAMITACIONSEGUIDA` | Automático, confianza media; requiere BOCG/Diario de Sesiones para auditoría plena |
| Sanción/promulgación | [API de legislación consolidada del BOE](https://www.boe.es/datosabiertos/api/api.php?lang=es) | `fecha_disposicion` | Inferencia estructurada, confianza media; el acto directo no se extrae de PDF |
| Publicación | [API del sumario diario del BOE](https://www.boe.es/datosabiertos/api/api.php?lang=es) | Ítem de sección I, Jefatura del Estado, con identificador `BOE-A-...` | Automático |
| Entrada en vigor | API de legislación consolidada del BOE | `fecha_vigencia`, solo si está explícita | Automático; no se calcula la vacatio legis |
| Aprobación del Gobierno | [RSS/HTML de La Moncloa](https://www.lamoncloa.gob.es/paginas/varios/rss.aspx) | Referencias a proyectos/anteproyectos aprobados | Opcional y heurístico; se mantiene fuera del contador parlamentario |

El filtro de la primera versión exige “Proyecto de Ley” o “Proposición de Ley” y excluye
“Ley Orgánica”. En el BOE exige rango `Ley` y ámbito estatal. El Congreso publica en esta
página los ficheros de la legislatura actual; el parámetro de legislatura se aplica de forma
explícita al Senado y se deja documentado para ampliar después el histórico del Congreso.

## API de Python

```python
from civic_metrics.legislation import Ley

informe = Ley.resumen_dia("2026-09-15", legislature="15")
print(len(informe.initiated))
print(len(informe.approved))
print(len(informe.sanctioned_promulgated))
print(informe.to_dict())
```

El informe también incluye publicación en BOE, entrada en vigor, todos los eventos de cada
ley, URL de procedencia, confianza y avisos de calidad. En JSON, `counts` solo contiene eventos
de confianza alta. Las inferencias de confianza media aparecen en `candidate_counts`, y
`observed_counts` muestra todo lo extraído. Si una fuente necesaria no responde, el valor de
`counts` es `null`, no `0`, y `coverage` lo marca como `incomplete`.

## Línea de comandos

```powershell
.\.venv\Scripts\python.exe scripts\leyes.py --date 2026-09-15
.\.venv\Scripts\python.exe scripts\leyes.py --date 2026-09-15 --json
.\.venv\Scripts\python.exe scripts\leyes.py --date 2026-09-15 --include-government
```

Las fuentes se consultan de forma independiente: si un portal no responde, el informe de las
demás fuentes se conserva y añade un aviso. Las respuestas HTTP se benefician de la caché por
ejecución de `HttpClient`.

Cuando la API oficial del sumario devuelve `404` para una fecha sin boletín, se registra como
fuente vacía (`empty`) y se confirma `publicadas_boe = 0`; un error distinto o una caída de la
fuente mantiene el valor en `null`.

## Qué significa cada contador

- `iniciadas`: expedientes cuya fecha de presentación/entrada coincide con el día consultado.
- `aprobadas_definitivamente`: aprobaciones confirmadas con evidencia de confianza alta. Las
  coincidencias de `RESULTADOTRAMITACION` se entregan como candidatas.
- `sancionadas_promulgadas`: confirmaciones de confianza alta. La fecha de disposición del BOE
  se entrega como candidata porque no es una lectura directa del instrumento de sanción.
- `publicadas_boe`: leyes ordinarias detectadas en el sumario diario del BOE.
- `entradas_en_vigor`: leyes con `fecha_vigencia` explícita igual al día.

No se cuenta un Real Decreto, Real Decreto-ley, Real Decreto Legislativo u otra disposición
reglamentaria como ley ordinaria. La aprobación de un anteproyecto/proyecto por el Gobierno,
cuando se activa `--include-government`, se devuelve en `government_approvals` y no altera los
cinco contadores legislativos principales.
