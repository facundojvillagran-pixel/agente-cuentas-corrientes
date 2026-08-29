# Agente de cuentas corrientes

Prototipo local y privado para transformar documentos y conversaciones en cuentas corrientes trazables.

## Estado actual

La primera base implementa el motor deterministico que:

- registra movimientos con evidencia obligatoria;
- separa saldos confirmados y proyectados;
- mantiene monedas independientes;
- evita duplicados exactos mediante huellas de origen;
- admite anticipos generales y aplicaciones parciales;
- calcula fletes sin factura y anticipos de gasoil;
- conserva auditoria de altas y cambios.

La IA documental y la interfaz web se agregaran sobre este motor. La IA interpretara y propondra datos, pero no calculara el saldo oficial.

## Ejecutar las pruebas

No requiere dependencias externas en esta etapa:

```bash
python3 -m unittest discover -s tests -v
```

## Seguridad

- No guardar claves de API en el repositorio.
- Usar `.env` solamente en la instalacion local.
- No incorporar documentacion real hasta habilitar almacenamiento privado, usuarios y copias de seguridad.

