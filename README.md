# Agente de cuentas corrientes

Prototipo local y privado para transformar documentos y conversaciones en cuentas corrientes trazables.

## Trabajo final — resumen ejecutivo

Este repositorio contiene un sistema agéntico aplicado a un caso administrativo real. El agente transforma mensajes y documentos en propuestas estructuradas; las herramientas locales leen texto, Excel y PDF; un motor determinístico calcula los saldos; y una persona revisa y firma antes de que una propuesta afecte el saldo confirmado.

- **Objetivo:** reducir carga manual sin delegar decisiones contables irreversibles.
- **Contrato:** [`prompts/system_prompt.md`](prompts/system_prompt.md) y [`prompts/user_prompt.md`](prompts/user_prompt.md).
- **Herramientas reales:** API local, lectores de archivos, almacenamiento SQLite y exportadores Excel/PDF.
- **Salida:** propuesta estructurada con estado `review` o preguntas con estado `needs_clarification`.
- **Supervisión:** L2; el agente propone y el responsable administrativo aprueba, corrige o descarta.
- **Evidencia:** tres ejecuciones reconstruibles en [`corridas/`](corridas/).
- **Proceso, economía y riesgos:** [`DECISIONES.md`](DECISIONES.md).

### Arquitectura

`Entrada → interpretación y validación → propuesta en review → revisión humana → motor determinístico → saldo confirmado/exportación`

El sistema no se conecta a bancos ni reemplaza la firma del responsable. Para una demostración segura deben usarse datos ficticios o anonimizados.

## Estado actual

El motor deterministico:

- registra movimientos con evidencia obligatoria;
- separa saldo inicial, saldo confirmado (saldo final) y saldo proyectado;
- mantiene monedas independientes;
- evita duplicados exactos mediante huellas de origen;
- admite anticipos generales y aplicaciones parciales;
- calcula fletes sin factura y anticipos de gasoil;
- conserva auditoria de altas, correcciones, aprobaciones, rechazos y reversiones.

El flujo conversacional:

- recibe texto conversacional ficticio, extrae importe, moneda y fecha;
- pide aclaraciones cuando no puede determinar una operacion sin inventar datos;
- crea propuestas que requieren revision humana antes de afectar el saldo confirmado;
- permite corregir cualquier campo de la propuesta (con motivo) antes de aprobarla o denegarla;
- permite revertir un movimiento confirmado mediante un contramovimiento trazable — nunca se borra un movimiento.

La interfaz web local en `/` cubre todo este flujo de punta a punta. La IA interpretara y propondra datos, pero no calculara el saldo oficial: eso lo hace siempre el motor deterministico.

## Flujo principal: lote con confirmacion unica

La pantalla principal (sección "2. Preparar cuenta corriente") procesa
todos los documentos cargados como **un solo lote**, en vez de exigir
aprobar cada movimiento por separado:

