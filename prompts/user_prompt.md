# Plantilla de ejecución

Procesá la siguiente entrada con el contrato de `system_prompt.md`.

- Cuenta: `{{account_id}}`
- Fecha de recepción: `{{received_on}}`
- Tipo de fuente: `{{source_kind}}`
- Referencia de fuente: `{{source_reference}}`
- Entrada: `{{input}}`
- Reglas confirmadas aplicables: `{{account_rules_or_none}}`

Usá la API o el lector correspondiente para analizar la entrada. No modifiques el saldo confirmado. Devolvé únicamente el JSON estructurado y dejá cualquier movimiento creado en estado `review` para aprobación humana.
