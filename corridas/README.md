# Corridas reales

Estas tres corridas fueron ejecutadas el 13 de septiembre de 2026 contra `interpret_text`, la misma función utilizada por `POST /accounts/{account_id}/intake/text`. Son casos operativos representativos del problema real; los textos, nombres y referencias fueron preparados y anonimizados para no publicar información comercial.

Cada carpeta incluye la entrada exacta, la salida guardada tal como la devolvió el motor (omitiendo únicamente el UUID aleatorio interno) y los pasos para reconstruirla. Ninguna propuesta fue aprobada: todas requieren revisión humana.

| Corrida | Situación | Resultado esperado |
|---|---|---|
| 01 | Anticipo claro en ARS | Propuesta en revisión |
| 02 | Transferencia sin dirección | Pregunta de aclaración |
| 03 | Cobro claro en USD con comprobante | Propuesta en revisión |
