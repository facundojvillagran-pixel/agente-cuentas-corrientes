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
- recibe texto conversacional ficticio, extrae importe, moneda y fecha;
- pide aclaraciones cuando no puede determinar una operacion sin inventar datos;
- crea propuestas proyectadas que requieren aprobacion humana para afectar el saldo confirmado.

La IA documental y la interfaz web se agregaran sobre este motor. La IA interpretara y propondra datos, pero no calculara el saldo oficial.

## Ejecutar las pruebas

No requiere dependencias externas en esta etapa:

```bash
.venv/bin/python -m pytest -q
```

Para iniciar la API local durante el desarrollo:

```bash
.venv/bin/uvicorn cuentas_corrientes.api:app --reload
```

Luego abrir `http://127.0.0.1:8000` para usar la interfaz simple. Las cuentas se
guardan localmente en `data/cuentas.sqlite3`, que esta excluido de Git.

## Seguridad

- No guardar claves de API en el repositorio.
- Usar `.env` solamente en la instalacion local.
- No incorporar documentacion real hasta habilitar almacenamiento privado, usuarios y copias de seguridad.
