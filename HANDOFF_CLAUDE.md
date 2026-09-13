# Traspaso para Claude Code — Agente de cuentas corrientes

## Objetivo inmediato

Convertir el prototipo actual en un MVP local realmente operable por una persona no tecnica. No procesar datos reales ni desplegar en Internet hasta completar los controles de seguridad indicados abajo.

## Estado comprobado (actualizado)

- Motor deterministico de movimientos, saldo inicial/confirmado/proyectado y monedas separadas.
- Ingreso inicial de texto conversacional con preguntas cuando falta informacion.
- Correccion de cualquier campo de una propuesta pendiente, con motivo obligatorio, antes de aprobarla o denegarla.
- Aprobacion humana de propuestas.
- Descarte trazable de propuestas pendientes, con motivo.
- Reversion de un movimiento confirmado mediante un contramovimiento trazable (nunca se borra el original).
- Interfaz local en `/` que cubre de punta a punta: alta de cuenta con saldo inicial opcional, carga de texto, revision y correccion de la propuesta interpretada, aprobar/denegar/revertir, y resumen completo (saldo inicial, movimientos, anticipos sin asignar, saldo confirmado, saldo proyectado, saldo final).
- Renderizado seguro en la interfaz (sin `innerHTML` con datos de usuario) y manejo de errores de red en lenguaje simple, sin dejar la pantalla en un estado inconcluso.
- Copia de seguridad local automatica (al iniciar y despues de cada guardado, con intervalo minimo) y restauracion verificada por integridad (`scripts/restore.py`).
- Arranque en un solo paso (`./start.sh`).
- Diseño (sin implementar) de usuarios con identidad, sesiones, CSRF y checklist de despliegue en `docs/DISENO_MULTIUSUARIO.md`.
- Carga de documentos por lote con preguntas consolidadas, confirmación única y regla de precio de gasoil reutilizable (ver "Entrega 3" más abajo).
- Cincuenta pruebas automatizadas aprobadas (dominio, API, backups, documentos, lotes).

## Problema anterior (resuelto en esta entrega)

La interfaz era demasiado basica y el flujo quedaba poco claro. Se reescribio para cubrir correccion, aprobacion, denegacion y reversion de punta a punta, y ahora cualquier propuesta pendiente muestra sus controles de forma persistente en la lista de movimientos (antes solo aparecian justo despues de analizar el texto). Sigue valiendo: abrir el archivo HTML directamente con `file://` no funciona; la aplicacion debe servirse por HTTP local (`./start.sh` o `uvicorn`).

## Prioridad 1: experiencia completa y simple — CUMPLIDA

Implementar y verificar en navegador un flujo de principio a fin:

1. Crear y seleccionar una cuenta.
2. Cargar texto ficticio.
3. Mostrar claramente que interpreto el sistema antes de guardarlo.
4. Permitir corregir campos de la propuesta.
5. Permitir aprobar o denegar con motivo.
6. Permitir revertir un movimiento confirmado mediante un contramovimiento trazable; nunca borrarlo.
7. Mostrar saldo inicial, movimientos, anticipos sin asignar, saldo confirmado, saldo proyectado y saldo final.
8. Mostrar errores en lenguaje sencillo y evitar que la pantalla quede en un estado inconcluso.

## Prioridad 2: confiabilidad local — CUMPLIDA

- Agregar pruebas end-to-end de la interfaz y API. → Cubierto a nivel API (flujo completo con `TestClient`); no se agrego Playwright para no sumar una dependencia pesada (decision acordada con el usuario). La interfaz se verifico manualmente en navegador.
- Manejar errores de red y respuestas inesperadas. → `request()` en la interfaz atrapa errores de red/parseo; handler global de excepciones no controladas en la API.
- Evitar HTML generado con datos sin escapar. → Reescrito para usar `createElement`/`textContent` en toda la interfaz; verificado con una carga de texto que incluia HTML/`<img onerror>` y no se ejecuto ni se inserto en el DOM.
- Agregar copia de seguridad local automática y restauracion comprobada. → `src/cuentas_corrientes/backup.py` + `scripts/restore.py`, con pruebas de round-trip.
- Agregar una orden simple de inicio; idealmente un solo comando o lanzador local. → `./start.sh`.
- No agregar dependencias sin justificar y documentar su necesidad. → No se agrego ninguna dependencia nueva en esta entrega.

## Entrega 2 — CUMPLIDA: carga de documentos, revision asistida y exportacion

Sobre la base anterior (intacta y sin romper ninguna prueba), se agrego:

