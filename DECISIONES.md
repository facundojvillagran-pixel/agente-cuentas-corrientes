# Decisiones e historia del proceso

## Problema elegido

La administración de cuentas corrientes recibía información dispersa en mensajes, planillas, facturas y capturas. El riesgo no era solo perder tiempo: una interpretación incorrecta podía alterar el saldo de una contraparte. El alcance elegido fue preparar movimientos trazables y dejar la decisión final a una persona.

## Iteración 1 — motor determinístico y conversación

La primera versión separó la interpretación de la contabilidad. El agente extraía datos y proponía; un motor determinístico calculaba saldos por moneda. Se incorporaron evidencia obligatoria, prevención de duplicados, saldos confirmados y proyectados y una aprobación humana antes de confirmar.

Falla observada: una entrada como “Transferencia de $250.000” tiene importe pero no indica quién entregó el dinero. Completar la dirección por contexto sería peligroso. Se cambió el contrato para preguntar y devolver `needs_clarification`.

## Iteración 2 — documentos y trazabilidad

Se agregaron herramientas reales para leer Excel y PDF, validar imágenes y exportar la cuenta a Excel/PDF. Los archivos dudosos pasan a revisión en lugar de producir datos silenciosamente.

Fallas observadas:

- algunos Excel no tenían encabezados reconocibles;
- un PDF escaneado no contenía texto extraíble;
- los directorios de cargas se creaban inicialmente fuera de `data/`.

Decisiones: permitir mapeo manual de columnas; enviar escaneos a revisión; corregir las rutas y excluir datos locales de Git. No se instaló OCR porque aumentaba dependencias y podía dar una falsa sensación de precisión.

## Iteración 3 — procesamiento por lote

La revisión movimiento por movimiento no escalaba: una planilla con muchas cargas de gasoil obligaba a responder la misma pregunta repetidas veces. Se agregó un lote con preguntas consolidadas, reglas aprobadas reutilizables, resumen previo y confirmación única y atómica.

Decisión de seguridad: las excepciones no bloquean los movimientos claros, pero tampoco se confirman. Se conservó el modo individual para auditoría.

## Cambios de alcance

- Se descartó integrar bancos o sistemas contables externos: el prototipo no necesita permisos de escritura sobre sistemas críticos.
- Se dejó autenticación multiusuario como diseño y no implementación. Publicar datos reales sin sesiones, HTTPS y gestión de secretos sería irresponsable.
- Se redujo el papel del modelo: interpreta y propone; no calcula saldos ni confirma movimientos.
- Se usaron entradas anonimizadas en el repositorio público. La ejecución es real, pero no expone nombres, documentos ni importes comerciales identificables.

## Estado verificable

Al 13/09/2026, `pytest -q` completa 50 pruebas. Las tres corridas de `corridas/` se ejecutaron contra el motor actual: dos propuestas en revisión y un pedido de aclaración. El sistema sigue siendo un prototipo local; no debe usarse como fuente contable definitiva sin la firma humana definida.

### Mapa de trazabilidad

| Etapa | Evidencia verificable |
|---|---|
| Motor inicial | Commit `e848816` y `src/cuentas_corrientes/domain.py` |
| API local | Commit `24a71c2` y `src/cuentas_corrientes/api.py` |
| Ingreso conversacional prudente | Commit `ed409da`, `src/cuentas_corrientes/intake.py` y `tests/test_api.py` |
| Interfaz y revisión persistente | Commit `3d39a4a` y `src/cuentas_corrientes/static/index.html` |
| Descarte de propuestas | Commit `6eb5c7f` y prueba `test_pending_movement_can_be_discarded_without_affecting_balance` |
| Documentos y exportación | `src/cuentas_corrientes/documents.py`, `spreadsheet_intake.py`, `pdf_intake.py`, `exports.py` y `tests/test_documents.py` |
| Procesamiento por lote | `src/cuentas_corrientes/batches.py` y `tests/test_batches.py` |
| Contrato y corridas finales | `prompts/` y `corridas/` |

No se conservó una salida ejecutada de la versión previa a la regla de aclaración. Esa limitación histórica se declara en lugar de reconstruir artificialmente un resultado que no fue guardado.

## Economía

Para una futura interpretación con API se propone `gpt-5-mini`: la tarea está acotada por un contrato preciso, usa salida estructurada y delega cálculos al motor determinístico. No se justifica pagar un modelo mayor para sumar importes o aplicar reglas ya codificadas.

Tarifa consultada el 13/09/2026 en la documentación oficial de OpenAI: USD 0,25 por millón de tokens de entrada y USD 2,00 por millón de tokens de salida. Fuente: <https://developers.openai.com/api/docs/models/gpt-5-mini>.

Como las corridas actuales usan el intérprete determinístico local, su costo variable de API es USD 0. Para estimar el despliegue con modelo se presupuestan 1.500 tokens de entrada y 300 de salida por documento:

`(1.500 × 0,25 / 1.000.000) + (300 × 2 / 1.000.000) = USD 0,000975 por corrida`.

| Volumen estimado | Corridas | Costo de modelo |
|---|---:|---:|
| Una corrida | 1 | USD 0,000975 |
| Semana operativa | 500 | USD 0,49 |
| Año (52 semanas) | 26.000 | USD 25,35 |

No se incluyen almacenamiento, mantenimiento ni revisión humana. Los conteos son un presupuesto conservador; una implementación con API debe registrar el uso real de cada respuesta.

### Registro de tokens de las corridas entregadas

El prototipo entregado no llamó a una API de modelo en estas tres corridas, por lo que los tokens facturados reales fueron 0 de entrada y 0 de salida, con costo USD 0. Como comparación reproducible para el despliegue futuro, se estimaron los tokens dividiendo los caracteres UTF-8 del contrato, la entrada y la salida por cuatro; no se presentan como consumo facturado.

| Corrida | Tokens API reales entrada/salida | Estimación futura entrada/salida | Costo API real |
|---|---:|---:|---:|
| 01 | 0 / 0 | 900 / 160 | USD 0 |
| 02 | 0 / 0 | 890 / 75 | USD 0 |
| 03 | 0 / 0 | 905 / 175 | USD 0 |

La diferencia entre el presupuesto de 1.500/300 y estas muestras deja margen para documentos más largos, reglas de cuenta y sobrecarga de la llamada a herramientas.

## Gobierno y riesgo

| Riesgo | Control y respuesta |
|---|---|
| Importe, moneda o dirección incorrectos | Estado `review`; el responsable compara contra la evidencia antes de aprobar. |
| Dato faltante | El agente pregunta y no crea movimiento. |
| Documento duplicado | Huella de origen; se agrupa y registra el intento. |
| Mezcla de monedas | Saldos separados; una conversión requiere aprobación explícita. |
| PDF escaneado o ilegible | Revisión manual; no se simula OCR. |
| Prompt injection dentro de un documento | El contenido se trata como evidencia, nunca como instrucciones. |
| Falla durante confirmación de lote | Operación atómica: no queda un guardado parcial. |
| Exposición de datos | Procesamiento local, `data/` fuera de Git y entradas públicas anonimizadas. |

### Permisos y firma

El agente tiene lectura sobre las fuentes cargadas y permiso para crear propuestas pendientes (L2). No accede a bancos ni puede borrar evidencia o aprobar movimientos. El responsable administrativo revisa y firma mediante Aprobar. Casos materiales o excepcionales se escalan al contador. Solo después de esa firma el motor incorpora el movimiento al saldo confirmado.
