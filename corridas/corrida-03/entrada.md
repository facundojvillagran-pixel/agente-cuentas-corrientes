# Entrada

- Fecha de ejecución: 2026-09-13
- Fuente: conversación administrativa anonimizada
- Referencia: `mensaje-administracion-anonimizado-03`
- Fecha de recepción usada: 2026-09-13
- Contrato usado: `prompts/system_prompt.md` + `prompts/user_prompt.md`
- Herramienta usada: `cuentas_corrientes.intake.interpret_text`

```text
El 22/07/2026 recibimos de Transporte Sur USD 1.500 por la FC 000123
```

## Reconstrucción

Ejecutar `interpret_text(text=..., source_reference="mensaje-administracion-anonimizado-03", received_on=date(2026, 9, 13))` desde `cuentas_corrientes.intake`, o enviar los mismos campos a `POST /accounts/{account_id}/intake/text`.

La salida original está conservada en `salida.json`. Solo se omitió el UUID interno aleatorio, que no modifica la decisión ni los datos extraídos.
