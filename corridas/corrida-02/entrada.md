# Entrada

- Fecha de ejecución: 2026-09-13
- Fuente: conversación administrativa anonimizada
- Referencia: `mensaje-administracion-anonimizado-02`
- Fecha de recepción usada: 2026-09-13
- Contrato usado: `prompts/system_prompt.md` + `prompts/user_prompt.md`
- Herramienta usada: `cuentas_corrientes.intake.interpret_text`

```text
Transferencia de $ 250.000
```

## Reconstrucción

Ejecutar `interpret_text(text=..., source_reference="mensaje-administracion-anonimizado-02", received_on=date(2026, 9, 13))` desde `cuentas_corrientes.intake`, o enviar los mismos campos a `POST /accounts/{account_id}/intake/text`.

La salida original está conservada en `salida.json`. Esta corrida demuestra la restricción de no inventar la dirección de una transferencia.