- Carga de `.xlsx`, `.pdf`, `.png`, `.jpg`/`.jpeg` por arrastre o seleccion, con progreso y resultado por archivo, reintento y opcion de quitar.
- Clasificacion automatica: planilla, factura PDF, foto/captura, no reconocido o invalido — validando extension y contenido real (no el nombre declarado).
- Planillas: deteccion de encabezados con variantes razonables, filas claras cargadas directo como propuestas, filas ambiguas o encabezados no reconocidos quedan para revisar/mapear a mano.
- Facturas PDF: extraccion determinista por expresiones regulares (fecha, comprobante, CUIT, moneda, neto, IVA, total); PDF escaneado sin texto queda para completar a mano.
- Fotos/capturas: sin OCR local disponible en esta maquina (se verifico que no hay Tesseract/Poppler/Vision instalados) — quedan siempre para completar a mano, documentado en `README.md`.
- Cola de revision unificada (`/documents` + movimientos en `review`), con formulario para completar campos manualmente y aprobacion en lote de las propuestas validas.
- Configuracion de informe (nombre comercial + logo, guardados fuera de Git en `data/logos/`).
- Exportacion a Excel (`openpyxl`, hoja por moneda + hoja de pendientes) y PDF (`fpdf2`, paginado con encabezado repetido y numeracion), reutilizando el motor deterministico existente para todos los saldos.
- Seguridad de archivos: limites de tamaño, verificacion de contenido, nombre interno aleatorio, proteccion de path traversal, sin exponer rutas locales.
- Se corrigio durante la verificacion manual un error real: los directorios `uploads/`/`logos/` se estaban creando en la raiz del repositorio en vez de dentro de `data/`, fuera del alcance de `.gitignore`. Ya esta corregido (ver `api.py`) y se agregaron ambas carpetas al `.gitignore` como respaldo adicional.
- Veintidos pruebas nuevas (cuarenta y una en total) cubriendo Excel con variantes, PDF con y sin texto, imagenes, archivos corruptos/con extension falsa/con nombre malicioso/demasiado grandes, resolucion manual, mapeo de columnas, aprobacion en lote, exportacion y persistencia.

## Entrega 3 — CUMPLIDA: lote con confirmacion unica

Motivo: cargar un Excel con muchas cargas de gasoil obligaba a aprobar cada
propuesta una por una — inutilizable en la practica. Se agrego:

- Nuevo modulo `batches.py`: procesa todos los archivos de una carga como
  un unico lote, agrupa las preguntas que se repiten en muchas filas (el
  caso central: precio de gasoil sin definir) en una sola pregunta
  consolidada por tipo, y permite responder aplicando la respuesta a todo
  el lote, a una seleccion puntual o a un periodo de fechas.
- Regla de cuenta reutilizable (`AccountRule`) para el precio de gasoil:
  valor, moneda, vigencia, evidencia y quien la confirmo; se reutiliza
  automaticamente en lotes futuros mientras siga aplicando.
- Resumen previo a confirmar (detectados, listos, duplicados excluidos,
  excepciones pendientes, totales Debe/Haber, saldo inicial y proyectado)
  y una unica accion "Confirmar y generar cuenta" que aprueba en lote
  todos los movimientos validos sin bloquear por las excepciones.
- Confirmacion atomica: se arman todos los movimientos en memoria antes de
  tocar la cuenta; si el guardado falla, no queda nada a medias.
- Interfaz principal reducida a: elegir cuenta → cargar todo → responder
  preguntas → revisar resumen → confirmar → descargar. La revision
  individual de la entrega anterior sigue existiendo, plegada como "Modo
  avanzado".
- El motor determinístico (`domain.py`, `rules.py`) no se tocó, salvo un
  método de solo lectura (`Account.has_source_hash`) para detectar
  duplicados desde la nueva capa sin exponer el set privado existente.

## Prioridad 3: preparacion para dos usuarios — SOLO DISEÑO (acordado con el usuario)

Diseñar, pero no desplegar todavia. Ver [docs/DISENO_MULTIUSUARIO.md](docs/DISENO_MULTIUSUARIO.md) para el diseño completo; no se escribio codigo de autenticacion en esta entrega.

- Usuario del responsable administrativo y usuario del contador.
- Iguales permisos funcionales.
- Identidad individual en cada alta, correccion, aprobacion o descarte.
- Sesiones seguras, contraseñas con hash y proteccion CSRF.
- Base y documentos fuera de GitHub.
- HTTPS, secretos externos al repositorio y copias de seguridad antes de publicar un enlace.

## Reglas comerciales ya aprobadas

- El sistema debe servir para cualquier cuenta corriente, no solo fletes.
- Una factura prevalece para peso, tarifa e IVA cuando existe.
- Sin factura no se calcula IVA.
- Los fletes sin factura pueden usar toneladas manuales aprobadas o bruto menos tara de una captura.
- Los anticipos de gasoil usan el precio de la ultima factura de gasoil pagada por la empresa; si falta, preguntar y no calcular.
- Un anticipo sin referencia especifica queda como anticipo general no asignado.
- No distribuir anticipos automaticamente entre facturas antiguas.
- Toda ambiguedad material requiere revision humana.
- La IA puede extraer y proponer; el motor deterministico calcula el saldo oficial.

## Seguridad obligatoria

- No leer ni incorporar datos reales durante el desarrollo inicial.
- No subir bases SQLite, `.env`, documentos, conversaciones ni credenciales.
- No pedir contraseñas, tokens, claves API, codigos SMS ni claves privadas por chat.
- No crear servicios externos, instalar software o hacer push sin autorizacion especifica del usuario.
- Mantener el repositorio privado.

## Criterio de terminado para la proxima entrega

El usuario debe poder abrir la aplicacion con un procedimiento corto, completar un caso ficticio entero sin ayuda tecnica, aprobar/denegar/corregir/revertir y cerrar/reabrir sin perder datos. Todas las pruebas deben pasar y debe entregarse un resumen de archivos cambiados, riesgos pendientes y pasos exactos para probar.