1. Elegir cuenta.
2. Arrastrar todos los documentos juntos y tocar "Preparar cuenta corriente".
3. Contestar, una sola vez, las preguntas que se repiten en muchas filas
   (el caso tipico: "Encontramos 48 cargas de gasoil sin precio. ¿Que
   precio por litro debemos usar?"). Cada respuesta se puede aplicar a
   todo el lote, a una seleccion puntual o a un periodo de fechas — asi
   se pueden cargar dos precios de gasoil distintos para dos periodos
   distintos, por ejemplo.
4. Revisar el resumen (movimientos detectados, cuantos se incorporan,
   duplicados excluidos, excepciones pendientes, totales Debe/Haber,
   saldo inicial y saldo final proyectado).
5. Tocar **"Confirmar y generar cuenta"**: una sola accion aprueba en
   lote todos los movimientos validos. Los que quedaron como excepcion
   (fila ambigua, documento sin datos suficientes, posible duplicado) no
   bloquean al resto — quedan pendientes y se pueden excluir o resolver
   despues.
6. Descargar Excel o PDF.

La confirmacion es **atomica**: primero se arman todos los movimientos en
memoria, y solo si el guardado en disco tiene exito se aplican a la
cuenta — si algo falla en el medio, no queda un guardado parcial.

Un precio de gasoil confirmado se guarda como una regla de la cuenta
(valor, moneda, vigencia desde, evidencia y quien lo confirmo) y se
reutiliza automaticamente en los proximos lotes mientras siga aplicando,
sin volver a preguntarlo.

La revision movimiento por movimiento de la entrega anterior sigue
disponible, ahora como **opcion secundaria** plegada bajo "Modo avanzado:
cargar y revisar documentos uno por uno".

## Carga de documentos

Ademas del texto libre, la interfaz permite arrastrar (o elegir) varios
archivos `.xlsx`, `.pdf`, `.png`, `.jpg`/`.jpeg` a la vez:

- **Planillas Excel**: detecta encabezados habituales (Fecha, Concepto,
  Debe, Haber, Moneda, Comprobante) con variantes razonables; las filas
  claras se cargan directo como propuestas a revisar, las filas ambiguas
  (importe en Debe y Haber a la vez, fecha o moneda ilegible) quedan para
  completar a mano, y si no reconoce los encabezados pide elegir las
  columnas manualmente antes de importar.
- **Facturas PDF**: si el PDF tiene texto (no es un escaneo), extrae fecha,
  numero de comprobante, CUIT, moneda, neto, IVA y total con expresiones
  regulares (nunca un modelo que "adivine"). Si no encuentra un total
  claro, o si es un PDF escaneado sin texto, queda para completar a mano.
- **Fotos y capturas**: esta version no tiene OCR local disponible (ver
  mas abajo), asi que siempre quedan para completar los datos a mano — la
  aplicacion nunca simula haber leido algo que no pudo leer.

Todo lo que el sistema no puede determinar con certeza queda como
propuesta pendiente de revision humana; nunca se inventa un importe, una
moneda o una direccion.

### Sin OCR local

Esta maquina no tiene Tesseract, Poppler ni bindings de Vision instalados,
y no se instalo software de sistema para esta entrega. Por eso las fotos y
los PDF escaneados siempre requieren completar los datos a mano. Para
automatizar esa lectura en el futuro (con autorizacion previa) se podria
agregar `pytesseract` + el binario de Tesseract, o Poppler + `pdf2image`.

## Exportar la cuenta corriente

Con el boton **Descargar Excel** se genera un `.xlsx` con una hoja por
moneda (saldo inicial, movimientos con Debe/Haber/saldo acumulado/IVA,
saldo final confirmado y proyectado) mas una hoja aparte de propuestas
pendientes. Con **Descargar PDF** se genera un documento paginado con el
mismo contenido, listo para imprimir o enviar, con el nombre comercial y
el logo configurados en "Configuracion del informe".

## Dependencias

No se agrego ninguna dependencia nueva para el flujo de lote (reutiliza
`openpyxl`/`pypdf`/`Pillow`/`fpdf2` ya instalados). Las que ya estaban,
todas instalables con `pip` dentro del `.venv`, sin tocar nada del
sistema operativo:

| Paquete | Para que | Por que esta |
|---|---|---|
| `openpyxl` | Leer y escribir `.xlsx` | Puro Python, sin dependencias compiladas, cubre lectura y escritura con formato en una sola libreria |
| `pypdf` | Extraer texto de facturas PDF y detectar PDF escaneados | Puro Python, sucesor mantenido de PyPDF2, no requiere Poppler |
| `Pillow` | Validar imagenes (fotos y logo) | Libreria de imagen estandar, con instaladores precompilados para macOS |
| `fpdf2` | Generar el PDF de la cuenta corriente | Puro Python, liviano, soporta tablas paginadas con encabezado repetido |
| `python-multipart` | Requisito de FastAPI para recibir archivos subidos | Dependencia obligatoria de FastAPI para `UploadFile`/formularios, no opcional |

## Arranque en un solo paso

```bash
./start.sh
```

Crea el entorno virtual si falta, instala dependencias y abre
`http://127.0.0.1:8000` en el navegador. Ctrl+C para detener el servidor.

## Ejecutar las pruebas

No requiere dependencias externas en esta etapa:

```bash
.venv/bin/python -m pytest -q
```

Para iniciar la API local manualmente durante el desarrollo:

```bash
.venv/bin/uvicorn cuentas_corrientes.api:app --reload
```

Luego abrir `http://127.0.0.1:8000` para usar la interfaz. Las cuentas se
guardan localmente en `data/cuentas.sqlite3`, los documentos subidos en
`data/uploads/` y los logos en `data/logos/` — todo dentro de `data/`, que
esta excluido de Git. **Abrir el archivo HTML directamente (`file://`) no
funciona**: la aplicacion tiene que servirse por HTTP local con
`start.sh` o `uvicorn`.

## Copias de seguridad

Se crea una copia de seguridad automatica en `backups/` al iniciar la
aplicacion y despues de cada cambio guardado (con un intervalo minimo de 5
minutos entre copias automaticas). Para restaurar una copia:

```bash
.venv/bin/python scripts/restore.py --list
.venv/bin/python scripts/restore.py --file backups/cuentas-XXXXXXXX.sqlite3
```

`restore.py` guarda automaticamente una copia de la base actual antes de
sobrescribirla y verifica la integridad de la base restaurada.

## Seguridad

- No guardar claves de API en el repositorio.
- Usar `.env` solamente en la instalacion local.
- No incorporar documentacion real hasta habilitar almacenamiento privado, usuarios y copias de seguridad verificadas en produccion (ver [docs/DISENO_MULTIUSUARIO.md](docs/DISENO_MULTIUSUARIO.md)).
